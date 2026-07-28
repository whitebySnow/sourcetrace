from typing import Protocol
from uuid import UUID

from sourcetrace.modules.documents.ingestion import (
    IngestionResult,
    TransientIngestionError,
)
from sourcetrace.modules.documents.models import Chunk, DocumentVersion, IngestionRun
from sourcetrace.modules.documents.service import DocumentVersionNotFoundError
from sourcetrace.rag.embeddings import (
    EmbeddingConfig,
    EmbeddingProviderError,
    validate_embeddings,
)
from sourcetrace.rag.ports import EmbeddingProvider


class IndexingRepositoryPort(Protocol):
    async def get_version(self, version_id: UUID) -> DocumentVersion | None: ...

    async def try_acquire_ingestion_lock(self, version_id: UUID) -> bool: ...

    async def release_ingestion_lock(self, version_id: UUID) -> None: ...

    async def get_latest_ingestion_run(
        self,
        document_version_id: UUID,
    ) -> IngestionRun | None: ...

    async def list_chunks(self, document_version_id: UUID) -> list[Chunk]: ...

    async def set_chunk_embeddings(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class DocumentIndexingService:
    def __init__(
        self,
        *,
        repository: IndexingRepositoryPort,
        embedding_provider: EmbeddingProvider,
        config: EmbeddingConfig,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._config = config

    async def process(self, version_id: UUID) -> IngestionResult:
        if not await self._repository.try_acquire_ingestion_lock(version_id):
            run = await self._repository.get_latest_ingestion_run(version_id)
            if run is None:
                raise DocumentVersionNotFoundError
            return self._result(run)
        try:
            return await self._process_owned(version_id)
        finally:
            await self._repository.release_ingestion_lock(version_id)

    async def _process_owned(self, version_id: UUID) -> IngestionResult:
        version = await self._repository.get_version(version_id)
        run = await self._repository.get_latest_ingestion_run(version_id)
        if version is None or run is None:
            raise DocumentVersionNotFoundError
        if version.status == "completed" and run.status == "completed":
            return self._result(run)
        if version.status != "chunked" or run.status != "chunked":
            return self._result(run)

        chunks = await self._repository.list_chunks(version_id)
        if not chunks:
            run.status = "failed"
            run.stage = "failed"
            run.retryable = False
            run.failure_code = "CHUNKS_NOT_FOUND"
            run.failure_message = "Document contains no chunks to index"
            version.status = "failed"
            await self._repository.commit()
            return self._result(run)

        if run.embedding_model is not None and not self._config_matches(run):
            run.status = "failed"
            run.stage = "failed"
            run.retryable = False
            run.failure_code = "EMBEDDING_CONFIG_MISMATCH"
            run.failure_message = "Recorded embedding configuration is incompatible"
            version.status = "failed"
            await self._repository.commit()
            return self._result(run)

        run.status = "processing"
        run.stage = "embedding"
        run.embedding_attempt_count += 1
        run.retryable = False
        run.failure_code = None
        run.failure_message = None
        if run.embedding_model is None:
            run.embedding_provider = self._config.provider
            run.embedding_model = self._config.model
            run.embedding_model_revision = self._config.revision
            run.embedding_dimension = self._config.dimension
            run.embedding_config_version = self._config.version
        version.status = "processing"
        await self._repository.commit()

        try:
            raw_embeddings = await self._embedding_provider.embed(
                [chunk.text for chunk in chunks]
            )
            embeddings = validate_embeddings(
                raw_embeddings,
                expected_count=len(chunks),
                dimension=self._config.dimension,
            )
            run.stage = "indexing"
            await self._repository.commit()
            await self._repository.set_chunk_embeddings(chunks, embeddings)
            run.status = "completed"
            run.stage = "completed"
            run.retryable = False
            version.status = "completed"
            await self._repository.commit()
        except EmbeddingProviderError as error:
            result = await self._record_transient_failure(version_id, error)
            if result is not None:
                return result
            raise TransientIngestionError(error.code, error.safe_message) from error
        except Exception as error:
            transient = EmbeddingProviderError(
                "INDEXING_TEMPORARY_FAILURE",
                "Document indexing failed temporarily",
            )
            result = await self._record_transient_failure(version_id, transient)
            if result is not None:
                return result
            raise TransientIngestionError(
                transient.code,
                transient.safe_message,
            ) from error

        return self._result(run)

    def _config_matches(self, run: IngestionRun) -> bool:
        return (
            run.embedding_provider == self._config.provider
            and run.embedding_model == self._config.model
            and run.embedding_model_revision == self._config.revision
            and run.embedding_dimension == self._config.dimension
            and run.embedding_config_version == self._config.version
        )

    async def _record_transient_failure(
        self,
        version_id: UUID,
        error: EmbeddingProviderError,
    ) -> IngestionResult | None:
        await self._repository.rollback()
        version = await self._repository.get_version(version_id)
        run = await self._repository.get_latest_ingestion_run(version_id)
        if version is None or run is None:
            raise DocumentVersionNotFoundError
        run.retryable = True
        run.failure_code = error.code
        run.failure_message = error.safe_message
        if run.embedding_attempt_count >= 3:
            run.status = "failed"
            run.stage = "failed"
            version.status = "failed"
            await self._repository.commit()
            return self._result(run)
        run.status = "chunked"
        run.stage = "chunked"
        version.status = "chunked"
        await self._repository.commit()
        return None

    @staticmethod
    def _result(run: IngestionRun) -> IngestionResult:
        return IngestionResult(
            status=run.status,
            stage=run.stage,
            attempt_count=max(run.attempt_count, run.embedding_attempt_count),
            retryable=run.retryable,
            failure_code=run.failure_code,
        )
