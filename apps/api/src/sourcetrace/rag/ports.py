from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: str
    content: str
    score: float
    citation_id: str
    document_title: str
    page_number: int
    matched_queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    sufficient: bool
    selected_chunk_ids: tuple[str, ...]
    supplemental_queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CitationValidationFeedback:
    issue: Literal["empty_answer", "uncited_claim", "unknown_label"]
    unit_count: int
    citation_count: int
    uncited_unit_indices: tuple[int, ...]
    unknown_label_unit_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GroundedClaim:
    text: str
    citation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClaimSupportDecision:
    claims: tuple[GroundedClaim, ...]


class ClaimSupportValidationError(Exception):
    """Raised when a claim-support decision cannot be safely used."""


type InitialPlanDisposition = Literal["empty", "accepted", "rejected"]
type RefinementDisposition = Literal[
    "not_required",
    "accepted",
    "provider_error",
    "invalid_shape",
    "document_changed",
    "anchor_changed",
    "anchor_invalid",
    "unchanged_query",
    "title_attribution",
]


@dataclass(frozen=True, slots=True)
class QueryPlanningSlotTrace:
    title_anchor: str
    refinement_disposition: RefinementDisposition


@dataclass(frozen=True, slots=True)
class QueryPlanningTrace:
    initial_disposition: InitialPlanDisposition
    initial_correction_applied: bool
    initial_slot_count: int
    selected_slots: tuple[QueryPlanningSlotTrace, ...]


@dataclass(frozen=True, slots=True)
class RetrievalPlanProposal:
    additional_queries: tuple[str, ...]
    planning_trace: QueryPlanningTrace | None = None


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
        previously_selected_chunk_ids: Sequence[str],
        supplemental_query_limit: int,
    ) -> EvidenceDecision: ...


class CitationRepairer(Protocol):
    async def repair(
        self,
        *,
        question: str,
        answer: str,
        evidence: Sequence[RetrievalCandidate],
        validation_feedback: CitationValidationFeedback,
    ) -> str: ...


class ClaimSupportVerifier(Protocol):
    async def verify(
        self,
        *,
        question: str,
        answer: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> ClaimSupportDecision: ...


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
