from functools import lru_cache
from uuid import UUID

import dramatiq

from sourcetrace.core.config import Settings, get_settings
from sourcetrace.db.session import session_factory
from sourcetrace.modules.documents.indexing import DocumentIndexingService
from sourcetrace.modules.documents.ingestion import ChunkingConfig, DocumentIngestionService
from sourcetrace.modules.documents.models import IngestionRun
from sourcetrace.modules.documents.parsing import PypdfDocumentParser
from sourcetrace.modules.documents.repository import DocumentRepository
from sourcetrace.modules.documents.storage import LocalDocumentStorage
from sourcetrace.rag.embeddings import (
    BgeM3EmbeddingProvider,
    EmbeddingConfig,
)
from sourcetrace.workers.broker import broker as broker


@dramatiq.actor(  # type: ignore[arg-type]
    queue_name="document-ingestion",
    max_retries=2,
    min_backoff=1_000,
    max_backoff=30_000,
)
async def ingest_document_version(version_id: str) -> None:
    await _ingest_document_version(UUID(version_id))


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
        result = await service.process(version_id)
        if result.status == "chunked":
            indexing_run = await repository.get_latest_ingestion_run(version_id)
            indexing_config = embedding_config_for_run(settings, indexing_run)
            await DocumentIndexingService(
                repository=repository,
                embedding_provider=get_embedding_provider(indexing_config),
                config=indexing_config,
            ).process(version_id)


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


def embedding_config_for_run(
    settings: Settings,
    run: IngestionRun | None,
) -> EmbeddingConfig:
    if run is not None and run.embedding_model is not None:
        if (
            run.embedding_provider is None
            or run.embedding_model_revision is None
            or run.embedding_dimension is None
            or run.embedding_config_version is None
        ):
            raise RuntimeError("recorded embedding configuration is incomplete")
        provider = run.embedding_provider
        model = run.embedding_model
        revision = run.embedding_model_revision
        dimension = run.embedding_dimension
        version = run.embedding_config_version
    else:
        provider = settings.embedding_provider
        model = settings.embedding_model
        revision = settings.embedding_model_revision
        dimension = settings.embedding_dimension
        version = settings.embedding_config_version
    return EmbeddingConfig(
        provider=provider,
        model=model,
        revision=revision,
        cache_dir=settings.embedding_cache_dir,
        endpoint=settings.embedding_hf_endpoint,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        dimension=dimension,
        version=version,
    )


@lru_cache
def get_embedding_provider(config: EmbeddingConfig) -> BgeM3EmbeddingProvider:
    if config.provider != "sentence-transformers":
        raise RuntimeError(f"unsupported embedding provider: {config.provider}")
    return BgeM3EmbeddingProvider(config)
