import asyncio
import os
import shutil
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import UUID, uuid4

from sourcetrace.core.logging import get_logger

logger = get_logger(__name__)


class LocalDocumentStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    async def store(
        self,
        knowledge_base_id: UUID,
        checksum_sha256: str,
        content: SpooledTemporaryFile[bytes],
    ) -> str:
        storage_key = f"{knowledge_base_id}/{checksum_sha256}.pdf"
        await asyncio.to_thread(self._store_sync, storage_key, content)
        return storage_key

    def source_path(self, storage_key: str) -> Path:
        candidate = self._root.joinpath(*storage_key.split("/")).resolve()
        if self._root not in candidate.parents:
            raise ValueError("document storage key escapes the configured root")
        return candidate

    async def stage_knowledge_base_deletion(
        self,
        knowledge_base_id: UUID,
    ) -> "LocalStagedKnowledgeBaseDeletion":
        source = (self._root / str(knowledge_base_id)).resolve()
        if source.parent != self._root:
            raise ValueError("knowledge base storage path escapes the configured root")
        staged = self._root / ".deletions" / f"{knowledge_base_id}-{uuid4().hex}"
        moved = await asyncio.to_thread(self._stage_deletion_sync, source, staged)
        return LocalStagedKnowledgeBaseDeletion(
            knowledge_base_id,
            source,
            staged if moved else None,
        )

    async def list_staged_deletions(self) -> list["LocalStagedKnowledgeBaseDeletion"]:
        return await asyncio.to_thread(self._list_staged_deletions_sync)

    def _list_staged_deletions_sync(self) -> list["LocalStagedKnowledgeBaseDeletion"]:
        deletion_root = self._root / ".deletions"
        if not deletion_root.exists():
            return []
        staged_deletions = []
        for staged in deletion_root.iterdir():
            try:
                knowledge_base_id = UUID(staged.name[:36])
            except ValueError:
                logger.warning(
                    "invalid_staged_document_cleanup_ignored",
                    staged_path=str(staged),
                )
                continue
            source = self._root / str(knowledge_base_id)
            staged_deletions.append(
                LocalStagedKnowledgeBaseDeletion(
                    knowledge_base_id,
                    source,
                    staged,
                )
            )
        return staged_deletions

    @staticmethod
    def _stage_deletion_sync(source: Path, staged: Path) -> bool:
        if not source.exists():
            return False
        staged.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, staged)
        return True

    def _store_sync(
        self,
        storage_key: str,
        content: SpooledTemporaryFile[bytes],
    ) -> None:
        destination = self._root.joinpath(*storage_key.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            content.seek(0)
            with temporary.open("xb") as target:
                while chunk := content.read(64 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
            content.seek(0)


class LocalStagedKnowledgeBaseDeletion:
    def __init__(
        self,
        knowledge_base_id: UUID,
        source: Path,
        staged: Path | None,
    ) -> None:
        self.knowledge_base_id = knowledge_base_id
        self._source = source
        self._staged = staged

    async def finalize(self) -> None:
        if self._staged is not None:
            await asyncio.to_thread(shutil.rmtree, self._staged)

    async def restore(self) -> None:
        if self._staged is not None:
            await asyncio.to_thread(self._restore_sync)

    def _restore_sync(self) -> None:
        if self._staged is None or not self._staged.exists():
            return
        self._source.parent.mkdir(parents=True, exist_ok=True)
        if self._source.exists():
            raise FileExistsError(f"cannot restore source files to {self._source}")
        os.replace(self._staged, self._source)
