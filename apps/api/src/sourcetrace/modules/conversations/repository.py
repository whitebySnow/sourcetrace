from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.modules.conversations.models import Conversation, Question


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, knowledge_base_id: UUID, title: str) -> Conversation:
        conversation = Conversation(knowledge_base_id=knowledge_base_id, title=title)
        self._session.add(conversation)
        await self._session.flush()
        await self._session.refresh(conversation)
        return conversation

    async def get(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
    ) -> Conversation | None:
        return await self._session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.knowledge_base_id == knowledge_base_id,
            )
        )

    async def list_page(
        self,
        knowledge_base_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[Conversation]:
        statement = select(Conversation).where(
            Conversation.knowledge_base_id == knowledge_base_id
        )
        if after is not None:
            created_at, conversation_id = after
            statement = statement.where(
                or_(
                    Conversation.created_at < created_at,
                    and_(
                        Conversation.created_at == created_at,
                        Conversation.id < conversation_id,
                    ),
                )
            )
        result = await self._session.scalars(
            statement.order_by(
                Conversation.created_at.desc(),
                Conversation.id.desc(),
            ).limit(limit)
        )
        return list(result)

    async def create_question(
        self,
        conversation_id: UUID,
        knowledge_base_id: UUID,
        content: str,
    ) -> Question:
        question = Question(
            conversation_id=conversation_id,
            knowledge_base_id=knowledge_base_id,
            content=content,
        )
        self._session.add(question)
        await self._session.flush()
        await self._session.refresh(question)
        return question

    async def list_question_page(
        self,
        conversation_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[Question]:
        statement = select(Question).where(Question.conversation_id == conversation_id)
        if after is not None:
            created_at, question_id = after
            statement = statement.where(
                or_(
                    Question.created_at > created_at,
                    and_(
                        Question.created_at == created_at,
                        Question.id > question_id,
                    ),
                )
            )
        result = await self._session.scalars(
            statement.order_by(Question.created_at, Question.id).limit(limit)
        )
        return list(result)

    async def commit(self) -> None:
        await self._session.commit()
