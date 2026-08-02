import asyncio
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict, cast
from uuid import UUID, uuid5

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from sourcetrace.modules.retrieval.service import RetrievedEvidence
from sourcetrace.rag.ports import (
    AnswerGenerator,
    CitationRepairer,
    EvidenceAssessor,
    RetrievalCandidate,
)

_CITATION_LABEL = re.compile(
    r"\[([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]"
)


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
    supplemental_query: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "sufficient": self.sufficient,
            "selected_chunk_ids": list(self.selected_chunk_ids),
            "supplemental_query": self.supplemental_query,
        }


@dataclass(frozen=True, slots=True)
class CitationValidationTrace:
    valid: bool
    issue: Literal["empty_answer", "uncited_claim", "unknown_label", "valid"]

    def to_payload(self) -> dict[str, str | bool]:
        return {"valid": self.valid, "issue": self.issue}


@dataclass(frozen=True, slots=True)
class WorkflowTrace:
    retrieval_queries: tuple[str, ...] = ()
    assessments: tuple[EvidenceAssessmentTrace, ...] = ()
    citation_validations: tuple[CitationValidationTrace, ...] = ()
    supplemental_retrieval_attempts: int = 0
    citation_repair_attempts: int = 0

    def to_payload(self) -> dict[str, object]:
        return {
            "retrieval_queries": list(self.retrieval_queries),
            "assessments": [item.to_payload() for item in self.assessments],
            "citation_validations": [
                item.to_payload() for item in self.citation_validations
            ],
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
    async def resolve_query(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> str: ...

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query: str,
    ) -> list[RetrievedEvidence]: ...


class WorkflowRunControl(Protocol):
    async def record_retrieval_query(self, run_id: UUID, query: str) -> bool: ...

    async def record_workflow_trace(self, run_id: UUID, trace: WorkflowTrace) -> bool: ...

    async def is_cancel_requested(self, run_id: UUID) -> bool: ...


class _WorkflowState(TypedDict, total=False):
    run_id: UUID
    knowledge_base_id: UUID
    question: str
    recent_questions: Sequence[str]
    retrieval_query: str
    evidence: list[RetrievedEvidence]
    supplemental_attempts: int
    supplemental_query: str
    selected_evidence: list[RetrievedEvidence]
    cited_evidence: list[RetrievedEvidence]
    answer: str
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
    ) -> None:
        if not -1.0 <= minimum_score <= 1.0:
            raise ValueError("minimum retrieval score must be between -1 and 1")
        if minimum_evidence <= 0:
            raise ValueError("minimum evidence count must be positive")
        self._retrieval = retrieval
        self._assessor = assessor
        self._generator = generator
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
            {"answer": "answer", "repair": "citation_repair", "refuse": "refusal"},
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
        query = await self._retrieval.resolve_query(
            question=state["question"],
            recent_questions=state["recent_questions"],
        )
        if not await self._run_control.record_retrieval_query(state["run_id"], query):
            raise _WorkflowCancellation
        trace = WorkflowTrace(retrieval_queries=(query,))
        await self._record_trace(state["run_id"], trace)
        return _WorkflowState(retrieval_query=query, workflow_trace=trace)

    async def _retrieve(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(WorkflowStatus(stage="retrieving"))
        evidence = await self._retrieval.search(
            knowledge_base_id=state["knowledge_base_id"],
            query=state["retrieval_query"],
        )
        return _WorkflowState(
            evidence=[item for item in evidence if item.score >= self._minimum_score]
        )

    async def _assess(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(WorkflowStatus(stage="assessing"))
        candidates = self._candidates(state["run_id"], state["evidence"])
        decision = await self._assessor.assess(
            question=state["question"],
            query=state["retrieval_query"],
            evidence=candidates,
            supplemental_allowed=state["supplemental_attempts"] == 0,
        )
        trace = WorkflowTrace(
            retrieval_queries=state["workflow_trace"].retrieval_queries,
            assessments=(
                *state["workflow_trace"].assessments,
                EvidenceAssessmentTrace(
                    sufficient=decision.sufficient,
                    selected_chunk_ids=decision.selected_chunk_ids,
                    supplemental_query=decision.supplemental_query,
                ),
            ),
            citation_validations=state["workflow_trace"].citation_validations,
            supplemental_retrieval_attempts=(
                state["workflow_trace"].supplemental_retrieval_attempts
            ),
            citation_repair_attempts=state["workflow_trace"].citation_repair_attempts,
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
        supplemental_query = (decision.supplemental_query or "").strip()
        if state["supplemental_attempts"] == 0 and supplemental_query:
            return _WorkflowState(
                supplemental_query=supplemental_query,
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
        trace = WorkflowTrace(
            retrieval_queries=(
                *state["workflow_trace"].retrieval_queries,
                state["supplemental_query"],
            ),
            assessments=state["workflow_trace"].assessments,
            citation_validations=state["workflow_trace"].citation_validations,
            supplemental_retrieval_attempts=1,
            citation_repair_attempts=state["workflow_trace"].citation_repair_attempts,
        )
        await self._record_trace(state["run_id"], trace)
        supplemental = await self._retrieval.search(
            knowledge_base_id=state["knowledge_base_id"],
            query=state["supplemental_query"],
        )
        by_chunk_id = {item.chunk_id: item for item in state["evidence"]}
        by_chunk_id.update(
            {item.chunk_id: item for item in supplemental if item.score >= self._minimum_score}
        )
        return _WorkflowState(
            retrieval_query=state["supplemental_query"],
            evidence=list(by_chunk_id.values()),
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
        issue = self._citation_validation_issue(state["answer"], set(allowed))
        citation_valid = issue == "valid"
        trace = WorkflowTrace(
            retrieval_queries=state["workflow_trace"].retrieval_queries,
            assessments=state["workflow_trace"].assessments,
            citation_validations=(
                *state["workflow_trace"].citation_validations,
                CitationValidationTrace(valid=citation_valid, issue=issue),
            ),
            supplemental_retrieval_attempts=(
                state["workflow_trace"].supplemental_retrieval_attempts
            ),
            citation_repair_attempts=state["workflow_trace"].citation_repair_attempts,
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

    async def _repair_citations(self, state: _WorkflowState) -> _WorkflowState:
        await self._ensure_active(state["run_id"])
        get_stream_writer()(WorkflowStatus(stage="repairing"))
        trace = WorkflowTrace(
            retrieval_queries=state["workflow_trace"].retrieval_queries,
            assessments=state["workflow_trace"].assessments,
            citation_validations=state["workflow_trace"].citation_validations,
            supplemental_retrieval_attempts=(
                state["workflow_trace"].supplemental_retrieval_attempts
            ),
            citation_repair_attempts=1,
        )
        await self._record_trace(state["run_id"], trace)
        repaired = await self._citation_repairer.repair(
            question=state["question"],
            answer=state["answer"],
            evidence=self._candidates(state["run_id"], state["selected_evidence"]),
        )
        return _WorkflowState(
            answer=repaired.strip(),
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
        if "supplemental_query" in state and state["supplemental_attempts"] == 0:
            return "supplement"
        return "generate"

    @staticmethod
    def _after_validation(
        state: _WorkflowState,
    ) -> Literal["answer", "repair", "refuse"]:
        if state["citation_valid"]:
            return "answer"
        return "refuse" if state["repair_attempts"] >= 1 else "repair"

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
    def _citation_validation_issue(
        answer: str,
        allowed: set[str],
    ) -> Literal["empty_answer", "uncited_claim", "unknown_label", "valid"]:
        if not answer.strip():
            return "empty_answer"
        units = [
            unit.strip()
            for unit in re.split(
                r"(?<=[.!?;])\s+|(?<=[\u3002\uff01\uff1f\uff1b])\s*|\n+",
                answer,
            )
            if unit.strip()
        ]
        if not units:
            return "empty_answer"
        for index, unit in enumerate(units):
            labels = _CITATION_LABEL.findall(unit)
            if any(label not in allowed for label in labels):
                return "unknown_label"
            if AnswerWorkflow._is_citation_only(unit):
                continue
            if labels:
                continue
            if index + 1 >= len(units) or not AnswerWorkflow._is_citation_only(
                units[index + 1], allowed
            ):
                return "uncited_claim"
        return "valid"

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
