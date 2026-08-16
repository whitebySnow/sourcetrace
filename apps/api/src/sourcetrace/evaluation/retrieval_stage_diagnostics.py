"""Sanitized stage diagnostics for replayed retrieval failures."""

from hashlib import sha256

from sourcetrace.evaluation.models import (
    EvaluationDataset,
    EvaluationReport,
    HybridCandidateTrace,
    HybridChannelCandidateTrace,
    HybridRetrievalEvaluationReport,
    RetrievalStageCaseDiagnostic,
    RetrievalStageClaimDiagnostic,
    RetrievalStageDiagnosticsReport,
    RetrievalStageDiagnosticsSummary,
    RetrievalStageFailureMechanism,
    RetrievalStageFusedHit,
    RetrievalStageRankedHit,
    SanitizedEvidenceLocation,
)


def build_retrieval_stage_diagnostics(
    dataset: EvaluationDataset,
    source_report: EvaluationReport,
    stage_report: HybridRetrievalEvaluationReport,
    *,
    dataset_sha256: str,
    source_report_sha256: str,
    stage_report_sha256: str,
) -> RetrievalStageDiagnosticsReport:
    """Classify the earliest stage that lost each expected evidence claim."""
    _validate_sources(dataset, source_report, stage_report, dataset_sha256)
    cases_by_id = {case.id: case for case in dataset.cases}
    stage_cases_by_id = {case.case_id: case for case in stage_report.cases}
    diagnostics: list[RetrievalStageCaseDiagnostic] = []
    for result in source_report.cases:
        case = cases_by_id.get(result.case_id)
        if case is None:
            raise ValueError("source report contains a case outside the supplied dataset")
        if result.retrieval != "failed" or case.expected.outcome != "answered":
            continue
        stage_case = stage_cases_by_id.get(case.id)
        if stage_case is None:
            raise ValueError("stage report is missing a failed answerable case")
        trace = result.observation.decision_trace
        if trace is None or not trace.retrieval_rounds:
            raise ValueError("source report is missing replayable retrieval rounds")
        source_queries = trace.retrieval_rounds[-1].queries
        traced_queries = tuple(item.query for item in stage_case.query_traces)
        if stage_case.queries != source_queries or traced_queries != source_queries:
            raise ValueError("stage report queries do not match the source report")
        claims = tuple(
            _claim_diagnostic(
                reference,
                index,
                stage_case.query_traces,
                stage_case.expanded_candidates,
            )
            for index, reference in enumerate(case.expected.evidence, start=1)
        )
        mechanisms = {claim.earliest_loss_stage for claim in claims}
        primary: RetrievalStageFailureMechanism = (
            next(iter(mechanisms)) if len(mechanisms) == 1 else "mixed"
        )
        diagnostics.append(
            RetrievalStageCaseDiagnostic(
                case_id=case.id,
                primary_mechanism=primary,
                claims=claims,
            )
        )
    counts = {
        mechanism: sum(item.primary_mechanism == mechanism for item in diagnostics)
        for mechanism in (
            "channel_recall",
            "channel_fusion",
            "primary_selection",
            "page_expansion",
            "minimum_score",
            "replay_did_not_reproduce",
            "mixed",
        )
    }
    return RetrievalStageDiagnosticsReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        dataset_sha256=dataset_sha256,
        source_report_sha256=source_report_sha256,
        stage_report_sha256=stage_report_sha256,
        source_metadata=source_report.metadata,
        stage_metadata=stage_report.metadata,
        summary=RetrievalStageDiagnosticsSummary(
            failed_answerable_cases=len(diagnostics),
            **counts,
        ),
        cases=tuple(diagnostics),
    )


def _validate_sources(
    dataset: EvaluationDataset,
    source_report: EvaluationReport,
    stage_report: HybridRetrievalEvaluationReport,
    dataset_sha256: str,
) -> None:
    identity = (dataset.dataset_id, dataset.dataset_version)
    if (source_report.dataset_id, source_report.dataset_version) != identity:
        raise ValueError("source report does not belong to the supplied dataset")
    if (stage_report.dataset_id, stage_report.dataset_version) != identity:
        raise ValueError("stage report does not belong to the supplied dataset")
    if stage_report.metadata.dataset_sha256 != dataset_sha256.lower():
        raise ValueError("stage report is not bound to the supplied dataset bytes")


def _claim_diagnostic(  # type: ignore[no-untyped-def]
    reference,
    index: int,
    query_traces,
    expanded_candidates,
) -> RetrievalStageClaimDiagnostic:
    claim_id = reference.claim_id or f"evidence-{index}"
    dense_hits = tuple(
        dense_hit
        for query in query_traces
        for candidate in query.dense_candidates
        if (dense_hit := _ranked_hit(query.query, candidate, claim_id)) is not None
    )
    lexical_hits = tuple(
        lexical_hit
        for query in query_traces
        for candidate in query.lexical_candidates
        if (lexical_hit := _ranked_hit(query.query, candidate, claim_id)) is not None
    )
    fused_hits = tuple(
        fused_hit
        for query in query_traces
        for candidate in query.candidates
        if (fused_hit := _fused_hit(query.query, candidate, claim_id)) is not None
    )
    primary_ids = tuple(
        item.chunk_id for item in fused_hits if item.selected_as_primary
    )
    expanded_ids = tuple(
        item.chunk_id
        for item in expanded_candidates
        if _match_status(item, claim_id) is not None
    )
    final_ids = tuple(
        item.chunk_id
        for item in expanded_candidates
        if item.passed_minimum_score and _match_status(item, claim_id) is not None
    )
    mechanism: RetrievalStageFailureMechanism
    if final_ids:
        mechanism = "replay_did_not_reproduce"
    elif expanded_ids:
        mechanism = "minimum_score"
    elif primary_ids:
        mechanism = "page_expansion"
    elif fused_hits:
        mechanism = "primary_selection"
    elif dense_hits or lexical_hits:
        mechanism = "channel_fusion"
    else:
        mechanism = "channel_recall"
    return RetrievalStageClaimDiagnostic(
        claim_id=claim_id,
        expected=SanitizedEvidenceLocation(
            document_version_id=reference.document_version_id,
            page_number=reference.page_number,
        ),
        dense_hits=dense_hits,
        lexical_hits=lexical_hits,
        channel_fusion_hits=fused_hits,
        expanded_chunk_ids=expanded_ids,
        final_chunk_ids=final_ids,
        earliest_loss_stage=mechanism,
    )


def _ranked_hit(
    query: str,
    candidate: HybridChannelCandidateTrace,
    claim_id: str,
) -> RetrievalStageRankedHit | None:
    status = _match_status(candidate, claim_id)
    if status is None:
        return None
    return RetrievalStageRankedHit(
        query_sha256=sha256(query.encode("utf-8")).hexdigest(),
        chunk_id=candidate.chunk_id,
        rank=candidate.rank,
        match_status=status,
    )


def _fused_hit(
    query: str,
    candidate: HybridCandidateTrace,
    claim_id: str,
) -> RetrievalStageFusedHit | None:
    status = _match_status(candidate, claim_id)
    if status is None:
        return None
    return RetrievalStageFusedHit(
        query_sha256=sha256(query.encode("utf-8")).hexdigest(),
        chunk_id=candidate.chunk_id,
        channel_fused_rank=candidate.channel_fused_rank,
        reranked_rank=candidate.reranked_rank,
        selected_for_query_coverage=candidate.selected_for_query_coverage,
        selected_as_primary=candidate.selected_as_primary,
        match_status=status,
    )


def _match_status(candidate, claim_id: str):  # type: ignore[no-untyped-def]
    if claim_id in candidate.canonical_claim_ids:
        return "canonical"
    if claim_id in candidate.approved_alternative_claim_ids:
        return "approved_alternative"
    return None
