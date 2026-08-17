"""Sanitized diagnostics for citation failures in an evaluation report."""

from collections.abc import Sequence

from sourcetrace.evaluation.evidence_matching import match_evidence_claims
from sourcetrace.evaluation.models import (
    CitationCaseDiagnostic,
    CitationClaimDiagnostic,
    CitationDiagnosticsReport,
    CitationDiagnosticsSummary,
    CitationFailureMechanism,
    CitationObservationStatus,
    EvaluationDataset,
    EvaluationReport,
    EvidenceReference,
    ObservedEvidence,
    SanitizedEvidenceLocation,
)


def build_citation_diagnostics(
    dataset: EvaluationDataset,
    report: EvaluationReport,
    *,
    report_sha256: str,
) -> CitationDiagnosticsReport:
    if (report.dataset_id, report.dataset_version) != (
        dataset.dataset_id,
        dataset.dataset_version,
    ):
        raise ValueError("evaluation report does not belong to the supplied dataset")

    cases_by_id = {case.id: case for case in dataset.cases}
    diagnostics: list[CitationCaseDiagnostic] = []
    for result in report.cases:
        if (
            result.retrieval != "passed"
            or result.citation != "failed"
            or result.observation.outcome != "answered"
        ):
            continue
        case = cases_by_id.get(result.case_id)
        if case is None:
            raise ValueError("evaluation report contains a case outside the supplied dataset")
        claims = tuple(
            CitationClaimDiagnostic(
                claim_id=reference.claim_id or f"evidence-{index}",
                expected=SanitizedEvidenceLocation(
                    document_version_id=reference.document_version_id,
                    page_number=reference.page_number,
                ),
                retrieval_status=_observation_status(
                    reference,
                    result.observation.retrieved_evidence,
                ),
                citation_status=_observation_status(
                    reference,
                    result.observation.citations,
                ),
            )
            for index, reference in enumerate(case.expected.evidence, start=1)
        )
        diagnostics.append(
            CitationCaseDiagnostic(
                case_id=case.id,
                primary_mechanism=_primary_mechanism(claims),
                claims=claims,
                cited_passages=tuple(
                    SanitizedEvidenceLocation(
                        document_version_id=item.document_version_id,
                        page_number=item.page_number,
                    )
                    for item in result.observation.citations
                ),
            )
        )
    return CitationDiagnosticsReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        report_sha256=report_sha256,
        source_metadata=report.metadata,
        summary=_summarize(diagnostics),
        cases=tuple(diagnostics),
    )


def _observation_status(
    reference: EvidenceReference,
    observed: Sequence[ObservedEvidence],
) -> CitationObservationStatus:
    match = match_evidence_claims((reference,), observed)[0]
    if match.match_status != "not_matched":
        return match.match_status
    accepted = (reference, *reference.approved_alternatives)
    if any(
        actual.document_version_id == passage.document_version_id
        and actual.page_number == passage.page_number
        for actual in observed
        for passage in accepted
    ):
        return "same_page_different_chunk"
    return "not_observed"


def _primary_mechanism(
    claims: tuple[CitationClaimDiagnostic, ...],
) -> CitationFailureMechanism:
    citation_successes = sum(
        item.citation_status in {"canonical", "approved_alternative"}
        for item in claims
    )
    if 0 < citation_successes < len(claims):
        return "partial_claim_coverage"
    if any(item.retrieval_status == "not_observed" for item in claims):
        return "expected_evidence_not_retrieved"
    if any(
        "same_page_different_chunk"
        in {item.retrieval_status, item.citation_status}
        for item in claims
    ):
        return "same_page_different_chunk"
    return "retrieved_but_not_cited"


def _summarize(
    cases: Sequence[CitationCaseDiagnostic],
) -> CitationDiagnosticsSummary:
    mechanisms = [item.primary_mechanism for item in cases]
    return CitationDiagnosticsSummary(
        failed_answered_cases=len(cases),
        expected_evidence_not_retrieved=mechanisms.count(
            "expected_evidence_not_retrieved"
        ),
        retrieved_but_not_cited=mechanisms.count("retrieved_but_not_cited"),
        same_page_different_chunk=mechanisms.count("same_page_different_chunk"),
        partial_claim_coverage=mechanisms.count("partial_claim_coverage"),
    )
