from datetime import datetime
from uuid import UUID

import pytest

from sourcetrace.modules.knowledge_bases.models import KnowledgeBase
from sourcetrace.modules.knowledge_bases.repository import DuplicateKnowledgeBaseNameError
from sourcetrace.modules.knowledge_bases.service import (
    KnowledgeBaseNameConflictError,
    KnowledgeBaseService,
)


class DuplicateNameRepository:
    def __init__(self) -> None:
        self.rolled_back = False

    async def create(self, name: str) -> KnowledgeBase:
        raise DuplicateKnowledgeBaseNameError

    async def get(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        return None

    async def list_page(
        self,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[KnowledgeBase]:
        return []

    async def delete(self, knowledge_base: KnowledgeBase) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        self.rolled_back = True


async def test_service_exposes_duplicate_names_as_a_business_conflict() -> None:
    repository = DuplicateNameRepository()
    service = KnowledgeBaseService(repository)

    with pytest.raises(KnowledgeBaseNameConflictError):
        await service.create("Research")

    assert repository.rolled_back is True
