from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sourcetrace.core.logging import get_logger
from sourcetrace.modules.documents.storage import LocalDocumentStorage

logger = get_logger(__name__)


class KnowledgeBaseLookupPort(Protocol):
    async def get(self, knowledge_base_id: UUID) -> object | None: ...


@dataclass(frozen=True)
class StagedDeletionRecovery:
    restored: int
    finalized: int
    failed: int


async def reconcile_staged_document_deletions(
    storage: LocalDocumentStorage,
    knowledge_bases: KnowledgeBaseLookupPort,
) -> StagedDeletionRecovery:
    restored = 0
    finalized = 0
    failed = 0
    for deletion in await storage.list_staged_deletions():
        knowledge_base = await knowledge_bases.get(deletion.knowledge_base_id)
        try:
            if knowledge_base is None:
                await deletion.finalize()
                finalized += 1
            else:
                await deletion.restore()
                restored += 1
        except OSError:
            failed += 1
            logger.exception(
                "staged_document_reconciliation_failed",
                knowledge_base_id=str(deletion.knowledge_base_id),
            )
    return StagedDeletionRecovery(
        restored=restored,
        finalized=finalized,
        failed=failed,
    )
