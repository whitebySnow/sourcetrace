from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from sourcetrace.modules.retrieval.service import RetrievalService, RetrievedEvidence
from sourcetrace.rag.ports import RetrievalPlanProposal
from tests.helpers import PreserveOrderReranker


class StaticEmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        assert list(texts) == ["Where is the expected evidence?"]
        return [[1.0, 0.0]]


class UnusedQuestionPlanner:
    async def plan(self, **kwargs: object) -> RetrievalPlanProposal:
        raise AssertionError("query planning must not start")


class PageContextRepository:
    def __init__(
        self,
        primary: RetrievedEvidence,
        neighbor: RetrievedEvidence,
    ) -> None:
        self.primary = primary
        self.neighbor = neighbor
        self.neighbor_calls: list[tuple[UUID, tuple[RetrievedEvidence, ...], int]] = []

    async def search(
        self,
        knowledge_base_id: UUID,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[RetrievedEvidence]:
        assert query_embedding == [1.0, 0.0]
        return [self.primary]

    async def expand_page_neighbors(
        self,
        knowledge_base_id: UUID,
        evidence: Sequence[RetrievedEvidence],
        *,
        neighbor_count: int,
    ) -> list[RetrievedEvidence]:
        self.neighbor_calls.append((knowledge_base_id, tuple(evidence), neighbor_count))
        return [self.neighbor]


def _evidence(*, page_chunk_index: int, text: str) -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        document_name="paper.pdf",
        storage_key="knowledge/paper.pdf",
        page_number=4,
        text=text,
        score=0.8,
        page_chunk_index=page_chunk_index,
    )


async def test_retrieval_adds_only_repository_supplied_same_page_neighbors() -> None:
    knowledge_base_id = uuid4()
    primary = _evidence(page_chunk_index=1, text="Initial matching chunk")
    neighbor = _evidence(page_chunk_index=0, text="Adjacent evidence chunk")
    repository = PageContextRepository(primary, neighbor)
    service = RetrievalService(
        repository=repository,
        embedding_provider=StaticEmbeddingProvider(),
        question_planner=UnusedQuestionPlanner(),
        reranker=PreserveOrderReranker(),
        top_k=8,
        page_neighbor_count=1,
    )

    result = await service.search(
        knowledge_base_id=knowledge_base_id,
        queries=("Where is the expected evidence?",),
    )

    assert [item.chunk_id for item in result.evidence] == [
        primary.chunk_id,
        neighbor.chunk_id,
    ]
    assert repository.neighbor_calls == [(knowledge_base_id, (primary,), 1)]


async def test_retrieval_does_not_expand_pages_when_disabled() -> None:
    primary = _evidence(page_chunk_index=1, text="Initial matching chunk")
    repository = PageContextRepository(primary, _evidence(page_chunk_index=0, text="Unused"))
    service = RetrievalService(
        repository=repository,
        embedding_provider=StaticEmbeddingProvider(),
        question_planner=UnusedQuestionPlanner(),
        reranker=PreserveOrderReranker(),
        top_k=8,
    )

    result = await service.search(
        knowledge_base_id=uuid4(),
        queries=("Where is the expected evidence?",),
    )

    assert result.evidence == (primary,)
    assert repository.neighbor_calls == []


def test_retrieval_rejects_negative_page_neighbor_count() -> None:
    primary = _evidence(page_chunk_index=1, text="Initial matching chunk")

    with pytest.raises(ValueError, match="page neighbor count"):
        RetrievalService(
            repository=PageContextRepository(primary, primary),
            embedding_provider=StaticEmbeddingProvider(),
            question_planner=UnusedQuestionPlanner(),
            reranker=PreserveOrderReranker(),
            top_k=8,
            page_neighbor_count=-1,
        )


def test_retrieval_rejects_more_than_eight_primary_candidates() -> None:
    primary = _evidence(page_chunk_index=1, text="Initial matching chunk")

    with pytest.raises(ValueError, match="at most 8"):
        RetrievalService(
            repository=PageContextRepository(primary, primary),
            embedding_provider=StaticEmbeddingProvider(),
            question_planner=UnusedQuestionPlanner(),
            reranker=PreserveOrderReranker(),
            top_k=9,
        )
