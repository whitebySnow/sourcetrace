from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sourcetrace.rag.ports import EmbeddingProvider, QuestionRewriter


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    document_name: str
    storage_key: str
    page_number: int
    text: str
    score: float
    page_chunk_index: int = 0


class RetrievalRepositoryPort(Protocol):
    async def search(
        self,
        knowledge_base_id: UUID,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[RetrievedEvidence]: ...

    async def expand_page_neighbors(
        self,
        knowledge_base_id: UUID,
        evidence: Sequence[RetrievedEvidence],
        *,
        neighbor_count: int,
    ) -> list[RetrievedEvidence]: ...


class RetrievalService:
    def __init__(
        self,
        *,
        repository: RetrievalRepositoryPort,
        embedding_provider: EmbeddingProvider,
        question_rewriter: QuestionRewriter,
        top_k: int,
        page_neighbor_count: int = 0,
    ) -> None:
        if top_k <= 0:
            raise ValueError("retrieval top_k must be positive")
        if page_neighbor_count < 0:
            raise ValueError("retrieval page neighbor count must not be negative")
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._question_rewriter = question_rewriter
        self._top_k = top_k
        self._page_neighbor_count = page_neighbor_count

    async def resolve_query(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> str:
        if not recent_questions:
            return question
        return await self._question_rewriter.rewrite(
            question=question,
            recent_questions=recent_questions,
        )

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query: str,
    ) -> list[RetrievedEvidence]:
        embeddings = await self._embedding_provider.embed([query])
        if len(embeddings) != 1:
            raise ValueError("query embedding provider returned an invalid result")
        retrieved = await self._repository.search(
            knowledge_base_id,
            embeddings[0],
            limit=self._top_k,
        )
        if not retrieved or self._page_neighbor_count == 0:
            return retrieved
        neighbors = await self._repository.expand_page_neighbors(
            knowledge_base_id,
            retrieved,
            neighbor_count=self._page_neighbor_count,
        )
        by_chunk_id = {item.chunk_id: item for item in retrieved}
        for neighbor in neighbors:
            by_chunk_id.setdefault(neighbor.chunk_id, neighbor)
        return list(by_chunk_id.values())
