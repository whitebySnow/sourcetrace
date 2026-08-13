import asyncio
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Literal, NoReturn, Protocol, TypedDict, cast
from uuid import UUID, uuid5

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from sourcetrace.modules.retrieval.service import (
    RetrievalPlan,
    RetrievalResult,
    RetrievedEvidence,
)
from sourcetrace.rag.ports import (
    AnswerGenerator,
    CitationRepairer,
    CitationValidationFeedback,
    ClaimSupportValidationError,
    ClaimSupportVerifier,
    EvidenceAssessor,
    RetrievalCandidate,
)

_CITATION_LABEL = re.compile(
    r"\[([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]"
)
_ANSWER_UNIT_SPLIT = re.compile(
    r"(?<=[.!?;])\s+|(?<=[\u3002\uff01\uff1f\uff1b])\s*|\n+"
)


class _FailClosedClaimSupportVerifier:
    async def verify(
        self,
        *,
        question: str,
        answer: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> NoReturn:
        raise ClaimSupportValidationError


_FAIL_CLOSED_CLAIM_SUPPORT_VERIFIER = _FailClosedClaimSupportVerifier()


@dataclass(frozen=True, slots=True)
class WorkflowRequest:
    run_id: UUID
    knowledge_base_id: UUID
    question: str
    recent_questions: Sequence[str]


@dataclass(frozen=True, slots=True)
class EvidenceAssessmentTrace:
    sufficient: bool
    selected_chunk_ids: tuple[str, ...]
    supplemental_queries: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "sufficient": self.sufficient,
            "selected_chunk_ids": list(self.selected_chunk_ids),
            "supplemental_queries": list(self.supplemental_queries),
        }


@dataclass(frozen=True, slots=True)
class CitationValidationTrace:
    attempt: Literal["initial", "repair"]
    valid: bool
    issue: Literal["empty_answer", "uncited_claim", "unknown_label", "valid"]
    unit_count: int
    citation_count: int
    uncited_unit_indices: tuple[int, ...]
    unknown_label_unit_indices: tuple[int, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "valid": self.valid,
            "issue": self.issue,
            "unit_count": self.unit_count,
            "citation_count": self.citation_count,
            "uncited_unit_indices": list(self.uncited_unit_indices),
            "unknown_label_unit_indices": list(self.unknown_label_unit_indices),
        }


@dataclass(frozen=True, slots=True)
class RetrievalCandidateTrace:
    chunk_id: str
    raw_rank: int
    raw_cosine_score: float
    dense_rank: int | None
    lexical_rank: int | None
    dense_score: float | None
    lexical_score: float | None
    channel_fused_rank: int
    channel_fused_score: float
    reranker_score: float
    reranked_rank: int
    selected_for_query_coverage: bool

    def to_payload(self) -> dict[str, str | int | float | bool | None]:
        return {
            "chunk_id": self.chunk_id,
            "raw_rank": self.raw_rank,
            "raw_cosine_score": self.raw_cosine_score,
            "dense_rank": self.dense_rank,
            "lexical_rank": self.lexical_rank,
            "dense_score": self.dense_score,
            "lexical_score": self.lexical_score,
            "channel_fused_rank": self.channel_fused_rank,
            "channel_fused_score": self.channel_fused_score,
            "reranker_score": self.reranker_score,
            "reranked_rank": self.reranked_rank,
            "selected_for_query_coverage": self.selected_for_query_coverage,
        }


@dataclass(frozen=True, slots=True)
class QueryRetrievalTrace:
    query: str
    candidates: tuple[RetrievalCandidateTrace, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "query": self.query,
            "candidates": [item.to_payload() for item in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class FusedCandidateTrace:
    chunk_id: str
    fused_score: float
    best_raw_cosine_score: float
    reranker_score: float
    reranked_rank: int
    selected_as_primary: bool

    def to_payload(self) -> dict[str, str | int | float | bool]:
        return {
            "chunk_id": self.chunk_id,
            "fused_score": self.fused_score,
            "best_raw_cosine_score": self.best_raw_cosine_score,
            "reranker_score": self.reranker_score,
            "reranked_rank": self.reranked_rank,
            "selected_as_primary": self.selected_as_primary,
        }


@dataclass(frozen=True, slots=True)
class RerankerTrace:
    provider: str
    model: str
    revision: str
    config_version: str

    def to_payload(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "model": self.model,
            "revision": self.revision,
            "config_version": self.config_version,
        }


@dataclass(frozen=True, slots=True)
class RetrievalRoundTrace:
    round_number: int
    queries: tuple[str, ...]
    query_results: tuple[QueryRetrievalTrace, ...]
    fused_candidates: tuple[FusedCandidateTrace, ...]
    final_evidence_chunk_ids: tuple[str, ...]
    rrf_rank_constant: int
    reranker: RerankerTrace

    def to_payload(self) -> dict[str, object]:
        return {
            "round_number": self.round_number,
            "queries": list(self.queries),
            "query_results": [item.to_payload() for item in self.query_results],
            "fused_candidates": [item.to_payload() for item in self.fused_candidates],
            "final_evidence_chunk_ids": list(self.final_evidence_chunk_ids),
            "rrf_rank_constant": self.rrf_rank_constant,
            "reranker": self.reranker.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class WorkflowTrace:
    retrieval_plan_version: str | None = None
    retrieval_queries: tuple[str, ...] = ()
    retrieval_rounds: tuple[RetrievalRoundTrace, ...] = ()
    assessments: tuple[EvidenceAssessmentTrace, ...] = ()
    citation_validations: tuple[CitationValidationTrace, ...] = ()
    supplemental_retrieval_attempts: int = 0
    citation_repair_attempts: int = 0

    def to_payload(self) -> dict[str, object]:
        return {
            "retrieval_plan_version": self.retrieval_plan_version,
            "retrieval_queries": list(self.retrieval_queries),
            "retrieval_rounds": [item.to_payload() for item in self.retrieval_rounds],
            "assessments": [item.to_payload() for item in self.assessments],
            "citation_validations": [item.to_payload() for item in self.citation_validations],
            "supplemental_retrieval_attempts": self.supplemental_retrieval_attempts,
            "citation_repair_attempts": self.citation_repair_attempts,
        }


@dataclass(frozen=True, slots=True)
class WorkflowStatus:
    stage: Literal[
        "analyzing",
        "retrieving",
        "assessing",
        "generating",
        "validating",
        "repairing",
    ]
    type: Literal["status"] = field(default="status", init=False)


@dataclass(frozen=True, slots=True)
class WorkflowDelta:
    delta: str
    type: Literal["delta"] = field(default="delta", init=False)


@dataclass(frozen=True, slots=True)
class WorkflowAnswered:
    answer: str
    evidence: tuple[RetrievedEvidence, ...]
    type: Literal["answered"] = field(default="answered", init=False)


@dataclass(frozen=True, slots=True)
class WorkflowRefused:
    code: str
    message: str
    type: Literal["refused"] = field(default="refused", init=False)


@dataclass(frozen=True, slots=True)
class WorkflowCancelled:
    type: Literal["cancelled"] = field(default="cancelled", init=False)


type WorkflowEvent = (
    WorkflowStatus | WorkflowDelta | WorkflowAnswered | WorkflowRefused | WorkflowCancelled
)


class _WorkflowCancellation(Exception):
    pass


class WorkflowRetrieval(Protocol):
    async def resolve_plan(
        self,
        *,
        knowledge_base_id: UUID,
        question: str,
        recent_questions: Sequence[str],
    ) -> RetrievalPlan: ...

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        queries: Sequence[str],
    ) -> RetrievalResult: ...


class WorkflowRunControl(Protocol):
    async def record_retrieval_query(self, run_id: UUID, query: str) -> bool: ...

    async def record_workflow_trace(self, run_id: UUID, trace: WorkflowTrace) -> bool: ...

    async def is_cancel_requested(self, run_id: UUID) -> bool: ...


class _WorkflowState(TypedDict, total=False):
    run_id: UUID
    knowledge_base_id: UUID
    question: str
    recent_questions: Sequence[str]
    retrieval_plan: RetrievalPlan
    evidence: list[RetrievedEvidence]
    supplemental_attempts: int
    supplemental_queries: tuple[str, ...]
    selected_evidence: list[RetrievedEvidence]
    cited_evidence: list[RetrievedEvidence]
    answer: str
    claim_support_valid: bool
    citation_valid: bool
    repair_attempts: int
    refusal_code: str
    refusal_message: str
    workflow_trace: WorkflowTrace


class AnswerWorkflow:
    def __init__(
        self,
        *,
        retrieval: WorkflowRetrieval,
        assessor: EvidenceAssessor,
        generator: AnswerGenerator,
        citation_repairer: CitationRepairer,
        run_control: WorkflowRunControl,
        minimum_score: float,
        minimum_evidence: int,
        claim_support_verifier: ClaimSupportVerifier = _FAIL_CLOSED_CLAIM_SUPPORT_VERIFIER,
    ) -> None:
        if not -1.0 <= minimum_score <= 1.0:
            raise ValueError("minimum retrieval score must be between -1 and 1")
        if minimum_evidence <= 0:
            raise ValueError("minimum evidence count must be positive")
        self._retrieval = retrieval
        self._assessor = assessor
        self._generator = generator
        self._claim_support_verifier = claim_support_verifier
        self._citation_repairer = citation_repairer
        self._run_control = run_control
        self._minimum_score = minimum_score
        self._minimum_evidence = minimum_evidence
        self._active_model_stream: AsyncIterator[str] | None = None
        self._active_next_delta: asyncio.Future[str] | None = None
        graph = StateGraph(_WorkflowState)
        graph.add_node("analysis", self._analyze)
        graph.add_node("retrieval", self._retrieve)
        graph.add_node("evidence_assessment", self._assess)
        graph.add_node("supplemental_retrieval", self._retrieve_supplemental)
        graph.add_node("generation", self._generate)
        graph.add_node("claim_support_validation", self._validate_claim_support)
        graph.add_node("citation_validation", self._validate_citations)
        graph.add_node("citation_repair", self._repair_citations)
        graph.add_node("answer", self._answer)
        graph.add_node("refusal", self._refuse)
        graph.add_edge(START, "analysis")
        graph.add_edge("analysis", "retrieval")
        graph.add_edge("retrieval", "evidence_assessment")
        graph.add_conditional_edges(
            "evidence_assessment",
            self._after_assessment,
            {
                "generate": "generation",
                "supplement": "supplemental_retrieval",
                "refuse": "refusal",
            },
        )
        graph.add_edge("supplemental_retrieval", "evidence_assessment")
        graph.add_edge("generation", "citation_validation")
        graph.add_conditional_edges(
            "citation_validation",
            self._after_validation,
            {
                "answer": "answer",
                "repair": "citation_repair",
                "support": "claim_support_validation",
                "refuse": "refusal",
            },
        )
        graph.add_conditional_edges(
            "claim_support_validation",
            self._after_claim_support,
            {"validate": "citation_validation", "refuse": "refusal"},
        )
        graph.add_edge("citation_repair", "citation_validation")
        graph.add_edge("answer", END)
        graph.add_edge("refusal", END)
        self._graph = graph.compile(name="bounded-answer-workflow")

    async def run(self, request: WorkflowRequest) -> AsyncIterator[WorkflowEvent]:
        initial = _WorkflowState(
            run_id=request.run_id,
            knowledge_base_id=request.knowledge_base_id,
            question=request.question,
            recent_questions=request.recent_questions,
            supplemental_attempts=0,
            repair_attempts=0,
            workflow_trace=WorkflowTrace(),
        )
        graph_stream = self._graph.astream(initial, stream_mode="custom")
        try:
            async for event in graph_stream:
                yield cast(WorkflowEvent, event)
        except _WorkflowCancellation:
            yield WorkflowCancelled()
        finally:
            await self._cancel_active_generation()
            await self._close_stream(graph_stream)

    async def _analyze(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(WorkflowStatus(stage="analyzing"))
        plan = await self._retrieval.resolve_plan(
            knowledge_base_id=state["knowledge_base_id"],
            question=state["question"],
            recent_questions=state["recent_questions"],
        )
        if not await self._run_control.record_retrieval_query(state["run_id"], plan.queries[0]):
            raise _WorkflowCancellation
        trace = WorkflowTrace(
            retrieval_plan_version=plan.version,
            retrieval_queries=plan.queries,
        )
        await self._record_trace(state["run_id"], trace)
        return _WorkflowState(retrieval_plan=plan, workflow_trace=trace)

    async def _retrieve(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(WorkflowStatus(stage="retrieving"))
        result = await self._retrieval.search(
            knowledge_base_id=state["knowledge_base_id"],
            queries=state["retrieval_plan"].queries,
        )
        trace = self._with_retrieval_round(state["workflow_trace"], result)
        await self._record_trace(state["run_id"], trace)
        return _WorkflowState(
            evidence=[item for item in result.evidence if item.score >= self._minimum_score],
            workflow_trace=trace,
        )

    async def _assess(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(WorkflowStatus(stage="assessing"))
        candidates = self._candidates(state["run_id"], state["evidence"])
        supplemental_query_limit = (
            max(0, 3 - len(state["retrieval_plan"].queries))
            if state["supplemental_attempts"] == 0
            else 0
        )
        decision = await self._assessor.assess(
            question=state["question"],
            queries=state["retrieval_plan"].queries,
            evidence=candidates,
            supplemental_query_limit=supplemental_query_limit,
        )
        trace = replace(
            state["workflow_trace"],
            assessments=(
                *state["workflow_trace"].assessments,
                EvidenceAssessmentTrace(
                    sufficient=decision.sufficient,
                    selected_chunk_ids=decision.selected_chunk_ids,
                    supplemental_queries=decision.supplemental_queries,
                ),
            ),
        )
        await self._record_trace(state["run_id"], trace)
        selected_ids = set(decision.selected_chunk_ids)
        available_ids = {str(item.chunk_id) for item in state["evidence"]}
        if not selected_ids <= available_ids:
            return self._refusal_state(
                "INSUFFICIENT_EVIDENCE",
                "The knowledge base does not contain enough evidence to answer.",
                trace=trace,
            )
        selected = [item for item in state["evidence"] if str(item.chunk_id) in selected_ids]
        if decision.sufficient and len(selected) >= self._minimum_evidence:
            return _WorkflowState(selected_evidence=selected, workflow_trace=trace)
        supplemental_queries = decision.supplemental_queries[:supplemental_query_limit]
        expanded_plan = state["retrieval_plan"].with_additional_queries(supplemental_queries)
        if state["supplemental_attempts"] == 0 and expanded_plan is not None:
            return _WorkflowState(
                supplemental_queries=supplemental_queries,
                retrieval_plan=expanded_plan,
                workflow_trace=trace,
            )
        return self._refusal_state(
            "INSUFFICIENT_EVIDENCE",
            "The knowledge base does not contain enough evidence to answer.",
            trace=trace,
        )

    async def _retrieve_supplemental(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(WorkflowStatus(stage="retrieving"))
        result = await self._retrieval.search(
            knowledge_base_id=state["knowledge_base_id"],
            queries=state["retrieval_plan"].queries,
        )
        trace = replace(
            self._with_retrieval_round(state["workflow_trace"], result),
            retrieval_queries=state["retrieval_plan"].queries,
            supplemental_retrieval_attempts=1,
        )
        await self._record_trace(state["run_id"], trace)
        return _WorkflowState(
            evidence=[item for item in result.evidence if item.score >= self._minimum_score],
            supplemental_attempts=1,
            workflow_trace=trace,
        )

    async def _generate(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        writer = get_stream_writer()
        writer(WorkflowStatus(stage="generating"))
        parts: list[str] = []
        model_stream = self._generator.stream_answer(
            question=state["question"],
            evidence=self._candidates(state["run_id"], state["selected_evidence"]),
        )
        self._active_model_stream = model_stream
        try:
            while True:
                delta = await self._next_delta_or_cancel(state["run_id"], model_stream)
                if delta is None:
                    break
                parts.append(delta)
                writer(WorkflowDelta(delta=delta))
        finally:
            await self._close_stream(model_stream)
            if self._active_model_stream is model_stream:
                self._active_model_stream = None
        return _WorkflowState(answer="".join(parts).strip())

    async def _validate_citations(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(WorkflowStatus(stage="validating"))
        allowed = {
            str(uuid5(state["run_id"], str(item.chunk_id))): item
            for item in state["selected_evidence"]
        }
        cited = _CITATION_LABEL.findall(state["answer"])
        validation = self._citation_validation_trace(
            state["answer"],
            set(allowed),
            attempt="repair" if state["repair_attempts"] else "initial",
        )
        citation_valid = validation.valid
        trace = replace(
            state["workflow_trace"],
            citation_validations=(
                *state["workflow_trace"].citation_validations,
                validation,
            ),
        )
        await self._record_trace(state["run_id"], trace)
        if not citation_valid and state["repair_attempts"] >= 1:
            return _WorkflowState(
                citation_valid=False,
                refusal_code="CITATION_VALIDATION_FAILED",
                refusal_message=("The generated answer did not contain a valid evidence citation."),
                workflow_trace=trace,
            )
        cited_ids = set(cited)
        return _WorkflowState(
            citation_valid=citation_valid,
            cited_evidence=[
                item for citation_id, item in allowed.items() if citation_id in cited_ids
            ],
            workflow_trace=trace,
        )

    async def _validate_claim_support(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(WorkflowStatus(stage="validating"))
        candidates = self._candidates(state["run_id"], state["selected_evidence"])
        try:
            decision = await self._claim_support_verifier.verify(
                question=state["question"],
                answer=state["answer"],
                evidence=candidates,
            )
        except ClaimSupportValidationError:
            return self._refusal_state(
                "CLAIM_SUPPORT_VALIDATION_FAILED",
                "The generated answer included a claim not supported by the evidence.",
                trace=state["workflow_trace"],
            )
        allowed = {item.citation_id for item in candidates}
        rendered: list[str] = []
        for claim in decision.claims:
            text = _CITATION_LABEL.sub("", claim.text).strip()
            citation_ids = tuple(dict.fromkeys(claim.citation_ids))
            if (
                not text
                or not citation_ids
                or any(item not in allowed for item in citation_ids)
            ):
                return self._refusal_state(
                    "CLAIM_SUPPORT_VALIDATION_FAILED",
                    "The generated answer included a claim not supported by the evidence.",
                    trace=state["workflow_trace"],
            )
            labels = " ".join(f"[{item}]" for item in citation_ids)
            units = [unit.strip() for unit in _ANSWER_UNIT_SPLIT.split(text) if unit.strip()]
            rendered.extend(f"{unit} {labels}" for unit in units)
        if not rendered:
            return self._refusal_state(
                "CLAIM_SUPPORT_VALIDATION_FAILED",
                "The generated answer included a claim not supported by the evidence.",
                trace=state["workflow_trace"],
            )
        return _WorkflowState(
            answer="\n".join(rendered),
            claim_support_valid=True,
        )

    async def _repair_citations(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(WorkflowStatus(stage="repairing"))
        trace = replace(
            state["workflow_trace"],
            citation_repair_attempts=1,
        )
        await self._record_trace(state["run_id"], trace)
        validation = trace.citation_validations[-1]
        if validation.issue == "valid":
            raise RuntimeError("citation repair requires an invalid validation result")
        repaired = await self._citation_repairer.repair(
            question=state["question"],
            answer=state["answer"],
            evidence=self._candidates(state["run_id"], state["selected_evidence"]),
            validation_feedback=CitationValidationFeedback(
                issue=validation.issue,
                unit_count=validation.unit_count,
                citation_count=validation.citation_count,
                uncited_unit_indices=validation.uncited_unit_indices,
                unknown_label_unit_indices=validation.unknown_label_unit_indices,
            ),
        )
        return _WorkflowState(
            answer=repaired.strip(),
            claim_support_valid=False,
            repair_attempts=1,
            workflow_trace=trace,
        )

    async def _answer(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(
            WorkflowAnswered(
                answer=state["answer"],
                evidence=tuple(state["cited_evidence"]),
            )
        )
        return _WorkflowState()

    async def _refuse(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(
            WorkflowRefused(
                code=state["refusal_code"],
                message=state["refusal_message"],
            )
        )
        return _WorkflowState()

    @staticmethod
    def _after_assessment(
        state: _WorkflowState,
    ) -> Literal["generate", "supplement", "refuse"]:
        if "refusal_code" in state:
            return "refuse"
        if "supplemental_queries" in state and state["supplemental_attempts"] == 0:
            return "supplement"
        return "generate"

    @staticmethod
    def _after_validation(
        state: _WorkflowState,
    ) -> Literal["answer", "repair", "refuse", "support"]:
        if state["citation_valid"] and not state.get("claim_support_valid", False):
            return "support"
        if state["citation_valid"]:
            return "answer"
        return "refuse" if state["repair_attempts"] >= 1 else "repair"

    @staticmethod
    def _after_claim_support(state: _WorkflowState) -> Literal["validate", "refuse"]:
        return "validate" if state.get("claim_support_valid", False) else "refuse"

    @staticmethod
    def _refusal_state(
        code: str,
        message: str,
        *,
        trace: WorkflowTrace,
    ) -> _WorkflowState:
        return _WorkflowState(
            refusal_code=code,
            refusal_message=message,
            workflow_trace=trace,
        )

    async def _record_trace(self, run_id: UUID, trace: WorkflowTrace) -> None:
        if not await self._run_control.record_workflow_trace(run_id, trace):
            raise _WorkflowCancellation

    @staticmethod
    def _with_retrieval_round(
        trace: WorkflowTrace,
        result: RetrievalResult,
    ) -> WorkflowTrace:
        retrieval_round = RetrievalRoundTrace(
            round_number=len(trace.retrieval_rounds) + 1,
            queries=tuple(item.query for item in result.query_results),
            query_results=tuple(
                QueryRetrievalTrace(
                    query=item.query,
                    candidates=tuple(
                        RetrievalCandidateTrace(
                            chunk_id=str(candidate.evidence.chunk_id),
                            raw_rank=candidate.rank,
                            raw_cosine_score=candidate.evidence.score,
                            dense_rank=candidate.dense_rank,
                            lexical_rank=candidate.lexical_rank,
                            dense_score=candidate.dense_score,
                            lexical_score=candidate.lexical_score,
                            channel_fused_rank=candidate.rank,
                            channel_fused_score=candidate.channel_fused_score,
                            reranker_score=cast(float, candidate.reranker_score),
                            reranked_rank=cast(int, candidate.reranked_rank),
                            selected_for_query_coverage=(
                                candidate.selected_for_query_coverage
                            ),
                        )
                        for candidate in item.candidates
                    ),
                )
                for item in result.query_results
            ),
            fused_candidates=tuple(
                FusedCandidateTrace(
                    chunk_id=str(item.evidence.chunk_id),
                    fused_score=item.fused_score,
                    best_raw_cosine_score=item.best_raw_score,
                    reranker_score=item.reranker_score,
                    reranked_rank=item.reranked_rank,
                    selected_as_primary=item.selected_as_primary,
                )
                for item in result.fused_candidates
            ),
            final_evidence_chunk_ids=tuple(str(item.chunk_id) for item in result.evidence),
            rrf_rank_constant=result.rrf_rank_constant,
            reranker=RerankerTrace(
                provider=result.reranker_identity.provider,
                model=result.reranker_identity.model,
                revision=result.reranker_identity.revision,
                config_version=result.reranker_identity.config_version,
            ),
        )
        return replace(
            trace,
            retrieval_rounds=(*trace.retrieval_rounds, retrieval_round),
        )

    @staticmethod
    def _citation_validation_issue(
        answer: str,
        allowed: set[str],
    ) -> Literal["empty_answer", "uncited_claim", "unknown_label", "valid"]:
        return AnswerWorkflow._citation_validation_trace(
            answer,
            allowed,
            attempt="initial",
        ).issue

    @staticmethod
    def _citation_validation_trace(
        answer: str,
        allowed: set[str],
        *,
        attempt: Literal["initial", "repair"],
    ) -> CitationValidationTrace:
        if not answer.strip():
            return CitationValidationTrace(
                attempt=attempt,
                valid=False,
                issue="empty_answer",
                unit_count=0,
                citation_count=0,
                uncited_unit_indices=(),
                unknown_label_unit_indices=(),
            )
        units = [
            unit.strip()
            for unit in _ANSWER_UNIT_SPLIT.split(answer)
            if unit.strip()
        ]
        if not units:
            return CitationValidationTrace(
                attempt=attempt,
                valid=False,
                issue="empty_answer",
                unit_count=0,
                citation_count=0,
                uncited_unit_indices=(),
                unknown_label_unit_indices=(),
            )
        issue: Literal["uncited_claim", "unknown_label", "valid"] = "valid"
        citation_count = 0
        uncited_unit_indices: list[int] = []
        unknown_label_unit_indices: list[int] = []
        for index, unit in enumerate(units):
            labels = _CITATION_LABEL.findall(unit)
            citation_count += len(labels)
            if any(label not in allowed for label in labels):
                unknown_label_unit_indices.append(index)
                if issue == "valid":
                    issue = "unknown_label"
                continue
            if AnswerWorkflow._is_citation_only(unit):
                continue
            if labels:
                continue
            if index + 1 >= len(units) or not AnswerWorkflow._is_citation_only(
                units[index + 1], allowed
            ):
                uncited_unit_indices.append(index)
                if issue == "valid":
                    issue = "uncited_claim"
        return CitationValidationTrace(
            attempt=attempt,
            valid=issue == "valid",
            issue=issue,
            unit_count=len(units),
            citation_count=citation_count,
            uncited_unit_indices=tuple(uncited_unit_indices),
            unknown_label_unit_indices=tuple(unknown_label_unit_indices),
        )

    @staticmethod
    def _is_citation_only(unit: str, allowed: set[str] | None = None) -> bool:
        labels = _CITATION_LABEL.findall(unit)
        if not labels or (allowed is not None and any(label not in allowed for label in labels)):
            return False
        remainder = _CITATION_LABEL.sub("", unit).strip(" \t\r\n.,;:!?\u3002\uff01\uff1f\uff1b")
        return not remainder

    async def _ensure_active(self, run_id: UUID) -> None:
        if await self._run_control.is_cancel_requested(run_id):
            raise _WorkflowCancellation

    async def _next_delta_or_cancel(
        self,
        run_id: UUID,
        stream: AsyncIterator[str],
    ) -> str | None:
        next_delta: asyncio.Future[str] = asyncio.ensure_future(anext(stream))
        self._active_next_delta = next_delta
        try:
            while True:
                done, _pending = await asyncio.wait({next_delta}, timeout=0.1)
                if done:
                    try:
                        return next_delta.result()
                    except StopAsyncIteration:
                        return None
                await self._ensure_active(run_id)
        except BaseException:
            if not next_delta.done():
                next_delta.cancel()
                with suppress(asyncio.CancelledError):
                    await next_delta
            raise
        finally:
            if self._active_next_delta is next_delta:
                self._active_next_delta = None

    async def _cancel_active_generation(self) -> None:
        next_delta = self._active_next_delta
        if next_delta is not None and not next_delta.done():
            next_delta.cancel()
            with suppress(asyncio.CancelledError):
                await next_delta
        model_stream = self._active_model_stream
        if model_stream is not None:
            await self._close_stream(model_stream)
            if self._active_model_stream is model_stream:
                self._active_model_stream = None

    @staticmethod
    async def _close_stream(stream: AsyncIterator[object]) -> None:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()

    @staticmethod
    def _candidates(
        run_id: UUID,
        evidence: Sequence[RetrievedEvidence],
    ) -> tuple[RetrievalCandidate, ...]:
        return tuple(
            RetrievalCandidate(
                chunk_id=str(item.chunk_id),
                content=item.text,
                score=item.score,
                citation_id=str(uuid5(run_id, str(item.chunk_id))),
            )
            for item in evidence
        )
