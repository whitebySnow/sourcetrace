from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.modules.knowledge_bases.models import KnowledgeBase


class DuplicateKnowledgeBaseNameError(Exception):
    pass


class KnowledgeBaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, name: str) -> KnowledgeBase:
        knowledge_base = KnowledgeBase(name=name)
        self._session.add(knowledge_base)
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise DuplicateKnowledgeBaseNameError from error
        await self._session.refresh(knowledge_base)
        return knowledge_base

    async def get(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        return await self._session.get(KnowledgeBase, knowledge_base_id)

    async def list_page(
        self,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[KnowledgeBase]:
        statement = select(KnowledgeBase)
        if after is not None:
            created_at, knowledge_base_id = after
            statement = statement.where(
                or_(
                    KnowledgeBase.created_at < created_at,
                    and_(
                        KnowledgeBase.created_at == created_at,
                        KnowledgeBase.id < knowledge_base_id,
                    ),
                )
            )
        statement = statement.order_by(
            KnowledgeBase.created_at.desc(),
            KnowledgeBase.id.desc(),
        ).limit(limit)
        result = await self._session.scalars(statement)
        return list(result)

    async def delete(self, knowledge_base: KnowledgeBase) -> None:
        await self._session.delete(knowledge_base)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
