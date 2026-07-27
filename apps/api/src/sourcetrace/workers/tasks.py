import asyncio
from uuid import UUID

import dramatiq

from sourcetrace.core.config import Settings, get_settings
from sourcetrace.db.session import session_factory
from sourcetrace.modules.documents.ingestion import ChunkingConfig, DocumentIngestionService
from sourcetrace.modules.documents.models import IngestionRun
from sourcetrace.modules.documents.parsing import PypdfDocumentParser
from sourcetrace.modules.documents.repository import DocumentRepository
from sourcetrace.modules.documents.storage import LocalDocumentStorage
from sourcetrace.workers.broker import broker as broker


@dramatiq.actor(
    queue_name="document-ingestion",
    max_retries=2,
    min_backoff=1_000,
    max_backoff=30_000,
)
def ingest_document_version(version_id: str) -> None:
    asyncio.run(_ingest_document_version(UUID(version_id)))


async def _ingest_document_version(version_id: UUID) -> None:
    settings = get_settings()
    async with session_factory() as session:
        repository = DocumentRepository(session)
        run = await repository.get_latest_ingestion_run(version_id)
        config = chunking_config_for_run(settings, run)
        service = DocumentIngestionService(
            repository=repository,
            parser=PypdfDocumentParser(LocalDocumentStorage(settings.upload_dir)),
            config=config,
        )
        await service.process(version_id)


def chunking_config_for_run(
    settings: Settings,
    run: IngestionRun | None,
) -> ChunkingConfig:
    if run is not None:
        return ChunkingConfig(
            tokenizer=run.tokenizer,
            chunk_size=run.chunk_size,
            chunk_overlap=run.chunk_overlap,
            version=run.chunking_config_version,
        )
    return ChunkingConfig(
        tokenizer=settings.ingestion_tokenizer,
        chunk_size=settings.ingestion_chunk_size,
        chunk_overlap=settings.ingestion_chunk_overlap,
        version=settings.ingestion_chunking_config_version,
    )
