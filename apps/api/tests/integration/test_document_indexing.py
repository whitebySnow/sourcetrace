from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.modules.documents.indexing import DocumentIndexingService
from sourcetrace.modules.documents.ingestion import (
    DocumentIngestionCoordinator,
    TransientIngestionError,
)
from sourcetrace.modules.documents.models import Chunk
from sourcetrace.modules.documents.repository import DocumentRepository
from sourcetrace.modules.documents.service import DocumentService
from sourcetrace.modules.knowledge_bases.repository import KnowledgeBaseRepository
from sourcetrace.modules.knowledge_bases.service import KnowledgeBaseService
from sourcetrace.rag.embeddings import EmbeddingConfig, EmbeddingProviderError


class RecordingEmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        vectors: list[list[float]] = []
        for index, _text in enumerate(texts):
            vector = [0.0] * 1024
            vector[index % 1024] = 1.0
            vectors.append(vector)
        return vectors


class UnavailableEmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingProviderError(
            "EMBEDDING_PROVIDER_UNAVAILABLE",
            "Embedding model is temporarily unavailable",
        )


class RecordingIngestionQueue:
    def __init__(self) -> None:
        self.version_ids: list[UUID] = []

    async def enqueue(self, version_id: UUID) -> None:
        self.version_ids.append(version_id)


def embedding_config() -> EmbeddingConfig:
    return EmbeddingConfig(
        provider="test-provider",
        model="BAAI/bge-m3",
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        cache_dir=Path(r"D:\DevelopEnvironment\huggingface"),
        endpoint="https://hf-mirror.com",
        device="cpu",
        batch_size=8,
        dimension=1024,
        version="bge-m3-dense-v1",
    )


async def create_chunked_version(
    session: AsyncSession,
    *,
    knowledge_base_id: UUID,
    file_name: str,
    checksum: str,
) -> UUID:
    repository = DocumentRepository(session)
    registration = await DocumentService(repository).register_version(
        knowledge_base_id,
        file_name=file_name,
        checksum_sha256=checksum,
        storage_key=f"{knowledge_base_id}/{checksum}.pdf",
        file_size_bytes=1024,
        page_count=1,
    )
    run = await repository.create_ingestion_run(
        registration.version.id,
        parser_version="fake-parser-v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_config_version="token-window-v1",
    )
    run.status = "chunked"
    run.stage = "chunked"
    run.attempt_count = 1
    registration.version.status = "chunked"
    await repository.create_chunks(
        [
            Chunk(
                id=uuid4(),
                document_version_id=registration.version.id,
                ingestion_run_id=run.id,
                page_number=1,
                chunk_index=index,
                page_chunk_index=index,
                text=text,
                token_count=2,
                chunking_config_version="token-window-v1",
            )
            for index, text in enumerate(["alpha evidence", "beta evidence"])
        ]
    )
    await repository.commit()
    return registration.version.id


async def test_indexing_writes_vectors_and_activates_only_once(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    version_id = await create_chunked_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="paper.pdf",
        checksum="a" * 64,
    )
    provider = RecordingEmbeddingProvider()
    repository = DocumentRepository(session)
    service = DocumentIndexingService(
        repository=repository,
        embedding_provider=provider,
        config=embedding_config(),
    )

    result = await service.process(version_id)
    duplicate = await service.process(version_id)
    chunks = await repository.list_chunks(version_id)
    run = await repository.get_latest_ingestion_run(version_id)

    assert result.status == "completed"
    assert duplicate.status == "completed"
    assert provider.calls == [["alpha evidence", "beta evidence"]]
    assert all(chunk.embedding is not None for chunk in chunks)
    assert run is not None
    assert run.embedding_provider == "test-provider"
    assert run.embedding_model == "BAAI/bge-m3"
    assert run.embedding_model_revision == embedding_config().revision
    assert run.embedding_dimension == 1024
    assert run.embedding_config_version == "bge-m3-dense-v1"


async def test_embedding_failure_never_makes_a_version_searchable(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    version_id = await create_chunked_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="paper.pdf",
        checksum="b" * 64,
    )
    repository = DocumentRepository(session)
    service = DocumentIndexingService(
        repository=repository,
        embedding_provider=UnavailableEmbeddingProvider(),
        config=embedding_config(),
    )

    with pytest.raises(TransientIngestionError):
        await service.process(version_id)
    with pytest.raises(TransientIngestionError):
        await service.process(version_id)
    result = await service.process(version_id)

    assert result.status == "failed"
    assert result.retryable is True
    assert result.failure_code == "EMBEDDING_PROVIDER_UNAVAILABLE"
    assert await repository.list_searchable_chunks(knowledge_base.id) == []
    assert all(
        chunk.embedding is None for chunk in await repository.list_chunks(version_id)
    )


async def test_searchable_scope_uses_latest_completed_version_and_keeps_history(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    first_id = await create_chunked_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="paper.pdf",
        checksum="c" * 64,
    )
    second_id = await create_chunked_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="paper.pdf",
        checksum="d" * 64,
    )
    repository = DocumentRepository(session)
    service = DocumentIndexingService(
        repository=repository,
        embedding_provider=RecordingEmbeddingProvider(),
        config=embedding_config(),
    )

    await service.process(first_id)
    while_second_is_chunked = await repository.list_searchable_chunks(knowledge_base.id)
    await service.process(second_id)
    after_second_completed = await repository.list_searchable_chunks(knowledge_base.id)

    assert {chunk.document_version_id for chunk in while_second_is_chunked} == {first_id}
    assert {chunk.document_version_id for chunk in after_second_completed} == {second_id}
    assert len(await repository.list_chunks(first_id)) == 2


async def test_manual_retry_resumes_embedding_without_recreating_chunks(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    version_id = await create_chunked_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="paper.pdf",
        checksum="e" * 64,
    )
    repository = DocumentRepository(session)
    failing = DocumentIndexingService(
        repository=repository,
        embedding_provider=UnavailableEmbeddingProvider(),
        config=embedding_config(),
    )
    with pytest.raises(TransientIngestionError):
        await failing.process(version_id)
    with pytest.raises(TransientIngestionError):
        await failing.process(version_id)
    assert (await failing.process(version_id)).status == "failed"
    original_chunk_ids = [chunk.id for chunk in await repository.list_chunks(version_id)]
    queue = RecordingIngestionQueue()

    retry = await DocumentIngestionCoordinator(
        repository=repository,
        queue=queue,
    ).retry(knowledge_base.id, version_id)
    resumed = await DocumentIndexingService(
        repository=repository,
        embedding_provider=RecordingEmbeddingProvider(),
        config=embedding_config(),
    ).process(version_id)

    assert retry.status == "chunked"
    assert retry.stage == "chunked"
    assert queue.version_ids == [version_id]
    assert resumed.status == "completed"
    assert [chunk.id for chunk in await repository.list_chunks(version_id)] == original_chunk_ids


async def test_failed_activation_rolls_back_vectors_and_remains_retryable(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    version_id = await create_chunked_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="paper.pdf",
        checksum="f" * 64,
    )
    await session.execute(
        text(
            """
            CREATE FUNCTION reject_completed_activation() RETURNS trigger AS $$
            BEGIN
                IF NEW.status = 'completed' THEN
                    RAISE EXCEPTION 'temporary activation failure';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE TRIGGER trg_reject_completed_activation
            BEFORE UPDATE OF status ON document_versions
            FOR EACH ROW EXECUTE FUNCTION reject_completed_activation()
            """
        )
    )
    await session.commit()
    repository = DocumentRepository(session)
    service = DocumentIndexingService(
        repository=repository,
        embedding_provider=RecordingEmbeddingProvider(),
        config=embedding_config(),
    )

    with pytest.raises(TransientIngestionError):
        await service.process(version_id)

    version = await repository.get_version(version_id)
    run = await repository.get_latest_ingestion_run(version_id)
    assert version is not None
    assert run is not None
    assert version.status == "chunked"
    assert run.status == "chunked"
    assert run.retryable is True
    assert all(
        chunk.embedding is None for chunk in await repository.list_chunks(version_id)
    )
