from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sourcetrace.modules.knowledge_bases.models import KnowledgeBase
from sourcetrace.modules.knowledge_bases.repository import DuplicateKnowledgeBaseNameError


class KnowledgeBaseNotFoundError(Exception):
    pass


class KnowledgeBaseNameConflictError(Exception):
    pass


class InvalidKnowledgeBaseCursorError(Exception):
    pass


@dataclass(frozen=True)
class KnowledgeBasePage:
    items: list[KnowledgeBase]
    next_cursor: str | None


class KnowledgeBaseRepositoryPort(Protocol):
    async def create(self, name: str) -> KnowledgeBase: ...

    async def get(self, knowledge_base_id: UUID) -> KnowledgeBase | None: ...

    async def list_page(
        self,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[KnowledgeBase]: ...

    async def delete(self, knowledge_base: KnowledgeBase) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class KnowledgeBaseService:
    def __init__(self, repository: KnowledgeBaseRepositoryPort) -> None:
        self._repository = repository

    async def create(self, name: str) -> KnowledgeBase:
        try:
            knowledge_base = await self._repository.create(name)
        except DuplicateKnowledgeBaseNameError as error:
            await self._repository.rollback()
            raise KnowledgeBaseNameConflictError from error
        await self._repository.commit()
        return knowledge_base

    async def get(self, knowledge_base_id: UUID) -> KnowledgeBase:
        knowledge_base = await self._repository.get(knowledge_base_id)
        if knowledge_base is None:
            raise KnowledgeBaseNotFoundError
        return knowledge_base

    async def list(self, *, limit: int, cursor: str | None) -> KnowledgeBasePage:
        after = self._decode_cursor(cursor) if cursor else None
        rows = await self._repository.list_page(limit=limit + 1, after=after)
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = self._encode_cursor(items[-1]) if has_more else None
        return KnowledgeBasePage(items=items, next_cursor=next_cursor)

    async def delete(self, knowledge_base_id: UUID) -> None:
        knowledge_base = await self.get(knowledge_base_id)
        await self._repository.delete(knowledge_base)
        await self._repository.commit()

    @staticmethod
    def _encode_cursor(knowledge_base: KnowledgeBase) -> str:
        value = f"{knowledge_base.created_at.isoformat()}|{knowledge_base.id}"
        return urlsafe_b64encode(value.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = urlsafe_b64decode(padded.encode()).decode()
            created_at_value, knowledge_base_id_value = value.split("|", maxsplit=1)
            created_at = datetime.fromisoformat(created_at_value)
            if created_at.tzinfo is None:
                raise ValueError
            return created_at, UUID(knowledge_base_id_value)
        except (ValueError, UnicodeDecodeError) as error:
            raise InvalidKnowledgeBaseCursorError from error
