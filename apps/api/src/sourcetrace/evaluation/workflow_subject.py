from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from sourcetrace.evaluation.models import (
    EvaluationCase,
    EvaluationDecisionTrace,
    EvaluationObservation,
    ObservedCitationValidation,
    ObservedEvidence,
    ObservedEvidenceAssessment,
    ObservedFusedCandidateTrace,
    ObservedQueryCandidateTrace,
    ObservedQueryRetrievalTrace,
    ObservedRerankerTrace,
    ObservedRetrieval,
    ObservedRetrievalCandidate,
    ObservedRetrievalRoundTrace,
)
from sourcetrace.modules.retrieval.service import (
    RetrievalPlan,
    RetrievalResult,
    RetrievedEvidence,
)
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
    async def resolve_plan(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> RetrievalPlan: ...

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        queries: Sequence[str],
    ) -> RetrievalResult: ...


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

    async def resolve_plan(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> RetrievalPlan:
        return await self._retrieval.resolve_plan(
            question=question,
            recent_questions=recent_questions,
        )

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        queries: Sequence[str],
    ) -> RetrievalResult:
        result = await self._retrieval.search(
            knowledge_base_id=knowledge_base_id,
            queries=queries,
        )
        recorded_queries = {item.query for item in self._retrievals}
        for query_result in result.query_results:
            if query_result.query not in recorded_queries:
                self._retrievals.append(
                    RecordedRetrieval(
                        query=query_result.query,
                        evidence=tuple(candidate.evidence for candidate in query_result.candidates),
                    )
                )
                recorded_queries.add(query_result.query)
        self._evidence_by_chunk.update({item.chunk_id: item for item in result.evidence})
        return result


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
                            raw_rank=rank,
                        )
                        for rank, evidence in enumerate(retrieval.evidence, start=1)
                    ),
                )
                for retrieval in self._retrieval.retrievals
            ),
            retrieval_plan_version=trace.retrieval_plan_version,
            retrieval_rounds=tuple(
                ObservedRetrievalRoundTrace(
                    round_number=retrieval_round.round_number,
                    queries=retrieval_round.queries,
                    query_results=tuple(
                        ObservedQueryRetrievalTrace(
                            query=query_result.query,
                            candidates=tuple(
                                ObservedQueryCandidateTrace(
                                    chunk_id=UUID(candidate.chunk_id),
                                    raw_rank=candidate.raw_rank,
                                    raw_cosine_score=candidate.raw_cosine_score,
                                )
                                for candidate in query_result.candidates
                            ),
                        )
                        for query_result in retrieval_round.query_results
                    ),
                    fused_candidates=tuple(
                        ObservedFusedCandidateTrace(
                            chunk_id=UUID(candidate.chunk_id),
                            fused_score=candidate.fused_score,
                            best_raw_cosine_score=(candidate.best_raw_cosine_score),
                            reranker_score=candidate.reranker_score,
                            reranked_rank=candidate.reranked_rank,
                            selected_as_primary=candidate.selected_as_primary,
                        )
                        for candidate in retrieval_round.fused_candidates
                    ),
                    final_evidence_chunk_ids=tuple(
                        UUID(chunk_id) for chunk_id in retrieval_round.final_evidence_chunk_ids
                    ),
                    rrf_rank_constant=retrieval_round.rrf_rank_constant,
                    reranker=(
                        ObservedRerankerTrace(
                            provider=retrieval_round.reranker.provider,
                            model=retrieval_round.reranker.model,
                            revision=retrieval_round.reranker.revision,
                            config_version=retrieval_round.reranker.config_version,
                        )
                        if retrieval_round.reranker is not None
                        else None
                    ),
                )
                for retrieval_round in trace.retrieval_rounds
            ),
            assessments=tuple(
                ObservedEvidenceAssessment(
                    sufficient=assessment.sufficient,
                    selected_chunk_ids=assessment.selected_chunk_ids,
                    supplemental_query=assessment.supplemental_query,
                )
                for assessment in trace.assessments
            ),
            citation_validations=tuple(
                ObservedCitationValidation(
                    valid=validation.valid,
                    issue=validation.issue,
                )
                for validation in trace.citation_validations
            ),
            supplemental_retrieval_attempts=trace.supplemental_retrieval_attempts,
            citation_repair_attempts=trace.citation_repair_attempts,
        )
