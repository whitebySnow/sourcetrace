"""Sanitized diagnostics for evidence-stage refusals in an evaluation report."""

from collections.abc import Mapping, Sequence
from typing import Literal
from uuid import UUID

from sourcetrace.evaluation.evidence_matching import match_evidence_claims
from sourcetrace.evaluation.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationDecisionTrace,
    EvaluationReport,
    EvaluationStatus,
    EvidenceAssessmentCaseDiagnostic,
    EvidenceAssessmentClaimDiagnostic,
    EvidenceAssessmentDiagnosticsReport,
    EvidenceAssessmentDiagnosticsSummary,
    EvidenceAssessmentFailureMechanism,
    EvidenceAssessmentRoundDiagnostic,
    ObservedEvidenceAssessment,
    ObservedRetrieval,
    SanitizedEvidenceLocation,
)


def build_evidence_assessment_diagnostics(
    dataset: EvaluationDataset,
    report: EvaluationReport,
    *,
    report_sha256: str,
) -> EvidenceAssessmentDiagnosticsReport:
    """Classify answerable refusals without retaining questions or evidence text."""
    if (report.dataset_id, report.dataset_version) != (
        dataset.dataset_id,
        dataset.dataset_version,
    ):
        raise ValueError("evaluation report does not belong to the supplied dataset")

    cases_by_id = {case.id: case for case in dataset.cases}
    diagnostics: list[EvidenceAssessmentCaseDiagnostic] = []
    for result in report.cases:
        case = cases_by_id.get(result.case_id)
        if case is None:
            raise ValueError("evaluation report contains a case outside the supplied dataset")
        trace = result.observation.decision_trace
        if not _is_evidence_stage_refusal(
            case,
            result.retrieval,
            result.observation.outcome,
            trace,
        ):
            continue
        assert trace is not None
        locations = _candidate_locations(trace.retrievals)
        rounds = tuple(
            _round_diagnostic(index, assessment, locations)
            for index, assessment in enumerate(trace.assessments, start=1)
        )
        final_locations = {
            (item.document_version_id, item.page_number)
            for item in rounds[-1].selected_source_pages
        }
        matches = match_evidence_claims(
            case.expected.evidence,
            result.observation.retrieved_evidence,
        )
        claims = tuple(
            EvidenceAssessmentClaimDiagnostic(
                claim_id=match.claim_id,
                expected=SanitizedEvidenceLocation(
                    document_version_id=reference.document_version_id,
                    page_number=reference.page_number,
                ),
                retrieval_match_status=match.match_status,
                selected_source_page=any(
                    (passage.document_version_id, passage.page_number) in final_locations
                    for passage in (reference, *reference.approved_alternatives)
                ),
            )
            for reference, match in zip(case.expected.evidence, matches, strict=True)
        )
        diagnostics.append(
            EvidenceAssessmentCaseDiagnostic(
                case_id=case.id,
                primary_mechanism=_primary_mechanism(claims, rounds[-1]),
                claims=claims,
                assessment_rounds=rounds,
            )
        )

    return EvidenceAssessmentDiagnosticsReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        report_sha256=report_sha256,
        source_metadata=report.metadata,
        summary=_summarize(diagnostics),
        cases=tuple(diagnostics),
    )


def _is_evidence_stage_refusal(
    case: EvaluationCase,
    retrieval_status: EvaluationStatus,
    outcome: Literal["answered", "refused", "error"],
    trace: EvaluationDecisionTrace | None,
) -> bool:
    if (
        case.expected.outcome != "answered"
        or retrieval_status != "passed"
        or outcome != "refused"
        or trace is None
    ):
        return False
    assessments = trace.assessments
    citation_validations = trace.citation_validations
    return bool(assessments and not assessments[-1].sufficient and not citation_validations)


def _candidate_locations(
    retrievals: Sequence[ObservedRetrieval],
) -> dict[str, SanitizedEvidenceLocation]:
    locations: dict[str, SanitizedEvidenceLocation] = {}
    for retrieval in retrievals:
        for candidate in retrieval.candidates:
            locations[str(candidate.chunk_id)] = SanitizedEvidenceLocation(
                document_version_id=candidate.document_version_id,
                page_number=candidate.page_number,
            )
    return locations


def _round_diagnostic(
    round_number: int,
    assessment: ObservedEvidenceAssessment,
    locations: Mapping[str, SanitizedEvidenceLocation],
) -> EvidenceAssessmentRoundDiagnostic:
    unknown = [item for item in assessment.selected_chunk_ids if item not in locations]
    if unknown:
        raise ValueError("assessment selected a chunk absent from the retrieval trace")
    selected_locations: list[SanitizedEvidenceLocation] = []
    seen: set[tuple[UUID, int]] = set()
    for chunk_id in assessment.selected_chunk_ids:
        location = locations[chunk_id]
        key = (location.document_version_id, location.page_number)
        if key in seen:
            continue
        seen.add(key)
        selected_locations.append(location)
    return EvidenceAssessmentRoundDiagnostic(
        round_number=round_number,
        sufficient=assessment.sufficient,
        selected_chunk_count=len(assessment.selected_chunk_ids),
        selected_source_pages=tuple(selected_locations),
        supplemental_query_count=len(assessment.supplemental_queries),
    )


def _primary_mechanism(
    claims: Sequence[EvidenceAssessmentClaimDiagnostic],
    final_round: EvidenceAssessmentRoundDiagnostic,
) -> EvidenceAssessmentFailureMechanism:
    if final_round.selected_chunk_count == 0:
        return "no_evidence_selected"
    if any(not claim.selected_source_page for claim in claims):
        return "expected_source_pages_not_selected"
    return "expected_source_pages_selected_but_insufficient"


def _summarize(
    cases: Sequence[EvidenceAssessmentCaseDiagnostic],
) -> EvidenceAssessmentDiagnosticsSummary:
    mechanisms = [case.primary_mechanism for case in cases]
    return EvidenceAssessmentDiagnosticsSummary(
        failed_answerable_refusals=len(cases),
        no_evidence_selected=mechanisms.count("no_evidence_selected"),
        expected_source_pages_not_selected=mechanisms.count("expected_source_pages_not_selected"),
        expected_source_pages_selected_but_insufficient=mechanisms.count(
            "expected_source_pages_selected_but_insufficient"
        ),
    )
