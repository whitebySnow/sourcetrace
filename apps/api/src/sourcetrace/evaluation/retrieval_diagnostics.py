"""Sanitized diagnostics for retrieval failures in a recorded evaluation report."""

from sourcetrace.evaluation.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationReport,
    ExpectedEvidenceDiagnostic,
    ExpectedEvidenceMatchStatus,
    RetrievalCaseDiagnostic,
    RetrievalDiagnosticsReport,
    RetrievalFailureMechanism,
)


def build_retrieval_diagnostics(
    dataset: EvaluationDataset,
    report: EvaluationReport,
    *,
    report_sha256: str,
) -> RetrievalDiagnosticsReport:
    """Describe failed retrievals without copying candidate document text."""
    if (report.dataset_id, report.dataset_version) != (
        dataset.dataset_id,
        dataset.dataset_version,
    ):
        raise ValueError("evaluation report does not belong to the supplied dataset")

    cases_by_id = {case.id: case for case in dataset.cases}
    diagnostics: list[RetrievalCaseDiagnostic] = []
    for result in report.cases:
        if result.retrieval != "failed":
            continue
        case = cases_by_id.get(result.case_id)
        if case is None:
            raise ValueError("evaluation report contains a case outside the supplied dataset")
        trace = result.observation.decision_trace
        retrievals = trace.retrievals if trace is not None else ()
        expected = tuple(
            ExpectedEvidenceDiagnostic(
                document_version_id=reference.document_version_id,
                page_number=reference.page_number,
                match_status=_match_status(reference, result.observation.retrieved_evidence),
            )
            for reference in case.expected.evidence
        )
        diagnostics.append(
            RetrievalCaseDiagnostic(
                case_id=case.id,
                primary_mechanism=_primary_mechanism(case, retrievals, expected),
                retrievals=retrievals,
                expected_evidence=expected,
            )
        )
    return RetrievalDiagnosticsReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        report_sha256=report_sha256,
        cases=tuple(diagnostics),
    )


def _match_status(reference, observed) -> ExpectedEvidenceMatchStatus:  # type: ignore[no-untyped-def]
    if any(
        item.document_version_id == reference.document_version_id
        and item.page_number == reference.page_number
        and reference.text.strip() in item.text.strip()
        for item in observed
    ):
        return "matched"
    if any(
        item.document_version_id == reference.document_version_id
        and item.page_number == reference.page_number
        for item in observed
    ):
        return "same_page_different_chunk"
    return "not_retrieved"


def _primary_mechanism(
    case: EvaluationCase,
    retrievals,
    expected: tuple[ExpectedEvidenceDiagnostic, ...],
) -> RetrievalFailureMechanism:  # type: ignore[no-untyped-def]
    if retrievals and retrievals[0].query != case.question:
        return "query_rewrite_drift"
    if expected and all(
        item.match_status in {"matched", "same_page_different_chunk"} for item in expected
    ):
        return "chunk_boundary_mismatch"
    return "embedding_retrieval_weakness"
