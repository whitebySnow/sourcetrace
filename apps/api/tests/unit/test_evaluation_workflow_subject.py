from collections.abc import AsyncIterator, Sequence
from uuid import UUID, uuid4

from sourcetrace.evaluation.models import EvaluationCase
from sourcetrace.evaluation.workflow_subject import WorkflowEvaluationSubject
from sourcetrace.modules.retrieval.service import RetrievedEvidence
from sourcetrace.rag.ports import EvidenceDecision, RetrievalCandidate
from sourcetrace.rag.workflow import AnswerWorkflow


class StaticRetrieval:
    def __init__(self, evidence: RetrievedEvidence) -> None:
        self.evidence = evidence

    async def resolve_query(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> str:
        return question

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query: str,
    ) -> list[RetrievedEvidence]:
        return [self.evidence]


class SelectingAssessor:
    def __init__(self, chunk_id: UUID) -> None:
        self.chunk_id = chunk_id

    async def assess(self, **kwargs: object) -> EvidenceDecision:
        return EvidenceDecision(
            sufficient=True,
            selected_chunk_ids=(str(self.chunk_id),),
            supplemental_query=None,
        )


class RefusingAssessor:
    async def assess(self, **kwargs: object) -> EvidenceDecision:
        return EvidenceDecision(
            sufficient=False,
            selected_chunk_ids=(),
            supplemental_query=None,
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
    assessment = observation.decision_trace.assessments[0]
    assert assessment.sufficient is False
    assert assessment.selected_chunk_ids == ()
