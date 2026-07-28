from datetime import datetime
from hashlib import blake2b
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from sourcetrace.modules.documents.models import Chunk, Document, DocumentVersion, IngestionRun


class DocumentWriteConflictError(Exception):
    pass


CONCURRENT_WRITE_CONSTRAINTS = {
    "uq_documents_knowledge_base_name",
    "uq_document_versions_knowledge_base_checksum",
    "uq_document_versions_sequence",
}


def _is_concurrent_write(error: IntegrityError) -> bool:
    if error.orig is None:
        return False
    cause = error.orig.__cause__
    return cause is not None and (
        getattr(cause, "constraint_name", None) in CONCURRENT_WRITE_CONSTRAINTS
    )


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._ingestion_locks: dict[UUID, tuple[AsyncConnection, int]] = {}

    async def try_acquire_ingestion_lock(self, version_id: UUID) -> bool:
        bind = self._session.bind
        if isinstance(bind, AsyncConnection):
            engine = bind.engine
        elif isinstance(bind, AsyncEngine):
            engine = bind
        else:
            raise RuntimeError("document repository requires an async database engine")
        connection = await engine.connect()
        key = int.from_bytes(blake2b(version_id.bytes, digest_size=8).digest(), signed=True)
        acquired = bool(
            await connection.scalar(select(func.pg_try_advisory_lock(key)))
        )
        if not acquired:
            await connection.close()
            return False
        self._ingestion_locks[version_id] = (connection, key)
        return True

    async def release_ingestion_lock(self, version_id: UUID) -> None:
        held = self._ingestion_locks.pop(version_id, None)
        if held is None:
            return
        connection, key = held
        try:
            await connection.scalar(select(func.pg_advisory_unlock(key)))
        finally:
            await connection.close()

    async def create_document(self, knowledge_base_id: UUID, name: str) -> Document:
        document = Document(knowledge_base_id=knowledge_base_id, name=name)
        self._session.add(document)
        try:
            await self._session.flush()
        except IntegrityError as error:
            if _is_concurrent_write(error):
                raise DocumentWriteConflictError from error
            raise
        await self._session.refresh(document)
        return document

    async def get_by_checksum(
        self,
        knowledge_base_id: UUID,
        checksum_sha256: str,
    ) -> tuple[Document, DocumentVersion] | None:
        statement = (
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                DocumentVersion.checksum_sha256 == checksum_sha256,
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        return (row.Document, row.DocumentVersion) if row is not None else None

    async def get_document_by_name(
        self,
        knowledge_base_id: UUID,
        name: str,
    ) -> Document | None:
        statement = select(Document).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.name == name,
        )
        return await self._session.scalar(statement)

    async def get_latest_version(self, document_id: UUID) -> DocumentVersion | None:
        statement = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
        return await self._session.scalar(statement)

    async def create_version(
        self,
        document_id: UUID,
        *,
        knowledge_base_id: UUID,
        version_number: int,
        checksum_sha256: str,
        storage_key: str | None = None,
        file_size_bytes: int | None = None,
        page_count: int | None = None,
    ) -> DocumentVersion:
        version = DocumentVersion(
            document_id=document_id,
            knowledge_base_id=knowledge_base_id,
            version_number=version_number,
            checksum_sha256=checksum_sha256,
            storage_key=storage_key,
            file_size_bytes=file_size_bytes,
            page_count=page_count,
        )
        self._session.add(version)
        try:
            await self._session.flush()
        except IntegrityError as error:
            if _is_concurrent_write(error):
                raise DocumentWriteConflictError from error
            raise
        await self._session.refresh(version)
        return version

    async def get_version(self, version_id: UUID) -> DocumentVersion | None:
        statement = select(DocumentVersion).where(DocumentVersion.id == version_id)
        return await self._session.scalar(statement)

    async def create_ingestion_run(
        self,
        document_version_id: UUID,
        *,
        parser_version: str,
        tokenizer: str,
        chunk_size: int,
        chunk_overlap: int,
        chunking_config_version: str,
    ) -> IngestionRun:
        latest_number = await self._session.scalar(
            select(IngestionRun.run_number)
            .where(IngestionRun.document_version_id == document_version_id)
            .order_by(IngestionRun.run_number.desc())
            .limit(1)
        )
        run = IngestionRun(
            document_version_id=document_version_id,
            run_number=1 if latest_number is None else latest_number + 1,
            parser_version=parser_version,
            tokenizer=tokenizer,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunking_config_version=chunking_config_version,
        )
        self._session.add(run)
        await self._session.flush()
        await self._session.refresh(run)
        return run

    async def get_latest_ingestion_run(
        self,
        document_version_id: UUID,
    ) -> IngestionRun | None:
        statement = (
            select(IngestionRun)
            .where(IngestionRun.document_version_id == document_version_id)
            .order_by(IngestionRun.run_number.desc())
            .limit(1)
        )
        return await self._session.scalar(statement)

    async def create_chunks(self, chunks: list[Chunk]) -> None:
        self._session.add_all(chunks)
        await self._session.flush()

    async def list_chunks(self, document_version_id: UUID) -> list[Chunk]:
        result = await self._session.scalars(
            select(Chunk)
            .where(Chunk.document_version_id == document_version_id)
            .order_by(Chunk.chunk_index)
        )
        return list(result)

    async def set_chunk_embeddings(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> None:
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            chunk.embedding = embedding
        await self._session.flush()

    async def list_searchable_chunks(self, knowledge_base_id: UUID) -> list[Chunk]:
        latest_completed = (
            select(
                DocumentVersion.document_id,
                func.max(DocumentVersion.version_number).label("version_number"),
            )
            .where(
                DocumentVersion.knowledge_base_id == knowledge_base_id,
                DocumentVersion.status == "completed",
            )
            .group_by(DocumentVersion.document_id)
            .subquery()
        )
        result = await self._session.scalars(
            select(Chunk)
            .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
            .join(
                latest_completed,
                and_(
                    latest_completed.c.document_id == DocumentVersion.document_id,
                    latest_completed.c.version_number == DocumentVersion.version_number,
                ),
            )
            .where(Chunk.embedding.is_not(None))
            .order_by(DocumentVersion.document_id, Chunk.chunk_index)
        )
        return list(result)

    async def get_latest_ingestion_runs(
        self,
        document_version_ids: list[UUID],
    ) -> dict[UUID, IngestionRun]:
        if not document_version_ids:
            return {}
        result = await self._session.scalars(
            select(IngestionRun)
            .where(IngestionRun.document_version_id.in_(document_version_ids))
            .order_by(
                IngestionRun.document_version_id,
                IngestionRun.run_number.desc(),
            )
            .distinct(IngestionRun.document_version_id)
        )
        return {run.document_version_id: run for run in result}

    async def list_versions(
        self,
        knowledge_base_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[tuple[Document, DocumentVersion]]:
        statement = (
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.knowledge_base_id == knowledge_base_id)
        )
        if after is not None:
            created_at, version_id = after
            statement = statement.where(
                or_(
                    DocumentVersion.created_at < created_at,
                    and_(
                        DocumentVersion.created_at == created_at,
                        DocumentVersion.id < version_id,
                    ),
                )
            )
        statement = statement.order_by(
            DocumentVersion.created_at.desc(),
            DocumentVersion.id.desc(),
        ).limit(limit)
        rows = (await self._session.execute(statement)).all()
        return [(row.Document, row.DocumentVersion) for row in rows]

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
