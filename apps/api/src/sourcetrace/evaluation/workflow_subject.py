from collections.abc import AsyncIterator, Sequence
from typing import Protocol
from uuid import UUID, uuid4

from sourcetrace.evaluation.models import (
    EvaluationCase,
    EvaluationObservation,
    ObservedEvidence,
)
from sourcetrace.modules.retrieval.service import RetrievedEvidence
from sourcetrace.rag.workflow import (
    WorkflowAnswered,
    WorkflowCancelled,
    WorkflowEvent,
    WorkflowRefused,
    WorkflowRequest,
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


class RecordingWorkflowRetrieval:
    def __init__(self, retrieval: WorkflowRetrieval) -> None:
        self._retrieval = retrieval
        self._evidence_by_chunk: dict[UUID, RetrievedEvidence] = {}

    @property
    def evidence(self) -> tuple[RetrievedEvidence, ...]:
        return tuple(self._evidence_by_chunk.values())

    def reset(self) -> None:
        self._evidence_by_chunk.clear()

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
        self._evidence_by_chunk.update({item.chunk_id: item for item in evidence})
        return evidence


class WorkflowEvaluationSubject:
    def __init__(
        self,
        *,
        workflow: WorkflowRunner,
        retrieval: RecordingWorkflowRetrieval,
        knowledge_base_id: UUID,
    ) -> None:
        self._workflow = workflow
        self._retrieval = retrieval
        self._knowledge_base_id = knowledge_base_id

    async def evaluate(self, case: EvaluationCase) -> EvaluationObservation:
        self._retrieval.reset()
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
        if isinstance(final, WorkflowAnswered):
            return EvaluationObservation(
                outcome="answered",
                answer=final.answer,
                retrieved_evidence=retrieved,
                citations=tuple(self._to_observed(item) for item in final.evidence),
            )
        return EvaluationObservation(
            outcome="refused" if isinstance(final, WorkflowRefused) else "error",
            answer=None,
            retrieved_evidence=retrieved,
            citations=(),
        )

    @staticmethod
    def _to_observed(evidence: RetrievedEvidence) -> ObservedEvidence:
        return ObservedEvidence(
            document_version_id=evidence.document_version_id,
            page_number=evidence.page_number,
            text=evidence.text,
        )
