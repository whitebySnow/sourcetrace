from collections.abc import AsyncIterator, Sequence
from uuid import UUID, uuid4

from sourcetrace.evaluation.models import EvaluationCase
from sourcetrace.evaluation.workflow_subject import WorkflowEvaluationSubject
from sourcetrace.modules.retrieval.service import RetrievedEvidence
from sourcetrace.rag.ports import EvidenceDecision, RetrievalCandidate
from sourcetrace.rag.workflow import AnswerWorkflow, WorkflowTrace


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


class ActiveRunControl:
    async def record_retrieval_query(self, run_id: UUID, query: str) -> bool:
        return True

    async def record_workflow_trace(self, run_id: UUID, trace: WorkflowTrace) -> bool:
        return True

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        return False


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
        workflow_factory=lambda retrieval: AnswerWorkflow(
            retrieval=retrieval,
            assessor=SelectingAssessor(evidence.chunk_id),
            generator=CitingGenerator(),
            citation_repairer=UnusedRepairer(),
            run_control=ActiveRunControl(),
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
