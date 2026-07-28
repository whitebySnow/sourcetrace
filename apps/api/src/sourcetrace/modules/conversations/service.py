from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sourcetrace.modules.conversations.models import Conversation, Question


class ConversationNotFoundError(Exception):
    pass


class InvalidConversationCursorError(Exception):
    pass


def _encode_cursor(created_at: datetime, resource_id: UUID) -> str:
    value = f"{created_at.isoformat()}|{resource_id}"
    return urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = urlsafe_b64decode(padded.encode()).decode()
        created_at_value, resource_id_value = value.split("|", maxsplit=1)
        created_at = datetime.fromisoformat(created_at_value)
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, UUID(resource_id_value)
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidConversationCursorError from error


@dataclass(frozen=True)
class ConversationPage:
    items: list[Conversation]
    next_cursor: str | None


@dataclass(frozen=True)
class QuestionPage:
    items: list[Question]
    next_cursor: str | None


class KnowledgeBaseLookupPort(Protocol):
    async def get(self, knowledge_base_id: UUID) -> object: ...


class ConversationRepositoryPort(Protocol):
    async def create(self, knowledge_base_id: UUID, title: str) -> Conversation: ...

    async def get(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
    ) -> Conversation | None: ...

    async def list_page(
        self,
        knowledge_base_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[Conversation]: ...

    async def create_question(
        self,
        conversation_id: UUID,
        knowledge_base_id: UUID,
        content: str,
    ) -> Question: ...

    async def list_question_page(
        self,
        conversation_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[Question]: ...

    async def commit(self) -> None: ...


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepositoryPort,
        knowledge_bases: KnowledgeBaseLookupPort,
    ) -> None:
        self._repository = repository
        self._knowledge_bases = knowledge_bases

    async def create(self, knowledge_base_id: UUID, title: str) -> Conversation:
        await self._knowledge_bases.get(knowledge_base_id)
        conversation = await self._repository.create(knowledge_base_id, title)
        await self._repository.commit()
        return conversation

    async def get(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
    ) -> Conversation:
        conversation = await self._repository.get(knowledge_base_id, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError
        return conversation

    async def list(
        self,
        knowledge_base_id: UUID,
        *,
        limit: int,
        cursor: str | None,
    ) -> ConversationPage:
        await self._knowledge_bases.get(knowledge_base_id)
        after = _decode_cursor(cursor) if cursor else None
        rows = await self._repository.list_page(
            knowledge_base_id,
            limit=limit + 1,
            after=after,
        )
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = (
            _encode_cursor(items[-1].created_at, items[-1].id) if has_more else None
        )
        return ConversationPage(items=items, next_cursor=next_cursor)

    async def create_question(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        content: str,
    ) -> Question:
        await self.get(knowledge_base_id, conversation_id)
        question = await self._repository.create_question(
            conversation_id,
            knowledge_base_id,
            content,
        )
        await self._repository.commit()
        return question

    async def list_questions(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        *,
        limit: int,
        cursor: str | None,
    ) -> QuestionPage:
        await self.get(knowledge_base_id, conversation_id)
        after = _decode_cursor(cursor) if cursor else None
        rows = await self._repository.list_question_page(
            conversation_id,
            limit=limit + 1,
            after=after,
        )
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = (
            _encode_cursor(items[-1].created_at, items[-1].id) if has_more else None
        )
        return QuestionPage(items=items, next_cursor=next_cursor)
