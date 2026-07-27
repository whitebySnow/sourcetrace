import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from tempfile import SpooledTemporaryFile
from typing import Protocol
from uuid import UUID

from sourcetrace.modules.documents.models import Document, DocumentVersion, IngestionRun
from sourcetrace.modules.documents.repository import DocumentWriteConflictError


class DocumentVersionNotFoundError(Exception):
    pass


class InvalidDocumentChecksumError(ValueError):
    pass


class InvalidDocumentNameError(ValueError):
    pass


class DocumentRegistrationConflictError(Exception):
    pass


class InvalidDocumentCursorError(ValueError):
    pass


class PdfFileTooLargeError(ValueError):
    pass


class InvalidPdfSignatureError(ValueError):
    pass


class IngestionQueueUnavailableError(Exception):
    pass


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_REGISTRATION_ATTEMPTS = 3
READ_CHUNK_SIZE = 64 * 1024


def normalize_document_name(file_name: str) -> str:
    normalized_name = file_name.strip()
    if not normalized_name or len(normalized_name) > 255:
        raise InvalidDocumentNameError
    return normalized_name


@dataclass(frozen=True)
class VersionRegistration:
    document: Document
    version: DocumentVersion
    deduplicated: bool


@dataclass(frozen=True)
class PdfMetadata:
    page_count: int


@dataclass(frozen=True)
class DocumentUpload:
    registration: VersionRegistration


@dataclass(frozen=True)
class DocumentVersionRecord:
    document: Document
    version: DocumentVersion
    ingestion_run: IngestionRun | None = None


@dataclass(frozen=True)
class DocumentVersionPage:
    items: list[DocumentVersionRecord]
    next_cursor: str | None


class UploadedFilePort(Protocol):
    filename: str | None

    async def read(self, size: int = -1) -> bytes: ...


class PdfValidatorPort(Protocol):
    async def validate(self, content: SpooledTemporaryFile[bytes]) -> PdfMetadata: ...


class DocumentStoragePort(Protocol):
    async def store(
        self,
        knowledge_base_id: UUID,
        checksum_sha256: str,
        content: SpooledTemporaryFile[bytes],
    ) -> str: ...


class IngestionQueuePort(Protocol):
    async def enqueue(self, version_id: UUID) -> None: ...


class KnowledgeBaseReaderPort(Protocol):
    async def get(self, knowledge_base_id: UUID) -> object: ...


class DocumentRepositoryPort(Protocol):
    async def get_by_checksum(
        self,
        knowledge_base_id: UUID,
        checksum_sha256: str,
    ) -> tuple[Document, DocumentVersion] | None: ...

    async def get_document_by_name(
        self,
        knowledge_base_id: UUID,
        name: str,
    ) -> Document | None: ...

    async def get_latest_version(self, document_id: UUID) -> DocumentVersion | None: ...

    async def create_document(self, knowledge_base_id: UUID, name: str) -> Document: ...

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
    ) -> DocumentVersion: ...

    async def get_version(self, version_id: UUID) -> DocumentVersion | None: ...

    async def list_versions(
        self,
        knowledge_base_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[tuple[Document, DocumentVersion]]: ...

    async def get_latest_ingestion_runs(
        self,
        document_version_ids: list[UUID],
    ) -> dict[UUID, IngestionRun]: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class DocumentService:
    def __init__(self, repository: DocumentRepositoryPort) -> None:
        self._repository = repository

    async def register_version(
        self,
        knowledge_base_id: UUID,
        *,
        file_name: str,
        checksum_sha256: str,
        storage_key: str | None = None,
        file_size_bytes: int | None = None,
        page_count: int | None = None,
    ) -> VersionRegistration:
        if SHA256_PATTERN.fullmatch(checksum_sha256) is None:
            raise InvalidDocumentChecksumError

        normalized_name = normalize_document_name(file_name)

        last_conflict: DocumentWriteConflictError | None = None
        for _attempt in range(MAX_REGISTRATION_ATTEMPTS):
            try:
                return await self._register_version(
                    knowledge_base_id,
                    normalized_name=normalized_name,
                    checksum_sha256=checksum_sha256,
                    storage_key=storage_key,
                    file_size_bytes=file_size_bytes,
                    page_count=page_count,
                )
            except DocumentWriteConflictError as error:
                last_conflict = error
                await self._repository.rollback()
        raise DocumentRegistrationConflictError from last_conflict

    async def _register_version(
        self,
        knowledge_base_id: UUID,
        *,
        normalized_name: str,
        checksum_sha256: str,
        storage_key: str | None,
        file_size_bytes: int | None,
        page_count: int | None,
    ) -> VersionRegistration:
        existing = await self._repository.get_by_checksum(
            knowledge_base_id,
            checksum_sha256,
        )
        if existing is not None:
            existing_document, existing_version = existing
            return VersionRegistration(
                document=existing_document,
                version=existing_version,
                deduplicated=True,
            )

        document = await self._repository.get_document_by_name(
            knowledge_base_id,
            normalized_name,
        )
        if document is None:
            document = await self._repository.create_document(
                knowledge_base_id,
                normalized_name,
            )
        latest = await self._repository.get_latest_version(document.id)
        version = await self._repository.create_version(
            document.id,
            knowledge_base_id=knowledge_base_id,
            version_number=1 if latest is None else latest.version_number + 1,
            checksum_sha256=checksum_sha256,
            storage_key=storage_key,
            file_size_bytes=file_size_bytes,
            page_count=page_count,
        )
        await self._repository.commit()
        return VersionRegistration(document=document, version=version, deduplicated=False)

    async def get_version(self, version_id: UUID) -> DocumentVersion:
        version = await self._repository.get_version(version_id)
        if version is None:
            raise DocumentVersionNotFoundError
        return version

    async def get_latest_version(self, document_id: UUID) -> DocumentVersion:
        version = await self._repository.get_latest_version(document_id)
        if version is None:
            raise DocumentVersionNotFoundError
        return version

    async def list_versions(
        self,
        knowledge_base_id: UUID,
        *,
        limit: int,
        cursor: str | None,
    ) -> DocumentVersionPage:
        after = self._decode_cursor(cursor) if cursor else None
        rows = await self._repository.list_versions(
            knowledge_base_id,
            limit=limit + 1,
            after=after,
        )
        has_more = len(rows) > limit
        selected = rows[:limit]
        runs = await self._repository.get_latest_ingestion_runs(
            [version.id for _, version in selected]
        )
        items = [
            DocumentVersionRecord(
                document=document,
                version=version,
                ingestion_run=runs.get(version.id),
            )
            for document, version in selected
        ]
        next_cursor = self._encode_cursor(items[-1].version) if has_more else None
        return DocumentVersionPage(items=items, next_cursor=next_cursor)

    @staticmethod
    def _encode_cursor(version: DocumentVersion) -> str:
        value = f"{version.created_at.isoformat()}|{version.id}"
        return urlsafe_b64encode(value.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = urlsafe_b64decode(padded.encode()).decode()
            created_at_value, version_id_value = value.split("|", maxsplit=1)
            created_at = datetime.fromisoformat(created_at_value)
            if created_at.tzinfo is None:
                raise ValueError
            return created_at, UUID(version_id_value)
        except (ValueError, UnicodeDecodeError) as error:
            raise InvalidDocumentCursorError from error


class DocumentUploadService:
    def __init__(
        self,
        *,
        documents: DocumentService,
        knowledge_bases: KnowledgeBaseReaderPort,
        validator: PdfValidatorPort,
        storage: DocumentStoragePort,
        ingestion_queue: IngestionQueuePort,
        max_upload_bytes: int,
    ) -> None:
        self._documents = documents
        self._knowledge_bases = knowledge_bases
        self._validator = validator
        self._storage = storage
        self._ingestion_queue = ingestion_queue
        self._max_upload_bytes = max_upload_bytes

    async def upload(
        self,
        knowledge_base_id: UUID,
        uploaded_file: UploadedFilePort,
    ) -> DocumentUpload:
        await self._knowledge_bases.get(knowledge_base_id)
        file_name = normalize_document_name(uploaded_file.filename or "")
        with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as staged:
            checksum, file_size = await self._stage(uploaded_file, staged)
            metadata = await self._validator.validate(staged)
            storage_key = await self._storage.store(
                knowledge_base_id,
                checksum,
                staged,
            )
            registration = await self._documents.register_version(
                knowledge_base_id,
                file_name=file_name,
                checksum_sha256=checksum,
                storage_key=storage_key,
                file_size_bytes=file_size,
                page_count=metadata.page_count,
            )
            if not registration.deduplicated or registration.version.status == "pending":
                await self._ingestion_queue.enqueue(registration.version.id)
        return DocumentUpload(registration=registration)

    async def _stage(
        self,
        uploaded_file: UploadedFilePort,
        staged: SpooledTemporaryFile[bytes],
    ) -> tuple[str, int]:
        digest = sha256()
        total = 0
        first_chunk = True
        async for chunk in self._chunks(uploaded_file):
            if first_chunk:
                first_chunk = False
                if not chunk.startswith(b"%PDF-"):
                    raise InvalidPdfSignatureError
            total += len(chunk)
            if total > self._max_upload_bytes:
                raise PdfFileTooLargeError
            digest.update(chunk)
            staged.write(chunk)
        if first_chunk:
            raise InvalidPdfSignatureError
        staged.seek(0)
        return digest.hexdigest(), total

    @staticmethod
    async def _chunks(uploaded_file: UploadedFilePort) -> AsyncIterator[bytes]:
        while chunk := await uploaded_file.read(READ_CHUNK_SIZE):
            yield chunk
