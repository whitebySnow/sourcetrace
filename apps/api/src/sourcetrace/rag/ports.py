from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: str
    content: str
    score: float
    citation_id: str


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    sufficient: bool
    selected_chunk_ids: tuple[str, ...]
    supplemental_query: str | None


class Retriever(Protocol):
    async def search(
        self, *, knowledge_base_id: str, query: str
    ) -> Sequence[RetrievalCandidate]: ...


class AnswerGenerator(Protocol):
    def stream_answer(
        self, *, question: str, evidence: Sequence[RetrievalCandidate]
    ) -> AsyncIterator[str]: ...


class EvidenceAssessor(Protocol):
    async def assess(
        self,
        *,
        question: str,
        query: str,
        evidence: Sequence[RetrievalCandidate],
        supplemental_allowed: bool,
    ) -> EvidenceDecision: ...


class CitationRepairer(Protocol):
    async def repair(
        self,
        *,
        question: str,
        answer: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> str: ...


class QuestionRewriter(Protocol):
    async def rewrite(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> str: ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
