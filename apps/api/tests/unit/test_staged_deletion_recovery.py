from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import UUID

from sourcetrace.api.recovery import reconcile_staged_document_deletions
from sourcetrace.modules.documents.storage import LocalDocumentStorage


class KnowledgeBaseLookup:
    def __init__(self, *, exists: bool) -> None:
        self._exists = exists

    async def get(self, knowledge_base_id: UUID) -> object | None:
        return object() if self._exists else None


async def stored_pdf(storage: LocalDocumentStorage, knowledge_base_id: UUID) -> str:
    with SpooledTemporaryFile(mode="w+b") as content:
        content.write(b"%PDF-1.7\noriginal")
        content.seek(0)
        return await storage.store(knowledge_base_id, "a" * 64, content)


async def test_recovery_restores_files_when_knowledge_base_still_exists(
    tmp_path: Path,
) -> None:
    storage = LocalDocumentStorage(tmp_path)
    knowledge_base_id = UUID("4a43e866-5694-4d4c-955d-69d1a58a2a17")
    storage_key = await stored_pdf(storage, knowledge_base_id)
    await storage.stage_knowledge_base_deletion(knowledge_base_id)

    recovery = await reconcile_staged_document_deletions(
        LocalDocumentStorage(tmp_path),
        KnowledgeBaseLookup(exists=True),
    )

    assert recovery.restored == 1
    assert recovery.finalized == 0
    assert (tmp_path / storage_key).exists()


async def test_recovery_finalizes_files_when_knowledge_base_is_gone(
    tmp_path: Path,
) -> None:
    storage = LocalDocumentStorage(tmp_path)
    knowledge_base_id = UUID("4a43e866-5694-4d4c-955d-69d1a58a2a17")
    storage_key = await stored_pdf(storage, knowledge_base_id)
    await storage.stage_knowledge_base_deletion(knowledge_base_id)

    recovery = await reconcile_staged_document_deletions(
        LocalDocumentStorage(tmp_path),
        KnowledgeBaseLookup(exists=False),
    )

    assert recovery.restored == 0
    assert recovery.finalized == 1
    assert not (tmp_path / storage_key).exists()
