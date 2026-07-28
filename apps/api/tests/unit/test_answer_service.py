from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from sourcetrace.modules.answers.service import AnswerExecutionMetadata, AnswerService
from sourcetrace.modules.conversations.models import Question
from sourcetrace.rag.ports import RetrievalCandidate


class StagingConversationService:
    def __init__(self) -> None:
        self.question = Question(
            id=uuid4(),
            conversation_id=uuid4(),
            knowledge_base_id=uuid4(),
            content="How are vectors stored?",
        )

    async def stage_question(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        content: str,
    ) -> Question:
        assert knowledge_base_id == self.question.knowledge_base_id
        assert conversation_id == self.question.conversation_id
        assert content == self.question.content
        return self.question


class FailingAnswerRepository:
    def __init__(self) -> None:
        self.committed = False

    async def create_run(self, question: Question, **metadata: str) -> None:
        raise RuntimeError("database rejected answer run")

    async def commit(self) -> None:
        self.committed = True


class UnusedRetrievalService:
    async def search(self, **kwargs: object) -> list[object]:
        raise AssertionError("retrieval must not start")


class UnusedAnswerGenerator:
    async def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> None:
        raise AssertionError("generation must not start")


async def test_question_is_not_committed_when_answer_run_creation_fails() -> None:
    conversations = StagingConversationService()
    repository = FailingAnswerRepository()
    service = AnswerService(
        repository=repository,  # type: ignore[arg-type]
        conversations=conversations,  # type: ignore[arg-type]
        retrieval=UnusedRetrievalService(),  # type: ignore[arg-type]
        generator=UnusedAnswerGenerator(),  # type: ignore[arg-type]
        metadata=AnswerExecutionMetadata(
            llm_provider="openai-compatible",
            llm_model="gpt-5.6-luna",
            prompt_version="grounded-answer-v1",
            retrieval_version="pgvector-cosine-v1",
            workflow_version="linear-grounded-v1",
        ),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    with pytest.raises(RuntimeError, match="database rejected answer run"):
        await service.start(
            knowledge_base_id=conversations.question.knowledge_base_id,
            conversation_id=conversations.question.conversation_id,
            content=conversations.question.content,
        )

    assert repository.committed is False
