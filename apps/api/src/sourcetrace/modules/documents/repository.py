from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.modules.documents.models import Document, DocumentVersion


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
