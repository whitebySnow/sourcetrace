import asyncio
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sourcetrace.modules.documents.models import DocumentVersion
from sourcetrace.modules.documents.repository import DocumentRepository
from sourcetrace.modules.documents.service import (
    DocumentService,
    DocumentVersionNotFoundError,
    InvalidDocumentChecksumError,
    InvalidDocumentNameError,
)
from sourcetrace.modules.knowledge_bases.repository import KnowledgeBaseRepository
from sourcetrace.modules.knowledge_bases.service import KnowledgeBaseService


async def test_registers_the_first_immutable_version_for_a_document(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    service = DocumentService(DocumentRepository(session))

    registration = await service.register_version(
        knowledge_base.id,
        file_name="paper.pdf",
        checksum_sha256="a" * 64,
    )
    retrieved = await service.get_version(registration.version.id)

    assert registration.deduplicated is False
    assert registration.document.name == "paper.pdf"
    assert registration.version.version_number == 1
    assert registration.version.checksum_sha256 == "a" * 64
    assert retrieved.id == registration.version.id
    assert retrieved.created_at.tzinfo is not None


async def test_deduplicates_matching_content_within_a_knowledge_base(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    service = DocumentService(DocumentRepository(session))
    first = await service.register_version(
        knowledge_base.id,
        file_name="paper.pdf",
        checksum_sha256="b" * 64,
    )

    duplicate = await service.register_version(
        knowledge_base.id,
        file_name="renamed-paper.pdf",
        checksum_sha256="b" * 64,
    )

    assert duplicate.deduplicated is True
    assert duplicate.document.id == first.document.id
    assert duplicate.version.id == first.version.id


async def test_same_file_name_with_changed_content_creates_a_new_version(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    service = DocumentService(DocumentRepository(session))
    first = await service.register_version(
        knowledge_base.id,
        file_name="paper.pdf",
        checksum_sha256="c" * 64,
    )

    changed = await service.register_version(
        knowledge_base.id,
        file_name="paper.pdf",
        checksum_sha256="d" * 64,
    )
    historical = await service.get_version(first.version.id)
    latest = await service.get_latest_version(first.document.id)

    assert changed.deduplicated is False
    assert changed.document.id == first.document.id
    assert changed.version.version_number == 2
    assert historical.id == first.version.id
    assert historical.checksum_sha256 == "c" * 64
    assert latest.id == changed.version.id


@pytest.mark.parametrize("checksum", ["A" * 64, "a" * 63, "g" * 64])
async def test_rejects_noncanonical_sha256_checksums(
    session: AsyncSession,
    checksum: str,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    service = DocumentService(DocumentRepository(session))

    with pytest.raises(InvalidDocumentChecksumError):
        await service.register_version(
            knowledge_base.id,
            file_name="paper.pdf",
            checksum_sha256=checksum,
        )


@pytest.mark.parametrize("file_name", ["   ", "a" * 256])
async def test_rejects_invalid_document_names(
    session: AsyncSession,
    file_name: str,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    service = DocumentService(DocumentRepository(session))

    with pytest.raises(InvalidDocumentNameError):
        await service.register_version(
            knowledge_base.id,
            file_name=file_name,
            checksum_sha256="e" * 64,
        )


async def test_database_rejects_changes_to_immutable_version_identity(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )
    service = DocumentService(DocumentRepository(session))
    registration = await service.register_version(
        knowledge_base.id,
        file_name="paper.pdf",
        checksum_sha256="f" * 64,
    )
    version_id = registration.version.id

    with pytest.raises(DBAPIError):
        await session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == version_id)
            .values(checksum_sha256="0" * 64)
        )
    await session.rollback()

    preserved = await service.get_version(version_id)
    assert preserved.checksum_sha256 == "f" * 64


async def test_deleting_a_knowledge_base_cascades_to_document_versions(
    session: AsyncSession,
) -> None:
    knowledge_base_service = KnowledgeBaseService(KnowledgeBaseRepository(session))
    knowledge_base = await knowledge_base_service.create("Research")
    document_service = DocumentService(DocumentRepository(session))
    registration = await document_service.register_version(
        knowledge_base.id,
        file_name="paper.pdf",
        checksum_sha256="1" * 64,
    )

    await knowledge_base_service.delete(knowledge_base.id)

    with pytest.raises(DocumentVersionNotFoundError):
        await document_service.get_version(registration.version.id)


async def test_concurrent_matching_checksums_resolve_to_one_version(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Research"
    )

    async def register(file_name: str) -> tuple[bool, UUID]:
        async with session_factory() as concurrent_session:
            registration = await DocumentService(
                DocumentRepository(concurrent_session)
            ).register_version(
                knowledge_base.id,
                file_name=file_name,
                checksum_sha256="2" * 64,
            )
            return registration.deduplicated, registration.version.id

    first, second = await asyncio.gather(
        register("paper.pdf"),
        register("renamed-paper.pdf"),
    )

    assert {first[0], second[0]} == {False, True}
    assert first[1] == second[1]
