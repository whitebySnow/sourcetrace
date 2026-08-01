import asyncio
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sourcetrace.modules.documents.ingestion import (
    ChunkingConfig,
    DocumentIngestionCoordinator,
    DocumentIngestionService,
    ParsedPage,
    PermanentIngestionError,
    TransientIngestionError,
)
from sourcetrace.modules.documents.models import Chunk
from sourcetrace.modules.documents.repository import DocumentRepository
from sourcetrace.modules.documents.service import DocumentService, IngestionQueueUnavailableError
from sourcetrace.modules.knowledge_bases.repository import KnowledgeBaseRepository
from sourcetrace.modules.knowledge_bases.service import KnowledgeBaseService


class TwoPageParser:
    version = "fake-parser-v1"

    async def parse(self, storage_key: str) -> list[ParsedPage]:
        return [
            ParsedPage(page_number=1, text="alpha beta gamma delta epsilon"),
            ParsedPage(page_number=2, text="zeta eta theta"),
        ]


class AlwaysTransientParser:
    version = "transient-parser-v1"

    async def parse(self, storage_key: str) -> list[ParsedPage]:
        raise TransientIngestionError("STORAGE_UNAVAILABLE", "Storage is temporarily unavailable")


class AlwaysPermanentParser:
    version = "permanent-parser-v1"

    async def parse(self, storage_key: str) -> list[ParsedPage]:
        raise PermanentIngestionError(
            "OCR_NOT_SUPPORTED",
            "PDF contains no extractable text; OCR is not supported",
        )


class UnexpectedFailureParser:
    version = "unexpected-parser-v1"

    async def parse(self, storage_key: str) -> list[ParsedPage]:
        raise RuntimeError("secret infrastructure detail")


class PausingParser(TwoPageParser):
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        self._started = started
        self._release = release

    async def parse(self, storage_key: str) -> list[ParsedPage]:
        self._started.set()
        await self._release.wait()
        return await super().parse(storage_key)


class PausingChunkRepository(DocumentRepository):
    def __init__(
        self,
        session: AsyncSession,
        started: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        super().__init__(session)
        self._started = started
        self._release = release

    async def create_chunks(self, chunks: list[Chunk]) -> None:
        self._started.set()
        await self._release.wait()
        await super().create_chunks(chunks)


class UnavailableQueue:
    async def enqueue(self, version_id: UUID) -> None:
        raise IngestionQueueUnavailableError


async def pending_version(session: AsyncSession) -> UUID:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    registration = await DocumentService(DocumentRepository(session)).register_version(
        knowledge_base.id,
        file_name="paper.pdf",
        checksum_sha256="a" * 64,
        storage_key=f"{knowledge_base.id}/{'a' * 64}.pdf",
        file_size_bytes=1024,
        page_count=2,
    )
    return registration.version.id


async def test_worker_processes_a_version_into_page_local_chunks(
    session: AsyncSession,
) -> None:
    version_id = await pending_version(session)
    service = DocumentIngestionService(
        repository=DocumentRepository(session),
        parser=TwoPageParser(),
        config=ChunkingConfig(
            tokenizer="cl100k_base",
            chunk_size=4,
            chunk_overlap=1,
            version="token-window-v1",
        ),
    )

    result = await service.process(version_id)
    chunks = await service.list_chunks(version_id)

    assert result.status == "chunked"
    assert result.stage == "chunked"
    assert result.attempt_count == 1
    assert [chunk.page_number for chunk in chunks] == [1, 1, 2]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
    assert [chunk.page_chunk_index for chunk in chunks] == [0, 1, 0]
    assert all(chunk.chunking_config_version == "token-window-v1" for chunk in chunks)


async def test_duplicate_task_delivery_does_not_duplicate_chunks(
    session: AsyncSession,
) -> None:
    version_id = await pending_version(session)
    service = DocumentIngestionService(
        repository=DocumentRepository(session),
        parser=TwoPageParser(),
        config=ChunkingConfig("cl100k_base", 4, 1, "token-window-v1"),
    )

    await service.process(version_id)
    first_chunks = await service.list_chunks(version_id)
    duplicate_result = await service.process(version_id)
    duplicate_chunks = await service.list_chunks(version_id)

    assert duplicate_result.status == "chunked"
    assert [chunk.id for chunk in duplicate_chunks] == [chunk.id for chunk in first_chunks]


async def test_concurrent_duplicate_delivery_is_a_no_op(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    version_id = await pending_version(session)
    started = asyncio.Event()
    release = asyncio.Event()

    async with session_factory() as first_session, session_factory() as second_session:
        first_service = DocumentIngestionService(
            repository=DocumentRepository(first_session),
            parser=PausingParser(started, release),
            config=ChunkingConfig("cl100k_base", 4, 1, "token-window-v1"),
        )
        second_service = DocumentIngestionService(
            repository=DocumentRepository(second_session),
            parser=TwoPageParser(),
            config=ChunkingConfig("cl100k_base", 4, 1, "token-window-v1"),
        )

        first_task = asyncio.create_task(first_service.process(version_id))
        await started.wait()
        duplicate = await second_service.process(version_id)
        release.set()
        completed = await first_task

        assert duplicate.status == "processing"
        assert duplicate.attempt_count == 1
        assert completed.status == "chunked"
        assert len(await second_service.list_chunks(version_id)) == 3


async def test_chunking_stage_is_visible_while_chunks_are_being_written(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    version_id = await pending_version(session)
    started = asyncio.Event()
    release = asyncio.Event()

    async with session_factory() as worker_session, session_factory() as observer_session:
        service = DocumentIngestionService(
            repository=PausingChunkRepository(worker_session, started, release),
            parser=TwoPageParser(),
            config=ChunkingConfig("cl100k_base", 4, 1, "token-window-v1"),
        )
        task = asyncio.create_task(service.process(version_id))
        await started.wait()

        visible_run = await DocumentRepository(observer_session).get_latest_ingestion_run(
            version_id
        )
        assert visible_run is not None
        assert visible_run.stage == "chunking"

        release.set()
        assert (await task).status == "chunked"


async def test_transient_failure_stops_after_three_attempts_and_allows_manual_retry(
    session: AsyncSession,
) -> None:
    version_id = await pending_version(session)
    service = DocumentIngestionService(
        repository=DocumentRepository(session),
        parser=AlwaysTransientParser(),
        config=ChunkingConfig("cl100k_base", 4, 1, "token-window-v1"),
    )

    with pytest.raises(TransientIngestionError):
        await service.process(version_id)
    with pytest.raises(TransientIngestionError):
        await service.process(version_id)
    result = await service.process(version_id)

    assert result.status == "failed"
    assert result.attempt_count == 3
    assert result.retryable is True
    assert result.failure_code == "STORAGE_UNAVAILABLE"


async def test_permanent_failure_does_not_auto_retry(
    session: AsyncSession,
) -> None:
    version_id = await pending_version(session)
    service = DocumentIngestionService(
        repository=DocumentRepository(session),
        parser=AlwaysPermanentParser(),
        config=ChunkingConfig("cl100k_base", 4, 1, "token-window-v1"),
    )

    result = await service.process(version_id)
    duplicate_result = await service.process(version_id)

    assert result.status == "failed"
    assert result.attempt_count == 1
    assert result.retryable is False
    assert result.failure_code == "OCR_NOT_SUPPORTED"
    assert duplicate_result.attempt_count == 1


async def test_unexpected_failure_is_sanitized_and_terminal_after_three_attempts(
    session: AsyncSession,
) -> None:
    version_id = await pending_version(session)
    service = DocumentIngestionService(
        repository=DocumentRepository(session),
        parser=UnexpectedFailureParser(),
        config=ChunkingConfig("cl100k_base", 4, 1, "token-window-v1"),
    )

    with pytest.raises(TransientIngestionError):
        await service.process(version_id)
    with pytest.raises(TransientIngestionError):
        await service.process(version_id)
    result = await service.process(version_id)

    assert result.status == "failed"
    assert result.attempt_count == 3
    assert result.failure_code == "INGESTION_TEMPORARY_FAILURE"


async def test_failed_manual_retry_remains_retryable_when_queue_is_unavailable(
    session: AsyncSession,
) -> None:
    version_id = await pending_version(session)
    repository = DocumentRepository(session)
    version = await repository.get_version(version_id)
    assert version is not None
    previous = await repository.create_ingestion_run(
        version_id,
        parser_version="pypdf-v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_config_version="token-window-v1",
    )
    previous.status = "failed"
    previous.stage = "failed"
    previous.retryable = True
    version.status = "failed"
    await repository.commit()
    coordinator = DocumentIngestionCoordinator(
        repository=repository,
        queue=UnavailableQueue(),
        parser_version="pypdf-v2",
        config=ChunkingConfig("cl100k_base", 600, 100, "token-window-v2"),
    )

    with pytest.raises(IngestionQueueUnavailableError):
        await coordinator.retry(version.knowledge_base_id, version_id)

    latest = await repository.get_latest_ingestion_run(version_id)
    assert latest is not None
    assert latest.run_number == 2
    assert latest.status == "failed"
    assert latest.retryable is True
    assert latest.failure_code == "QUEUE_UNAVAILABLE"
    assert latest.parser_version == "pypdf-v2"
    assert latest.chunk_size == 600
    assert latest.chunk_overlap == 100
    assert latest.chunking_config_version == "token-window-v2"
