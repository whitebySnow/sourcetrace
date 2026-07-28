from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.core.config import get_settings
from sourcetrace.db.session import get_session
from sourcetrace.modules.conversations.repository import ConversationRepository
from sourcetrace.modules.conversations.service import ConversationService
from sourcetrace.modules.documents.storage import LocalDocumentStorage
from sourcetrace.modules.knowledge_bases.repository import KnowledgeBaseRepository
from sourcetrace.modules.knowledge_bases.service import KnowledgeBaseService


def get_knowledge_base_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBaseService:
    return KnowledgeBaseService(
        KnowledgeBaseRepository(session),
        resource_cleaner=LocalDocumentStorage(get_settings().upload_dir),
    )


def get_conversation_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    knowledge_bases: Annotated[
        KnowledgeBaseService,
        Depends(get_knowledge_base_service),
    ],
) -> ConversationService:
    return ConversationService(
        ConversationRepository(session),
        knowledge_bases,
    )
