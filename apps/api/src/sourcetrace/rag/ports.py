from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: str
    content: str
    score: float
    citation_id: str


class Retriever(Protocol):
    async def search(
        self, *, knowledge_base_id: str, query: str
    ) -> Sequence[RetrievalCandidate]: ...


class AnswerGenerator(Protocol):
    def stream_answer(
        self, *, question: str, evidence: Sequence[RetrievalCandidate]
    ) -> AsyncIterator[str]: ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
