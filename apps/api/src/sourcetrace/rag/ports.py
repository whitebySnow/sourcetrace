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
    supplemental_queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalPlanProposal:
    additional_queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RerankerIdentity:
    provider: str
    model: str
    revision: str
    config_version: str


class Reranker(Protocol):
    @property
    def identity(self) -> RerankerIdentity: ...

    async def score(
        self,
        *,
        question: str,
        passages: Sequence[str],
    ) -> Sequence[float]: ...


class AnswerGenerator(Protocol):
    def stream_answer(
        self, *, question: str, evidence: Sequence[RetrievalCandidate]
    ) -> AsyncIterator[str]: ...


class EvidenceAssessor(Protocol):
    async def assess(
        self,
        *,
        question: str,
        queries: Sequence[str],
        evidence: Sequence[RetrievalCandidate],
        supplemental_query_limit: int,
    ) -> EvidenceDecision: ...


class CitationRepairer(Protocol):
    async def repair(
        self,
        *,
        question: str,
        answer: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> str: ...


class QuestionPlanner(Protocol):
    async def plan(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
        document_titles: Sequence[str] = (),
    ) -> RetrievalPlanProposal: ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
