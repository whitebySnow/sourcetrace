from collections.abc import Sequence
from uuid import UUID

import pytest

from sourcetrace.modules.retrieval.service import (
    RetrievalPlan,
    RetrievalService,
    RetrievedEvidence,
)
from sourcetrace.rag.ports import RetrievalPlanProposal


class StaticPlanner:
    def __init__(self, *additional_queries: str) -> None:
        self.additional_queries = additional_queries
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def plan(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> RetrievalPlanProposal:
        self.calls.append((question, tuple(recent_questions)))
        return RetrievalPlanProposal(additional_queries=self.additional_queries)


class RecordingEmbeddingProvider:
    def __init__(self, embeddings: Sequence[Sequence[float]]) -> None:
        self.embeddings = embeddings
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.calls.append(tuple(texts))
        return self.embeddings


class RankedListRepository:
    def __init__(
        self,
        ranked_lists: dict[tuple[float, ...], list[RetrievedEvidence]],
    ) -> None:
        self.ranked_lists = ranked_lists
        self.search_calls: list[tuple[UUID, tuple[float, ...], int]] = []

    async def search(
        self,
        knowledge_base_id: UUID,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[RetrievedEvidence]:
        key = tuple(query_embedding)
        self.search_calls.append((knowledge_base_id, key, limit))
        return self.ranked_lists[key]

    async def expand_page_neighbors(
        self,
        knowledge_base_id: UUID,
        evidence: Sequence[RetrievedEvidence],
        *,
        neighbor_count: int,
    ) -> list[RetrievedEvidence]:
        return []


def _evidence(
    chunk_id: str,
    *,
    score: float,
    page_number: int,
    document_version_id: str = "10000000-0000-0000-0000-000000000001",
) -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=UUID(chunk_id),
        document_id=UUID("20000000-0000-0000-0000-000000000001"),
        document_version_id=UUID(document_version_id),
        document_name="paper.pdf",
        storage_key="knowledge/paper.pdf",
        page_number=page_number,
        text=f"Evidence {chunk_id}",
        score=score,
    )


async def test_plan_keeps_original_question_and_skips_normalized_duplicates() -> None:
    planner = StaticPlanner(
        "  What does ReAct combine?  ",
        "ReAct reasoning and acting interaction",
    )
    service = RetrievalService(
        repository=RankedListRepository({}),
        embedding_provider=RecordingEmbeddingProvider([]),
        question_planner=planner,
        top_k=8,
    )

    plan = await service.resolve_plan(
        question="What does ReAct combine?",
        recent_questions=("What is ReAct?",),
    )

    assert plan == RetrievalPlan(
        version="bounded-multi-query-v1",
        queries=(
            "What does ReAct combine?",
            "ReAct reasoning and acting interaction",
        ),
    )
    assert planner.calls == [
        ("What does ReAct combine?", ("What is ReAct?",)),
    ]


async def test_multi_query_search_uses_rrf_before_page_diversity() -> None:
    knowledge_base_id = UUID("30000000-0000-0000-0000-000000000001")
    first = _evidence(
        "00000000-0000-0000-0000-000000000001",
        score=0.95,
        page_number=1,
    )
    repeated = _evidence(
        "00000000-0000-0000-0000-000000000002",
        score=0.80,
        page_number=1,
    )
    other_page = _evidence(
        "00000000-0000-0000-0000-000000000003",
        score=0.70,
        page_number=2,
    )
    repository = RankedListRepository(
        {
            (1.0, 0.0): [first, repeated],
            (0.0, 1.0): [repeated, other_page],
        }
    )
    embeddings = RecordingEmbeddingProvider(((1.0, 0.0), (0.0, 1.0)))
    service = RetrievalService(
        repository=repository,
        embedding_provider=embeddings,
        question_planner=StaticPlanner(),
        top_k=2,
        rrf_rank_constant=60,
    )

    result = await service.search(
        knowledge_base_id=knowledge_base_id,
        queries=("original query", "expanded query"),
    )

    assert embeddings.calls == [("original query", "expanded query")]
    assert [call[1] for call in repository.search_calls] == [
        (1.0, 0.0),
        (0.0, 1.0),
    ]
    assert [item.chunk_id for item in result.primary_evidence] == [
        repeated.chunk_id,
        other_page.chunk_id,
    ]
    assert result.primary_evidence[0].score == 0.80
    assert result.query_results[0].candidates[1].rank == 2
    assert result.query_results[1].candidates[0].rank == 1
    fused = {item.evidence.chunk_id: item for item in result.fused_candidates}
    assert fused[repeated.chunk_id].fused_score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[repeated.chunk_id].best_raw_score == 0.80
    assert fused[first.chunk_id].selected_as_primary is False


async def test_rrf_ties_use_best_raw_score_then_chunk_uuid() -> None:
    lower_score = _evidence(
        "00000000-0000-0000-0000-000000000003",
        score=0.80,
        page_number=1,
    )
    higher_uuid = _evidence(
        "00000000-0000-0000-0000-000000000002",
        score=0.90,
        page_number=2,
    )
    lower_uuid = _evidence(
        "00000000-0000-0000-0000-000000000001",
        score=0.90,
        page_number=3,
    )
    service = RetrievalService(
        repository=RankedListRepository(
            {
                (1.0,): [lower_score],
                (2.0,): [higher_uuid],
                (3.0,): [lower_uuid],
            }
        ),
        embedding_provider=RecordingEmbeddingProvider(((1.0,), (2.0,), (3.0,))),
        question_planner=StaticPlanner(),
        top_k=3,
        rrf_rank_constant=60,
    )

    result = await service.search(
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        queries=("one", "two", "three"),
    )

    assert [item.chunk_id for item in result.primary_evidence] == [
        lower_uuid.chunk_id,
        higher_uuid.chunk_id,
        lower_score.chunk_id,
    ]
