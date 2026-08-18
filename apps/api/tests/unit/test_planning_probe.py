from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sourcetrace.evaluation.models import (
    DatasetReview,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRunMetadata,
    EvidenceReference,
    ExpectedResult,
)
from sourcetrace.evaluation.planning_probe import run_planning_probe
from sourcetrace.rag.ports import (
    QueryPlanningFailure,
    QueryPlanningSlotTrace,
    QueryPlanningTrace,
    RetrievalPlanProposal,
)


def _dataset() -> EvaluationDataset:
    knowledge_base_id = uuid4()
    document_version_id = uuid4()
    return EvaluationDataset(
        schema_version="1",
        dataset_id="planning-probe-fixture",
        dataset_version="1.0.0",
        knowledge_base_id=knowledge_base_id,
        document_version_ids=[document_version_id],
        review=DatasetReview(
            status="reviewed",
            reviewed_by="project-owner",
            reviewed_at=datetime(2026, 8, 18, tzinfo=UTC),
        ),
        cases=[
            EvaluationCase(
                id="ARF-025",
                category="multi_chunk",
                question="Question text must not appear in the artifact.",
                expected=ExpectedResult(
                    outcome="answered",
                    reference_answer="Reference answer must not appear in the artifact.",
                    evidence=[
                        EvidenceReference(
                            document_version_id=document_version_id,
                            page_number=1,
                            text="Evidence text must not appear in the artifact.",
                        )
                    ],
                ),
            ),
            EvaluationCase(
                id="ARF-026",
                category="confusing",
                question="Another question text must not appear in the artifact.",
                expected=ExpectedResult(
                    outcome="answered",
                    reference_answer="Another reference answer must not appear in the artifact.",
                    evidence=[
                        EvidenceReference(
                            document_version_id=document_version_id,
                            page_number=2,
                            text="Another evidence text must not appear in the artifact.",
                        )
                    ],
                ),
            ),
        ],
    )


def _metadata() -> EvaluationRunMetadata:
    return EvaluationRunMetadata(
        code_commit="test-commit",
        model_provider="openai-compatible",
        model_name="test-model",
        workflow_version="planning-probe-v1",
        parser_version="test-parser",
        tokenizer="test-tokenizer",
        chunk_size=512,
        chunk_overlap=64,
        chunking_version="test-chunking",
        embedding_provider="sentence-transformers",
        embedding_model="test-embedding",
        embedding_revision="test-revision",
        embedding_dimension=1024,
        embedding_version="test-embedding-v1",
        retrieval_version="test-retrieval-v1",
        retrieval_top_k=8,
        retrieval_minimum_score=0.2,
        retrieval_minimum_evidence=1,
        generation_prompt_version="unused",
        question_rewrite_prompt_version="retrieval-plan-v8",
        evidence_assessment_prompt_version="unused",
        citation_repair_prompt_version="unused",
    )


class _PlanningFake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def plan(
        self,
        *,
        question: str,
        recent_questions: tuple[str, ...],
        document_titles: tuple[str, ...] = (),
    ) -> RetrievalPlanProposal:
        self.calls.append((question, document_titles))
        if question.startswith("Another"):
            raise QueryPlanningFailure(
                code="LLM_INVALID_RESPONSE",
                safe_message="Language model returned an invalid response",
                reason="provider_structured_invalid_json",
                planning_trace=QueryPlanningTrace(
                    initial_disposition="failed",
                    initial_correction_applied=True,
                    initial_slot_count=0,
                    selected_slots=(),
                ),
                retrieval_plan_version="retrieval-plan-v8",
            )
        return RetrievalPlanProposal(
            additional_queries=("bounded query",),
            planning_trace=QueryPlanningTrace(
                initial_disposition="accepted",
                initial_correction_applied=False,
                initial_slot_count=1,
                selected_slots=(
                    QueryPlanningSlotTrace(
                        title_anchor="ReAct",
                        refinement_disposition="not_required",
                    ),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_planning_probe_records_only_sanitized_planning_observations() -> None:
    planner = _PlanningFake()
    report = await run_planning_probe(
        _dataset(),
        case_ids=("ARF-025", "ARF-026"),
        planner=planner,
        document_titles=("ReAct.pdf", "Self-RAG.pdf"),
        metadata=_metadata(),
    )

    payload = report.model_dump_json()

    assert [observation.case_id for observation in report.observations] == [
        "ARF-025",
        "ARF-026",
    ]
    assert report.observations[0].status == "planned"
    assert report.observations[0].planning.selected_slots[0].title_anchor == "ReAct"
    assert report.observations[1].status == "failed"
    assert report.observations[1].error_code == "LLM_INVALID_RESPONSE"
    assert report.observations[1].error_reason == "provider_structured_invalid_json"
    assert "Question text" not in payload
    assert "Reference answer" not in payload
    assert "Evidence text" not in payload
    assert "ReAct.pdf" not in payload


@pytest.mark.asyncio
async def test_planning_probe_rejects_more_than_two_or_unknown_case_ids() -> None:
    planner = _PlanningFake()
    dataset = _dataset()

    with pytest.raises(ValueError, match="one or two"):
        await run_planning_probe(
            dataset,
            case_ids=("ARF-025", "ARF-026", "ARF-027"),
            planner=planner,
            document_titles=(),
            metadata=_metadata(),
        )
    with pytest.raises(ValueError, match="unknown planning probe case IDs: ARF-999"):
        await run_planning_probe(
            dataset,
            case_ids=("ARF-999",),
            planner=planner,
            document_titles=(),
            metadata=_metadata(),
        )
