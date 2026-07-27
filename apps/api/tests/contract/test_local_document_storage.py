from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import UUID

from sourcetrace.modules.documents.storage import LocalDocumentStorage


async def test_local_storage_uses_an_immutable_content_addressed_key(
    tmp_path: Path,
) -> None:
    storage = LocalDocumentStorage(tmp_path)
    knowledge_base_id = UUID("4a43e866-5694-4d4c-955d-69d1a58a2a17")
    checksum = "a" * 64
    with SpooledTemporaryFile(mode="w+b") as original:
        original.write(b"%PDF-1.7\noriginal")
        original.seek(0)
        storage_key = await storage.store(knowledge_base_id, checksum, original)

    with SpooledTemporaryFile(mode="w+b") as replacement:
        replacement.write(b"%PDF-1.7\nreplacement")
        replacement.seek(0)
        repeated_key = await storage.store(knowledge_base_id, checksum, replacement)

    assert storage_key == f"{knowledge_base_id}/{checksum}.pdf"
    assert repeated_key == storage_key
    assert (tmp_path / storage_key).read_bytes() == b"%PDF-1.7\noriginal"


async def test_staged_deletion_can_be_restored_or_finalized(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    knowledge_base_id = UUID("4a43e866-5694-4d4c-955d-69d1a58a2a17")
    checksum = "a" * 64
    with SpooledTemporaryFile(mode="w+b") as content:
        content.write(b"%PDF-1.7\noriginal")
        content.seek(0)
        storage_key = await storage.store(knowledge_base_id, checksum, content)

    staged = await storage.stage_knowledge_base_deletion(knowledge_base_id)
    assert not (tmp_path / storage_key).exists()
    await staged.restore()
    assert (tmp_path / storage_key).exists()

    staged_again = await storage.stage_knowledge_base_deletion(knowledge_base_id)
    await staged_again.finalize()
    assert not (tmp_path / storage_key).exists()
    assert list((tmp_path / ".deletions").iterdir()) == []


async def test_abandoned_staged_deletions_can_be_discovered(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    knowledge_base_id = UUID("4a43e866-5694-4d4c-955d-69d1a58a2a17")
    checksum = "a" * 64
    with SpooledTemporaryFile(mode="w+b") as content:
        content.write(b"%PDF-1.7\noriginal")
        content.seek(0)
        storage_key = await storage.store(knowledge_base_id, checksum, content)
    await storage.stage_knowledge_base_deletion(knowledge_base_id)

    staged = await LocalDocumentStorage(tmp_path).list_staged_deletions()

    assert len(staged) == 1
    assert staged[0].knowledge_base_id == knowledge_base_id
    assert not (tmp_path / storage_key).exists()
