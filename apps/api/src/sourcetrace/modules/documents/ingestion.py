from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

import tiktoken

from sourcetrace.modules.documents.models import Chunk, DocumentVersion, IngestionRun
from sourcetrace.modules.documents.service import (
    DocumentVersionNotFoundError,
    IngestionQueuePort,
    IngestionQueueUnavailableError,
)


class PermanentIngestionError(Exception):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class TransientIngestionError(Exception):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class IngestionNotRetryableError(Exception):
    pass


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class ChunkingConfig:
    tokenizer: str
    chunk_size: int
    chunk_overlap: int
    version: str

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError("chunk size must be positive")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk overlap must be nonnegative and smaller than chunk size")


@dataclass(frozen=True)
class IngestionResult:
    status: str
    stage: str
    attempt_count: int
    retryable: bool
    failure_code: str | None


class DocumentParserPort(Protocol):
    version: str

    async def parse(self, storage_key: str) -> list[ParsedPage]: ...


class IngestionRepositoryPort(Protocol):
    async def get_version(self, version_id: UUID) -> DocumentVersion | None: ...

    async def try_acquire_ingestion_lock(self, version_id: UUID) -> bool: ...

    async def release_ingestion_lock(self, version_id: UUID) -> None: ...

    async def create_ingestion_run(
        self,
        document_version_id: UUID,
        *,
        parser_version: str,
        tokenizer: str,
        chunk_size: int,
        chunk_overlap: int,
        chunking_config_version: str,
    ) -> IngestionRun: ...

    async def get_latest_ingestion_run(
        self,
        document_version_id: UUID,
    ) -> IngestionRun | None: ...

    async def create_chunks(self, chunks: list[Chunk]) -> None: ...

    async def list_chunks(self, document_version_id: UUID) -> list[Chunk]: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class TokenWindowChunker:
    def __init__(self, config: ChunkingConfig) -> None:
        self._config = config
        self._encoding = tiktoken.get_encoding(config.tokenizer)

    def split(
        self,
        document_version_id: UUID,
        ingestion_run_id: UUID,
        pages: list[ParsedPage],
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        chunk_index = 0
        step = self._config.chunk_size - self._config.chunk_overlap
        for page in pages:
            tokens = self._encoding.encode(page.text)
            for page_chunk_index, start in enumerate(range(0, len(tokens), step)):
                window = tokens[start : start + self._config.chunk_size]
                if not window:
                    continue
                text = self._encoding.decode(window)
                if not text.strip():
                    continue
                stable_name = (
                    f"sourcetrace:chunk:{document_version_id}:"
                    f"{self._config.version}:{page.page_number}:{page_chunk_index}"
                )
                chunks.append(
                    Chunk(
                        id=uuid5(NAMESPACE_URL, stable_name),
                        document_version_id=document_version_id,
                        ingestion_run_id=ingestion_run_id,
                        page_number=page.page_number,
                        chunk_index=chunk_index,
                        page_chunk_index=page_chunk_index,
                        text=text,
                        token_count=len(window),
                        chunking_config_version=self._config.version,
                    )
                )
                chunk_index += 1
                if start + self._config.chunk_size >= len(tokens):
                    break
        return chunks


class DocumentIngestionService:
    def __init__(
        self,
        *,
        repository: IngestionRepositoryPort,
        parser: DocumentParserPort,
        config: ChunkingConfig,
    ) -> None:
        self._repository = repository
        self._parser = parser
        self._config = config
        self._chunker = TokenWindowChunker(config)

    async def process(self, version_id: UUID) -> IngestionResult:
        if not await self._repository.try_acquire_ingestion_lock(version_id):
            run = await self._repository.get_latest_ingestion_run(version_id)
            if run is not None:
                return self._result(run)
            return IngestionResult(
                status="pending",
                stage="queued",
                attempt_count=0,
                retryable=False,
                failure_code=None,
            )
        try:
            return await self._process_owned(version_id)
        finally:
            await self._repository.release_ingestion_lock(version_id)

    async def _process_owned(self, version_id: UUID) -> IngestionResult:
        version = await self._repository.get_version(version_id)
        if version is None or version.storage_key is None:
            raise DocumentVersionNotFoundError

        run = await self._repository.get_latest_ingestion_run(version_id)
        if run is not None and run.status in {"chunked", "completed", "failed"}:
            return self._result(run)
        if run is None:
            run = await self._repository.create_ingestion_run(
                version_id,
                parser_version=self._parser.version,
                tokenizer=self._config.tokenizer,
                chunk_size=self._config.chunk_size,
                chunk_overlap=self._config.chunk_overlap,
                chunking_config_version=self._config.version,
            )

        run.status = "processing"
        run.stage = "parsing"
        run.attempt_count += 1
        run.failure_code = None
        run.failure_message = None
        version.status = "processing"
        await self._repository.commit()

        try:
            pages = await self._parser.parse(version.storage_key)
            run.stage = "chunking"
            await self._repository.commit()
            chunks = self._chunker.split(version_id, run.id, pages)
            await self._repository.create_chunks(chunks)
        except PermanentIngestionError as error:
            run.status = "failed"
            run.stage = "failed"
            run.retryable = False
            run.failure_code = error.code
            run.failure_message = error.safe_message
            version.status = "failed"
            await self._repository.commit()
            return self._result(run)
        except TransientIngestionError as error:
            result = await self._record_transient_failure(version_id, error)
            if result is not None:
                return result
            raise
        except Exception as error:
            transient = TransientIngestionError(
                "INGESTION_TEMPORARY_FAILURE",
                "Document processing failed temporarily",
            )
            result = await self._record_transient_failure(version_id, transient)
            if result is not None:
                return result
            raise transient from error

        run.status = "chunked"
        run.stage = "chunked"
        run.retryable = False
        version.status = "chunked"
        await self._repository.commit()
        return self._result(run)

    async def _record_transient_failure(
        self,
        version_id: UUID,
        error: TransientIngestionError,
    ) -> IngestionResult | None:
        await self._repository.rollback()
        version = await self._repository.get_version(version_id)
        run = await self._repository.get_latest_ingestion_run(version_id)
        if version is None or run is None:
            raise DocumentVersionNotFoundError
        run.retryable = True
        run.failure_code = error.code
        run.failure_message = error.safe_message
        if run.attempt_count >= 3:
            run.status = "failed"
            run.stage = "failed"
            version.status = "failed"
            await self._repository.commit()
            return self._result(run)
        run.status = "pending"
        run.stage = "queued"
        version.status = "pending"
        await self._repository.commit()
        return None

    @staticmethod
    def _result(run: IngestionRun) -> IngestionResult:
        return IngestionResult(
            status=run.status,
            stage=run.stage,
            attempt_count=run.attempt_count,
            retryable=run.retryable,
            failure_code=run.failure_code,
        )

    async def list_chunks(self, version_id: UUID) -> list[Chunk]:
        return await self._repository.list_chunks(version_id)


class DocumentIngestionCoordinator:
    def __init__(
        self,
        *,
        repository: IngestionRepositoryPort,
        queue: IngestionQueuePort,
    ) -> None:
        self._repository = repository
        self._queue = queue

    async def retry(
        self,
        knowledge_base_id: UUID,
        version_id: UUID,
    ) -> IngestionResult:
        version = await self._repository.get_version(version_id)
        if version is None or version.knowledge_base_id != knowledge_base_id:
            raise DocumentVersionNotFoundError
        previous = await self._repository.get_latest_ingestion_run(version_id)
        if previous is None or previous.status != "failed" or not previous.retryable:
            raise IngestionNotRetryableError
        run = await self._repository.create_ingestion_run(
            version_id,
            parser_version=previous.parser_version,
            tokenizer=previous.tokenizer,
            chunk_size=previous.chunk_size,
            chunk_overlap=previous.chunk_overlap,
            chunking_config_version=previous.chunking_config_version,
        )
        if previous.embedding_model is not None:
            run.status = "chunked"
            run.stage = "chunked"
            run.embedding_provider = previous.embedding_provider
            run.embedding_model = previous.embedding_model
            run.embedding_model_revision = previous.embedding_model_revision
            run.embedding_dimension = previous.embedding_dimension
            run.embedding_config_version = previous.embedding_config_version
            version.status = "chunked"
        else:
            version.status = "pending"
        await self._repository.commit()
        try:
            await self._queue.enqueue(version_id)
        except IngestionQueueUnavailableError:
            run.status = "failed"
            run.stage = "failed"
            run.retryable = True
            run.failure_code = "QUEUE_UNAVAILABLE"
            run.failure_message = "The ingestion queue is temporarily unavailable"
            version.status = "failed"
            await self._repository.commit()
            raise
        return DocumentIngestionService._result(run)
