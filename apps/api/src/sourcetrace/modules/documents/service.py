import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sourcetrace.modules.documents.models import Document, DocumentVersion
from sourcetrace.modules.documents.repository import DocumentWriteConflictError


class DocumentVersionNotFoundError(Exception):
    pass


class InvalidDocumentChecksumError(ValueError):
    pass


class InvalidDocumentNameError(ValueError):
    pass


class DocumentRegistrationConflictError(Exception):
    pass


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_REGISTRATION_ATTEMPTS = 3


@dataclass(frozen=True)
class VersionRegistration:
    document: Document
    version: DocumentVersion
    deduplicated: bool


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
    ) -> DocumentVersion: ...

    async def get_version(self, version_id: UUID) -> DocumentVersion | None: ...

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
    ) -> VersionRegistration:
        if SHA256_PATTERN.fullmatch(checksum_sha256) is None:
            raise InvalidDocumentChecksumError

        normalized_name = file_name.strip()
        if not normalized_name or len(normalized_name) > 255:
            raise InvalidDocumentNameError

        last_conflict: DocumentWriteConflictError | None = None
        for _attempt in range(MAX_REGISTRATION_ATTEMPTS):
            try:
                return await self._register_version(
                    knowledge_base_id,
                    normalized_name=normalized_name,
                    checksum_sha256=checksum_sha256,
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
