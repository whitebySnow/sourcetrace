from datetime import datetime
from uuid import UUID, uuid4

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


class DeletionRepository(DuplicateNameRepository):
    def __init__(self) -> None:
        super().__init__()
        self.knowledge_base = KnowledgeBase(id=uuid4(), name="Research")
        self.deleted = False
        self.committed = False

    async def get(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        return self.knowledge_base

    async def delete(self, knowledge_base: KnowledgeBase) -> None:
        self.deleted = True

    async def commit(self) -> None:
        self.committed = True


class StagedDeletion:
    def __init__(self) -> None:
        self.finalized = False
        self.restored = False

    async def finalize(self) -> None:
        self.finalized = True

    async def restore(self) -> None:
        self.restored = True


class FinalizeFailingStagedDeletion(StagedDeletion):
    async def finalize(self) -> None:
        raise OSError("storage unavailable")


class FailingResourceCleaner:
    async def stage_knowledge_base_deletion(
        self,
        knowledge_base_id: UUID,
    ) -> StagedDeletion:
        raise OSError("storage unavailable")


class ResourceCleaner:
    def __init__(self, staged_deletion: StagedDeletion) -> None:
        self.staged_deletion = staged_deletion

    async def stage_knowledge_base_deletion(
        self,
        knowledge_base_id: UUID,
    ) -> StagedDeletion:
        return self.staged_deletion


class CommitFailingDeletionRepository(DeletionRepository):
    async def commit(self) -> None:
        raise RuntimeError("database unavailable")


async def test_service_exposes_duplicate_names_as_a_business_conflict() -> None:
    repository = DuplicateNameRepository()
    service = KnowledgeBaseService(repository)

    with pytest.raises(KnowledgeBaseNameConflictError):
        await service.create("Research")

    assert repository.rolled_back is True


async def test_cleanup_failure_keeps_knowledge_base_available_for_retry() -> None:
    repository = DeletionRepository()
    service = KnowledgeBaseService(repository, FailingResourceCleaner())

    with pytest.raises(OSError, match="storage unavailable"):
        await service.delete(repository.knowledge_base.id)

    assert repository.deleted is False
    assert repository.committed is False


async def test_database_failure_restores_staged_source_files() -> None:
    repository = CommitFailingDeletionRepository()
    staged_deletion = StagedDeletion()
    service = KnowledgeBaseService(repository, ResourceCleaner(staged_deletion))

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.delete(repository.knowledge_base.id)

    assert repository.rolled_back is True
    assert staged_deletion.restored is True
    assert staged_deletion.finalized is False


async def test_finalize_failure_is_deferred_after_database_commit() -> None:
    repository = DeletionRepository()
    staged_deletion = FinalizeFailingStagedDeletion()
    service = KnowledgeBaseService(repository, ResourceCleaner(staged_deletion))

    await service.delete(repository.knowledge_base.id)

    assert repository.deleted is True
    assert repository.committed is True
    assert staged_deletion.restored is False
