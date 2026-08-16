from hashlib import sha256
from uuid import UUID

import pytest

from sourcetrace.evaluation.models import (
    CaseEvaluationResult,
    DatasetReview,
    EvaluationCase,
    EvaluationDataset,
    EvaluationDecisionTrace,
    EvaluationObservation,
    EvaluationReport,
    EvaluationRunMetadata,
    EvaluationSummary,
    HybridCandidateTrace,
    HybridChannelCandidateTrace,
    HybridQueryTrace,
    HybridRetrievalCaseResult,
    HybridRetrievalEvaluationReport,
    HybridRetrievalRunMetadata,
    HybridRetrievalSummary,
    ObservedRetrievalRoundTrace,
)
from sourcetrace.evaluation.retrieval_stage_diagnostics import (
    build_retrieval_stage_diagnostics,
)

VERSION_ID = UUID("10000000-0000-0000-0000-000000000001")
CHUNK_ID = UUID("20000000-0000-0000-0000-000000000001")


def test_diagnostics_identify_expected_evidence_discarded_before_primary_selection() -> None:
    dataset = _dataset()
    source_report = _source_report(dataset)
    stage_report = _stage_report(dataset)

    diagnostics = build_retrieval_stage_diagnostics(
        dataset,
        source_report,
        stage_report,
        dataset_sha256=sha256(b"dataset").hexdigest(),
        source_report_sha256=sha256(b"source").hexdigest(),
        stage_report_sha256=sha256(b"stage").hexdigest(),
    )

    assert diagnostics.summary.failed_answerable_cases == 1
    assert diagnostics.summary.primary_selection == 1
    case = diagnostics.cases[0]
    assert case.case_id == "ARF-stage"
    assert case.primary_mechanism == "primary_selection"
    claim = case.claims[0]
    assert claim.claim_id == "support-claim"
    assert claim.earliest_loss_stage == "primary_selection"
    assert claim.dense_hits == ()
    assert claim.lexical_hits[0].chunk_id == CHUNK_ID
    assert claim.channel_fusion_hits[0].reranked_rank == 3
    assert claim.channel_fusion_hits[0].selected_for_query_coverage is False
    assert claim.channel_fusion_hits[0].selected_as_primary is False
    assert claim.expanded_chunk_ids == ()
    assert claim.final_chunk_ids == ()
    payload = diagnostics.model_dump_json()
    assert "Exact expected evidence." not in payload
    assert "targeted lexical query" not in payload


def test_diagnostics_reject_stage_replay_with_different_queries() -> None:
    dataset = _dataset()
    stage_report = _stage_report(dataset)
    mismatched = stage_report.model_copy(
        update={
            "cases": (
                stage_report.cases[0].model_copy(
                    update={"queries": ("Where is the exact evidence?", "different query")}
                ),
            )
        }
    )

    with pytest.raises(ValueError, match="queries do not match"):
        build_retrieval_stage_diagnostics(
            dataset,
            _source_report(dataset),
            mismatched,
            dataset_sha256=sha256(b"dataset").hexdigest(),
            source_report_sha256=sha256(b"source").hexdigest(),
            stage_report_sha256=sha256(b"stage").hexdigest(),
        )


def _dataset() -> EvaluationDataset:
    case = EvaluationCase.model_validate(
        {
            "id": "ARF-stage",
            "category": "direct",
            "question": "Where is the exact evidence?",
            "expected": {
                "outcome": "answered",
                "reference_answer": "The answer is supported.",
                "evidence": [
                    {
                        "claim_id": "support-claim",
                        "document_version_id": str(VERSION_ID),
                        "page_number": 7,
                        "text": "Exact expected evidence.",
                    }
                ],
            },
        }
    )
    return EvaluationDataset(
        schema_version="1",
        dataset_id="stage-fixture",
        dataset_version="1.0.0",
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        document_version_ids=[VERSION_ID],
        review=DatasetReview(status="fixture"),
        cases=[case],
    )


def _source_report(dataset: EvaluationDataset) -> EvaluationReport:
    failed = EvaluationSummary(passed=0, failed=1, pending_review=0, not_applicable=0)
    return EvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        knowledge_base_id=dataset.knowledge_base_id,
        document_version_ids=dataset.document_version_ids,
        metadata=_source_metadata(),
        cases=[
            CaseEvaluationResult(
                case_id="ARF-stage",
                retrieval="failed",
                citation="failed",
                refusal="not_applicable",
                end_to_end="failed",
                observation=EvaluationObservation(
                    outcome="answered",
                    answer="Answer [citation]",
                    retrieved_evidence=(),
                    citations=(),
                    decision_trace=EvaluationDecisionTrace(
                        retrievals=(),
                        retrieval_rounds=(
                            ObservedRetrievalRoundTrace(
                                round_number=1,
                                queries=(
                                    "Where is the exact evidence?",
                                    "targeted lexical query",
                                ),
                                query_results=(),
                                fused_candidates=(),
                                final_evidence_chunk_ids=(),
                                rrf_rank_constant=60,
                            ),
                        ),
                        assessments=(),
                        citation_validations=(),
                        supplemental_retrieval_attempts=0,
                        citation_repair_attempts=0,
                    ),
                ),
            )
        ],
        retrieval_summary=failed,
        citation_summary=failed,
        refusal_summary=EvaluationSummary(
            passed=0,
            failed=0,
            pending_review=0,
            not_applicable=1,
        ),
        end_to_end_summary=failed,
    )


def _stage_report(dataset: EvaluationDataset) -> HybridRetrievalEvaluationReport:
    query_sha = sha256(b"targeted lexical query").hexdigest()
    channel = HybridChannelCandidateTrace(
        chunk_id=CHUNK_ID,
        document_version_id=VERSION_ID,
        page_number=7,
        rank=2,
        canonical_claim_ids=("support-claim",),
    )
    fused = HybridCandidateTrace(
        chunk_id=CHUNK_ID,
        document_version_id=VERSION_ID,
        page_number=7,
        lexical_rank=2,
        channel_fused_rank=2,
        cosine_score=0.71,
        lexical_score=0.9,
        channel_fused_score=0.03,
        reranker_score=0.8,
        reranked_rank=3,
        selected_for_query_coverage=False,
        selected_as_primary=False,
        canonical_claim_ids=("support-claim",),
    )
    return HybridRetrievalEvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        knowledge_base_id=dataset.knowledge_base_id,
        document_version_ids=dataset.document_version_ids,
        metadata=_hybrid_metadata(query_sha),
        cases=(
            HybridRetrievalCaseResult(
                case_id="ARF-stage",
                queries=("Where is the exact evidence?", "targeted lexical query"),
                baseline_retrieval="failed",
                hybrid_retrieval="failed",
                query_traces=(
                    HybridQueryTrace(
                        query="Where is the exact evidence?",
                        lexical_enabled=False,
                        dense_candidates=(),
                        lexical_candidates=(),
                        candidates=(),
                    ),
                    HybridQueryTrace(
                        query="targeted lexical query",
                        lexical_enabled=True,
                        dense_candidates=(),
                        lexical_candidates=(channel,),
                        candidates=(fused,),
                    ),
                ),
                selected_primary_chunk_ids=(),
                expanded_evidence_chunk_ids=(),
                expanded_candidates=(),
            ),
        ),
        summary=HybridRetrievalSummary(
            baseline_passed=0,
            hybrid_passed=0,
            not_applicable=0,
            improvements=(),
            regressions=(),
        ),
    )


def _source_metadata() -> EvaluationRunMetadata:
    return EvaluationRunMetadata(
        code_commit="source-commit",
        model_provider="fake",
        model_name="fake",
        workflow_version="v1",
        parser_version="v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_version="v1",
        embedding_provider="fake",
        embedding_model="fake",
        embedding_revision="1",
        embedding_dimension=4,
        embedding_version="v1",
        retrieval_version="v1",
        retrieval_top_k=8,
        retrieval_minimum_score=0.5,
        retrieval_minimum_evidence=1,
        generation_prompt_version="v1",
        question_rewrite_prompt_version="v1",
        evidence_assessment_prompt_version="v1",
        citation_repair_prompt_version="v1",
    )


def _hybrid_metadata(query_plan_sha256: str) -> HybridRetrievalRunMetadata:
    return HybridRetrievalRunMetadata(
        code_commit="diagnostic-commit",
        dataset_sha256=sha256(b"dataset").hexdigest(),
        query_plan_sha256=query_plan_sha256,
        retrieval_version="hybrid-v1",
        planner_version="source-report-queries-v1",
        parser_version="v1",
        chunking_version="v1",
        embedding_provider="fake",
        embedding_model="fake",
        embedding_revision="1",
        embedding_version="v1",
        embedding_device="cpu",
        reranker_provider="fake",
        reranker_model="fake",
        reranker_revision="1",
        reranker_weight_sha256=sha256(b"weights").hexdigest(),
        reranker_version="v1",
        reranker_device="cpu",
        phrase_weight=2.0,
        channel_rrf_rank_constant=60,
        channel_candidate_limit=32,
        retrieval_top_k=8,
        retrieval_minimum_score=0.5,
        retrieval_page_neighbor_count=1,
    )
