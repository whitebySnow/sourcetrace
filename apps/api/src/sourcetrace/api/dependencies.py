from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.core.config import get_settings
from sourcetrace.db.session import get_session
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
