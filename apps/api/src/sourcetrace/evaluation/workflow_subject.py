from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from sourcetrace.evaluation.models import (
    EvaluationCase,
    EvaluationDecisionTrace,
    EvaluationObservation,
    ObservedEvidence,
    ObservedEvidenceAssessment,
    ObservedRetrieval,
    ObservedRetrievalCandidate,
)
from sourcetrace.modules.retrieval.service import RetrievedEvidence
from sourcetrace.rag.workflow import (
    WorkflowAnswered,
    WorkflowCancelled,
    WorkflowEvent,
    WorkflowRefused,
    WorkflowRequest,
    WorkflowRunControl,
    WorkflowTrace,
)


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


class WorkflowRunner(Protocol):
    def run(self, request: WorkflowRequest) -> AsyncIterator[WorkflowEvent]: ...


@dataclass(frozen=True, slots=True)
class RecordedRetrieval:
    query: str
    evidence: tuple[RetrievedEvidence, ...]


class RecordingWorkflowRetrieval:
    def __init__(self, retrieval: WorkflowRetrieval) -> None:
        self._retrieval = retrieval
        self._evidence_by_chunk: dict[UUID, RetrievedEvidence] = {}
        self._retrievals: list[RecordedRetrieval] = []

    @property
    def evidence(self) -> tuple[RetrievedEvidence, ...]:
        return tuple(self._evidence_by_chunk.values())

    @property
    def retrievals(self) -> tuple[RecordedRetrieval, ...]:
        return tuple(self._retrievals)

    def reset(self) -> None:
        self._evidence_by_chunk.clear()
        self._retrievals.clear()

    async def resolve_query(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> str:
        return await self._retrieval.resolve_query(
            question=question,
            recent_questions=recent_questions,
        )

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query: str,
    ) -> list[RetrievedEvidence]:
        evidence = await self._retrieval.search(
            knowledge_base_id=knowledge_base_id,
            query=query,
        )
        self._retrievals.append(RecordedRetrieval(query=query, evidence=tuple(evidence)))
        self._evidence_by_chunk.update({item.chunk_id: item for item in evidence})
        return evidence


class RecordingWorkflowRunControl:
    def __init__(self) -> None:
        self._trace = WorkflowTrace()

    @property
    def trace(self) -> WorkflowTrace:
        return self._trace

    def reset(self) -> None:
        self._trace = WorkflowTrace()

    async def record_retrieval_query(self, run_id: UUID, query: str) -> bool:
        return True

    async def record_workflow_trace(self, run_id: UUID, trace: WorkflowTrace) -> bool:
        self._trace = trace
        return True

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        return False


type WorkflowFactory = Callable[
    [RecordingWorkflowRetrieval, WorkflowRunControl],
    WorkflowRunner,
]


class WorkflowEvaluationSubject:
    def __init__(
        self,
        *,
        retrieval: WorkflowRetrieval,
        workflow_factory: WorkflowFactory,
        knowledge_base_id: UUID,
    ) -> None:
        self._retrieval = RecordingWorkflowRetrieval(retrieval)
        self._run_control = RecordingWorkflowRunControl()
        self._workflow = workflow_factory(self._retrieval, self._run_control)
        self._knowledge_base_id = knowledge_base_id

    async def evaluate(self, case: EvaluationCase) -> EvaluationObservation:
        self._retrieval.reset()
        self._run_control.reset()
        final: WorkflowAnswered | WorkflowRefused | WorkflowCancelled | None = None
        request = WorkflowRequest(
            run_id=uuid4(),
            knowledge_base_id=self._knowledge_base_id,
            question=case.question,
            recent_questions=(),
        )
        async for event in self._workflow.run(request):
            if isinstance(event, (WorkflowAnswered, WorkflowRefused, WorkflowCancelled)):
                final = event
        if final is None:
            raise RuntimeError("answer workflow completed without a final event")

        retrieved = tuple(self._to_observed(item) for item in self._retrieval.evidence)
        decision_trace = self._to_decision_trace()
        if isinstance(final, WorkflowAnswered):
            return EvaluationObservation(
                outcome="answered",
                answer=final.answer,
                retrieved_evidence=retrieved,
                citations=tuple(self._to_observed(item) for item in final.evidence),
                decision_trace=decision_trace,
            )
        return EvaluationObservation(
            outcome="refused" if isinstance(final, WorkflowRefused) else "error",
            answer=None,
            retrieved_evidence=retrieved,
            citations=(),
            decision_trace=decision_trace,
        )

    @staticmethod
    def _to_observed(evidence: RetrievedEvidence) -> ObservedEvidence:
        return ObservedEvidence(
            document_version_id=evidence.document_version_id,
            page_number=evidence.page_number,
            text=evidence.text,
        )

    def _to_decision_trace(self) -> EvaluationDecisionTrace:
        trace = self._run_control.trace
        return EvaluationDecisionTrace(
            retrievals=tuple(
                ObservedRetrieval(
                    query=retrieval.query,
                    candidates=tuple(
                        ObservedRetrievalCandidate(
                            chunk_id=evidence.chunk_id,
                            document_version_id=evidence.document_version_id,
                            page_number=evidence.page_number,
                            score=evidence.score,
                        )
                        for evidence in retrieval.evidence
                    ),
                )
                for retrieval in self._retrieval.retrievals
            ),
            assessments=tuple(
                ObservedEvidenceAssessment(
                    sufficient=assessment.sufficient,
                    selected_chunk_ids=assessment.selected_chunk_ids,
                    supplemental_query=assessment.supplemental_query,
                )
                for assessment in trace.assessments
            ),
            supplemental_retrieval_attempts=trace.supplemental_retrieval_attempts,
            citation_repair_attempts=trace.citation_repair_attempts,
        )
