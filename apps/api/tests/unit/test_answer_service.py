from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from sourcetrace.modules.answers.models import AnswerRun
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

    async def recent_questions(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        *,
        limit: int,
    ) -> list[Question]:
        assert knowledge_base_id == self.question.knowledge_base_id
        assert conversation_id == self.question.conversation_id
        assert limit == 4
        return []


class FailingAnswerRepository:
    def __init__(self) -> None:
        self.committed = False

    async def create_run(self, question: Question, **metadata: str) -> None:
        raise RuntimeError("database rejected answer run")

    async def commit(self) -> None:
        self.committed = True


class UnusedRetrievalService:
    async def resolve_query(self, *, question: str, **kwargs: object) -> str:
        return question

    async def search(self, **kwargs: object) -> list[object]:
        raise AssertionError("retrieval must not start")


class ExplodingRetrievalService:
    async def resolve_query(self, *, question: str, **kwargs: object) -> str:
        return question

    async def search(self, **kwargs: object) -> list[object]:
        raise RuntimeError("unexpected retrieval failure")


class UnusedAnswerGenerator:
    async def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> None:
        raise AssertionError("generation must not start")


class DisconnectRepository:
    def __init__(self) -> None:
        self.cancelled = False
        self.failed = False
        self.rolled_back = False

    async def create_run(self, question: Question, **metadata: str) -> AnswerRun:
        return AnswerRun(
            id=uuid4(),
            question_id=question.id,
            conversation_id=question.conversation_id,
            knowledge_base_id=question.knowledge_base_id,
            status="pending",
            **metadata,
        )

    async def mark_running(self, run_id: UUID) -> bool:
        return True

    async def set_retrieval_query(self, run_id: UUID, query: str) -> bool:
        return True

    async def cancel(self, run_id: UUID) -> bool:
        self.cancelled = True
        return True

    async def fail(self, run_id: UUID, *, code: str, message: str) -> bool:
        assert code == "ANSWER_RUN_UNEXPECTED_ERROR"
        assert message == "Answer generation failed unexpectedly"
        self.failed = True
        return True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def commit(self) -> None:
        return None


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
            query_rewrite_version="follow-up-query-v1",
            workflow_version="linear-grounded-v1",
        ),
        minimum_score=0.5,
        minimum_evidence=1,
        context_question_limit=4,
    )

    with pytest.raises(RuntimeError, match="database rejected answer run"):
        await service.start(
            knowledge_base_id=conversations.question.knowledge_base_id,
            conversation_id=conversations.question.conversation_id,
            content=conversations.question.content,
        )

    assert repository.committed is False


async def test_disconnecting_during_retrieval_cancels_the_run() -> None:
    conversations = StagingConversationService()
    repository = DisconnectRepository()
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
            query_rewrite_version="follow-up-query-v1",
            workflow_version="linear-grounded-v1",
        ),
        minimum_score=0.5,
        minimum_evidence=1,
        context_question_limit=4,
    )
    events = await service.start(
        knowledge_base_id=conversations.question.knowledge_base_id,
        conversation_id=conversations.question.conversation_id,
        content=conversations.question.content,
    )

    first = await anext(events)
    assert first.type == "status"
    assert first.status == "retrieving"
    await events.aclose()

    assert repository.cancelled is True


async def test_unexpected_workflow_error_marks_the_run_failed() -> None:
    conversations = StagingConversationService()
    repository = DisconnectRepository()
    service = AnswerService(
        repository=repository,  # type: ignore[arg-type]
        conversations=conversations,  # type: ignore[arg-type]
        retrieval=ExplodingRetrievalService(),  # type: ignore[arg-type]
        generator=UnusedAnswerGenerator(),  # type: ignore[arg-type]
        metadata=AnswerExecutionMetadata(
            llm_provider="openai-compatible",
            llm_model="gpt-5.6-luna",
            prompt_version="grounded-answer-v1",
            retrieval_version="pgvector-cosine-v1",
            query_rewrite_version="follow-up-query-v1",
            workflow_version="linear-grounded-v1",
        ),
        minimum_score=0.5,
        minimum_evidence=1,
        context_question_limit=4,
    )
    events = await service.start(
        knowledge_base_id=conversations.question.knowledge_base_id,
        conversation_id=conversations.question.conversation_id,
        content=conversations.question.content,
    )

    emitted = [event async for event in events]

    assert [event.type for event in emitted] == ["status", "error"]
    assert emitted[-1].code == "ANSWER_RUN_UNEXPECTED_ERROR"  # type: ignore[union-attr]
    assert repository.rolled_back is True
    assert repository.failed is True
