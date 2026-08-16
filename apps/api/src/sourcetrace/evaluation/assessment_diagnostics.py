"""Sanitized diagnostics for evidence-stage refusals in an evaluation report."""

from collections.abc import Mapping, Sequence
from hashlib import sha256
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
    EvidenceAssessmentRetrievalRoundDiagnostic,
    EvidenceAssessmentRoundDiagnostic,
    ObservedEvidence,
    ObservedEvidenceAssessment,
    ObservedRetrieval,
    ObservedRetrievalRoundTrace,
    SanitizedEvidenceChunk,
    SanitizedEvidenceLocation,
    SanitizedRetrievalQueryDiagnostic,
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
        candidate_chunks_by_id = _candidate_chunks_by_id(
            trace.retrievals,
            result.observation.retrieved_evidence,
        )
        previous_selection: set[UUID] = set()
        rounds: list[EvidenceAssessmentRoundDiagnostic] = []
        for index, assessment in enumerate(trace.assessments, start=1):
            diagnostic = _round_diagnostic(
                index,
                assessment,
                candidate_chunks_by_id,
                previous_selection=previous_selection,
            )
            rounds.append(diagnostic)
            previous_selection = {chunk.chunk_id for chunk in diagnostic.selected_chunks}
        final_locations = {
            (item.document_version_id, item.page_number)
            for item in rounds[-1].selected_source_pages
        }
        matches = match_evidence_claims(
            case.expected.evidence,
            result.observation.retrieved_evidence,
        )
        if any(match.match_status == "not_matched" for match in matches):
            raise ValueError(
                "retrieval passed but recomputed evidence claim matching failed"
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
                retrieval_plan_version=trace.retrieval_plan_version,
                candidate_sources=tuple(candidate_chunks_by_id.values()),
                retrieval_rounds=tuple(
                    _retrieval_round_diagnostic(round_trace)
                    for round_trace in trace.retrieval_rounds
                ),
                assessment_rounds=tuple(rounds),
                supplemental_retrieval_attempts=trace.supplemental_retrieval_attempts,
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


def _candidate_chunks_by_id(
    retrievals: Sequence[ObservedRetrieval],
    final_evidence: Sequence[ObservedEvidence],
) -> dict[UUID, SanitizedEvidenceChunk]:
    chunks_by_id: dict[UUID, SanitizedEvidenceChunk] = {}

    def remember(chunk: SanitizedEvidenceChunk) -> None:
        previous = chunks_by_id.setdefault(chunk.chunk_id, chunk)
        if previous != chunk:
            raise ValueError("retrieval trace maps one chunk to multiple source locations")

    for retrieval in retrievals:
        for candidate in retrieval.candidates:
            remember(
                SanitizedEvidenceChunk(
                    chunk_id=candidate.chunk_id,
                    document_version_id=candidate.document_version_id,
                    page_number=candidate.page_number,
                )
            )
    for evidence in final_evidence:
        if evidence.chunk_id is not None:
            remember(
                SanitizedEvidenceChunk(
                    chunk_id=evidence.chunk_id,
                    document_version_id=evidence.document_version_id,
                    page_number=evidence.page_number,
                )
            )
    return chunks_by_id


def _retrieval_round_diagnostic(
    retrieval_round: ObservedRetrievalRoundTrace,
) -> EvidenceAssessmentRetrievalRoundDiagnostic:
    queries: list[SanitizedRetrievalQueryDiagnostic] = []
    results_by_query = {result.query: result for result in retrieval_round.query_results}
    if len(results_by_query) != len(retrieval_round.query_results):
        raise ValueError("retrieval round contains duplicate query results")
    if not set(results_by_query) <= set(retrieval_round.queries):
        raise ValueError("retrieval round result does not belong to its declared queries")
    for query in retrieval_round.queries:
        query_result = results_by_query.get(query)
        candidates = query_result.candidates if query_result is not None else ()
        candidate_ids = tuple(candidate.chunk_id for candidate in candidates)
        queries.append(
            SanitizedRetrievalQueryDiagnostic(
                query_sha256=_query_sha256(query),
                candidate_chunk_ids=candidate_ids,
                query_coverage_chunk_ids=tuple(
                    candidate.chunk_id
                    for candidate in candidates
                    if candidate.selected_for_query_coverage
                ),
            )
        )
    return EvidenceAssessmentRetrievalRoundDiagnostic(
        round_number=retrieval_round.round_number,
        queries=tuple(queries),
        final_evidence_chunk_ids=retrieval_round.final_evidence_chunk_ids,
    )


def _round_diagnostic(
    round_number: int,
    assessment: ObservedEvidenceAssessment,
    candidate_chunks_by_id: Mapping[UUID, SanitizedEvidenceChunk],
    *,
    previous_selection: set[UUID],
) -> EvidenceAssessmentRoundDiagnostic:
    selected_ids = tuple(UUID(item) for item in assessment.selected_chunk_ids)
    _require_known_chunks(selected_ids, candidate_chunks_by_id)
    selected_chunks = tuple(candidate_chunks_by_id[chunk_id] for chunk_id in selected_ids)
    selected_locations: list[SanitizedEvidenceLocation] = []
    seen: set[tuple[UUID, int]] = set()
    for location in selected_chunks:
        key = (location.document_version_id, location.page_number)
        if key in seen:
            continue
        seen.add(key)
        selected_locations.append(location)
    return EvidenceAssessmentRoundDiagnostic(
        round_number=round_number,
        sufficient=assessment.sufficient,
        selected_chunk_count=len(selected_chunks),
        selected_chunks=selected_chunks,
        preserved_selection_chunk_ids=tuple(
            chunk_id for chunk_id in selected_ids if chunk_id in previous_selection
        ),
        selected_source_pages=tuple(selected_locations),
        supplemental_query_count=len(assessment.supplemental_queries),
        supplemental_query_sha256=tuple(
            _query_sha256(query) for query in assessment.supplemental_queries
        ),
    )


def _require_known_chunks(
    chunk_ids: Sequence[UUID],
    candidate_chunks_by_id: Mapping[UUID, SanitizedEvidenceChunk],
) -> None:
    if any(chunk_id not in candidate_chunks_by_id for chunk_id in chunk_ids):
        raise ValueError("diagnostic trace references a chunk absent from retrieval candidates")


def _query_sha256(query: str) -> str:
    return sha256(query.encode("utf-8")).hexdigest()


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
