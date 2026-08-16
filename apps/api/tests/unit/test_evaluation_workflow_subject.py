from collections.abc import AsyncIterator, Sequence
from uuid import UUID, uuid4

import pytest

from sourcetrace.evaluation.models import EvaluationCase
from sourcetrace.evaluation.workflow_subject import (
    EvaluationExecutionFailure,
    WorkflowEvaluationSubject,
)
from sourcetrace.modules.retrieval.service import (
    FusedRetrievalCandidate,
    QueryRetrievalResult,
    RankedRetrievalCandidate,
    RetrievalPlan,
    RetrievalResult,
    RetrievedEvidence,
)
from sourcetrace.rag.llm import LlmProviderError
from sourcetrace.rag.ports import EvidenceDecision, RetrievalCandidate
from sourcetrace.rag.workflow import AnswerWorkflow
from tests.helpers import CitationPreservingClaimSupportVerifier, PreserveOrderReranker


class StaticRetrieval:
    def __init__(self, evidence: RetrievedEvidence) -> None:
        self.evidence = evidence

    async def resolve_plan(
        self,
        *,
        knowledge_base_id: UUID,
        question: str,
        recent_questions: Sequence[str],
    ) -> RetrievalPlan:
        return RetrievalPlan("bounded-multi-query-v1", (question,))

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        queries: Sequence[str],
    ) -> RetrievalResult:
        query_results = tuple(
            QueryRetrievalResult(
                query=query,
                candidates=(
                    RankedRetrievalCandidate(
                        rank=1,
                        evidence=self.evidence,
                        reranker_score=1.0,
                        reranked_rank=1,
                        selected_for_query_coverage=True,
                    ),
                ),
            )
            for query in queries
        )
        return RetrievalResult(
            evidence=(self.evidence,),
            primary_evidence=(self.evidence,),
            query_results=query_results,
            fused_candidates=(
                FusedRetrievalCandidate(
                    evidence=self.evidence,
                    fused_score=sum(1 / 61 for _query in queries),
                    best_raw_score=self.evidence.score,
                    reranker_score=1.0,
                    reranked_rank=1,
                    selected_as_primary=True,
                ),
            ),
            rrf_rank_constant=60,
            reranker_identity=PreserveOrderReranker.identity,
        )


class SelectingAssessor:
    def __init__(self, chunk_id: UUID) -> None:
        self.chunk_id = chunk_id

    async def assess(self, **kwargs: object) -> EvidenceDecision:
        return EvidenceDecision(
            sufficient=True,
            selected_chunk_ids=(str(self.chunk_id),),
            supplemental_queries=(),
        )


class RefusingAssessor:
    async def assess(self, **kwargs: object) -> EvidenceDecision:
        return EvidenceDecision(
            sufficient=False,
            selected_chunk_ids=(),
            supplemental_queries=(),
        )


class InvalidResponseAssessor:
    async def assess(self, **kwargs: object) -> EvidenceDecision:
        raise LlmProviderError(
            "LLM_INVALID_RESPONSE",
            "Language model returned an invalid response",
            reason="provider_structured_invalid_json",
        )


class CitingGenerator:
    async def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        yield f"Bounded workflows stop after one retry [{evidence[0].citation_id}]"


class UnusedRepairer:
    async def repair(self, **kwargs: object) -> str:
        raise AssertionError("valid citations must not be repaired")


async def test_workflow_subject_captures_retrieval_and_final_citations() -> None:
    knowledge_base_id = uuid4()
    evidence = RetrievedEvidence(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        document_name="agents.pdf",
        storage_key="knowledge/agents.pdf",
        page_number=3,
        text="Bounded workflows stop after one retry.",
        score=0.9,
    )
    subject = WorkflowEvaluationSubject(
        retrieval=StaticRetrieval(evidence),
        workflow_factory=lambda retrieval, run_control: AnswerWorkflow(
            retrieval=retrieval,
                assessor=SelectingAssessor(evidence.chunk_id),
                generator=CitingGenerator(),
                claim_support_verifier=CitationPreservingClaimSupportVerifier(),
            citation_repairer=UnusedRepairer(),
            run_control=run_control,
            minimum_score=0.5,
            minimum_evidence=1,
        ),
        knowledge_base_id=knowledge_base_id,
    )
    case = EvaluationCase.model_validate(
        {
            "id": "direct-001",
            "category": "direct",
            "question": "How many retries are allowed?",
            "expected": {
                "outcome": "answered",
                "reference_answer": "One retry.",
                "evidence": [
                    {
                        "document_version_id": evidence.document_version_id,
                        "page_number": 3,
                        "text": "one retry",
                    }
                ],
            },
        }
    )

    observation = await subject.evaluate(case)

    assert observation.outcome == "answered"
    assert observation.answer is not None
    assert [item.document_version_id for item in observation.retrieved_evidence] == [
        evidence.document_version_id
    ]
    assert [item.document_version_id for item in observation.citations] == [
        evidence.document_version_id
    ]


async def test_workflow_subject_records_trace_for_retrieved_but_refused_evidence() -> None:
    knowledge_base_id = uuid4()
    evidence = RetrievedEvidence(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        document_name="rag.pdf",
        storage_key="knowledge/rag.pdf",
        page_number=1,
        text="RAG combines parametric and non-parametric memory.",
        score=0.9,
    )
    subject = WorkflowEvaluationSubject(
        retrieval=StaticRetrieval(evidence),
        workflow_factory=lambda retrieval, run_control: AnswerWorkflow(
            retrieval=retrieval,
            assessor=RefusingAssessor(),
            generator=CitingGenerator(),
            citation_repairer=UnusedRepairer(),
            run_control=run_control,
            minimum_score=0.5,
            minimum_evidence=1,
        ),
        knowledge_base_id=knowledge_base_id,
    )
    case = EvaluationCase.model_validate(
        {
            "id": "direct-refusal-001",
            "category": "direct",
            "question": "Which memories does RAG combine?",
            "expected": {
                "outcome": "answered",
                "reference_answer": "Parametric and non-parametric memory.",
                "evidence": [
                    {
                        "document_version_id": evidence.document_version_id,
                        "page_number": 1,
                        "text": "parametric and non-parametric memory",
                    }
                ],
            },
        }
    )

    observation = await subject.evaluate(case)

    assert observation.outcome == "refused"
    assert observation.decision_trace is not None
    assert observation.decision_trace.retrievals[0].query == case.question
    candidate = observation.decision_trace.retrievals[0].candidates[0]
    assert candidate.chunk_id == evidence.chunk_id
    assert candidate.score == 0.9
    assert candidate.raw_rank == 1
    assert observation.decision_trace.retrieval_plan_version == ("bounded-multi-query-v1")
    retrieval_round = observation.decision_trace.retrieval_rounds[0]
    assert retrieval_round.round_number == 1
    assert retrieval_round.queries == (case.question,)
    assert retrieval_round.query_results[0].candidates[0].raw_rank == 1
    assert retrieval_round.query_results[0].candidates[0].reranker_score == 1.0
    assert retrieval_round.query_results[0].candidates[0].reranked_rank == 1
    assert (
        retrieval_round.query_results[0]
        .candidates[0]
        .selected_for_query_coverage
        is True
    )
    assert retrieval_round.fused_candidates[0].chunk_id == evidence.chunk_id
    assert retrieval_round.fused_candidates[0].selected_as_primary is True
    assert retrieval_round.fused_candidates[0].reranker_score == 1.0
    assert retrieval_round.fused_candidates[0].reranked_rank == 1
    assert retrieval_round.reranker is not None
    assert retrieval_round.reranker.model == "preserve-order"
    assert retrieval_round.reranker.revision == "v1"
    assert retrieval_round.final_evidence_chunk_ids == (evidence.chunk_id,)
    assert retrieval_round.rrf_rank_constant == 60
    assert observation.retrieved_evidence[0].chunk_id == evidence.chunk_id
    assessment = observation.decision_trace.assessments[0]
    assert assessment.sufficient is False
    assert assessment.selected_chunk_ids == ()
    assert observation.decision_trace.citation_validations == ()


async def test_workflow_subject_classifies_provider_failure_without_an_observation() -> None:
    evidence = RetrievedEvidence(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        document_name="agents.pdf",
        storage_key="knowledge/agents.pdf",
        page_number=3,
        text="Bounded workflows stop after one retry.",
        score=0.9,
    )
    subject = WorkflowEvaluationSubject(
        retrieval=StaticRetrieval(evidence),
        workflow_factory=lambda retrieval, run_control: AnswerWorkflow(
            retrieval=retrieval,
            assessor=InvalidResponseAssessor(),
            generator=CitingGenerator(),
            citation_repairer=UnusedRepairer(),
            run_control=run_control,
            minimum_score=0.5,
            minimum_evidence=1,
        ),
        knowledge_base_id=uuid4(),
    )
    case = EvaluationCase.model_validate(
        {
            "id": "provider-failure-001",
            "category": "direct",
            "question": "How many retries are allowed?",
            "expected": {
                "outcome": "answered",
                "reference_answer": "One retry.",
                "evidence": [
                    {
                        "document_version_id": str(evidence.document_version_id),
                        "page_number": 3,
                        "text": "one retry",
                    }
                ],
            },
        }
    )

    with pytest.raises(EvaluationExecutionFailure) as error:
        await subject.evaluate(case)

    assert error.value.case_id == case.id
    assert error.value.phase == "assessing"
    assert error.value.error_code == "LLM_INVALID_RESPONSE"
    assert error.value.error_reason == "provider_structured_invalid_json"
