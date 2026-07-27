import asyncio
from io import BytesIO
from pathlib import Path
from typing import Protocol
from uuid import UUID

from httpx import AsyncClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from pytest import MonkeyPatch
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.core.config import get_settings
from sourcetrace.modules.documents.models import DocumentVersion
from sourcetrace.modules.documents.repository import DocumentRepository


class RecordingIngestionQueue(Protocol):
    version_ids: list[UUID]


def text_pdf_bytes(text: str = "SourceTrace evidence") -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
    )
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def blank_pdf_bytes(*, page_count: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def encrypted_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(text_pdf_bytes())))
    writer.encrypt("secret")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


async def stored_pdf_files(root: Path) -> list[Path]:
    return await asyncio.to_thread(lambda: list(root.rglob("*.pdf")))


async def test_user_can_upload_a_text_pdf_as_a_pending_version(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    ingestion_queue: RecordingIngestionQueue,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]
    pdf = text_pdf_bytes()

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("paper.pdf", pdf, "application/octet-stream")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["name"] == "paper.pdf"
    assert body["version_number"] == 1
    assert body["file_size_bytes"] == len(pdf)
    assert body["page_count"] == 1
    assert body["status"] == "pending"
    assert body["deduplicated"] is False
    assert body["request_id"]
    assert ingestion_queue.version_ids == [UUID(body["version_id"])]


async def test_uploaded_version_remains_visible_after_refresh(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]
    upload_response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("paper.pdf", text_pdf_bytes(), "application/pdf")},
    )
    version_id = upload_response.json()["version_id"]

    response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        params={"limit": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert len(body["items"]) == 1
    assert body["items"][0]["version_id"] == version_id
    assert body["items"][0]["status"] == "pending"


async def test_document_list_returns_the_persisted_ingestion_status(
    client: AsyncClient,
    session: AsyncSession,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]
    upload_response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("paper.pdf", text_pdf_bytes(), "application/pdf")},
    )
    version_id = upload_response.json()["version_id"]
    await session.execute(
        update(DocumentVersion).where(DocumentVersion.id == version_id).values(status="processing")
    )
    await session.commit()

    response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["status"] == "processing"


async def test_document_list_exposes_latest_ingestion_progress(
    client: AsyncClient,
    session: AsyncSession,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    knowledge_base_id = (
        await client.post("/api/v1/knowledge-bases", json={"name": "Research"})
    ).json()["id"]
    version_id = (
        await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            files={"file": ("paper.pdf", text_pdf_bytes(), "application/pdf")},
        )
    ).json()["version_id"]
    repository = DocumentRepository(session)
    run = await repository.create_ingestion_run(
        UUID(version_id),
        parser_version="pypdf-v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_config_version="token-window-v1",
    )
    run.status = "processing"
    run.stage = "chunking"
    run.attempt_count = 2
    await repository.commit()

    response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents"
    )

    item = response.json()["items"][0]
    assert item["status"] == "processing"
    assert item["stage"] == "chunking"
    assert item["attempt_count"] == 2
    assert item["retryable"] is False
    assert item["failure_code"] is None


async def test_user_can_retry_a_recoverable_failed_ingestion(
    client: AsyncClient,
    session: AsyncSession,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    ingestion_queue: RecordingIngestionQueue,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    knowledge_base_id = (
        await client.post("/api/v1/knowledge-bases", json={"name": "Research"})
    ).json()["id"]
    version_id = UUID(
        (
            await client.post(
                f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
                files={"file": ("paper.pdf", text_pdf_bytes(), "application/pdf")},
            )
        ).json()["version_id"]
    )
    repository = DocumentRepository(session)
    run = await repository.create_ingestion_run(
        version_id,
        parser_version="pypdf-v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_config_version="token-window-v1",
    )
    run.status = "failed"
    run.stage = "failed"
    run.attempt_count = 3
    run.retryable = True
    run.failure_code = "STORAGE_UNAVAILABLE"
    await repository.commit()

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents/{version_id}/retry"
    )

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert response.json()["stage"] == "queued"
    assert response.json()["attempt_count"] == 0
    assert ingestion_queue.version_ids == [version_id, version_id]


async def test_permanent_ingestion_failure_cannot_be_manually_retried(
    client: AsyncClient,
    session: AsyncSession,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    ingestion_queue: RecordingIngestionQueue,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    knowledge_base_id = (
        await client.post("/api/v1/knowledge-bases", json={"name": "Research"})
    ).json()["id"]
    version_id = UUID(
        (
            await client.post(
                f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
                files={"file": ("paper.pdf", text_pdf_bytes(), "application/pdf")},
            )
        ).json()["version_id"]
    )
    repository = DocumentRepository(session)
    run = await repository.create_ingestion_run(
        version_id,
        parser_version="pypdf-v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_config_version="token-window-v1",
    )
    run.status = "failed"
    run.stage = "failed"
    run.attempt_count = 1
    run.retryable = False
    run.failure_code = "OCR_NOT_SUPPORTED"
    await repository.commit()

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents/{version_id}/retry"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "INGESTION_NOT_RETRYABLE"
    assert ingestion_queue.version_ids == [version_id]


async def test_exact_duplicate_returns_the_existing_version(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    ingestion_queue: RecordingIngestionQueue,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]
    pdf = text_pdf_bytes()
    first = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("paper.pdf", pdf, "application/pdf")},
    )

    duplicate = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("renamed.pdf", pdf, "text/plain")},
    )

    assert first.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.json()["deduplicated"] is True
    assert duplicate.json()["document_id"] == first.json()["document_id"]
    assert duplicate.json()["version_id"] == first.json()["version_id"]
    assert ingestion_queue.version_ids == [
        UUID(first.json()["version_id"]),
        UUID(first.json()["version_id"]),
    ]


async def test_file_extension_and_client_media_type_cannot_fake_a_pdf(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("fake.pdf", b"SourceTrace private content", "application/pdf")},
    )

    assert response.status_code == 415
    body = response.json()
    assert body["code"] == "UNSUPPORTED_FILE_TYPE"
    assert body["request_id"]
    assert "private content" not in str(body)


async def test_pdf_over_the_configured_size_limit_is_rejected(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    pdf = text_pdf_bytes()
    monkeypatch.setattr(settings, "max_upload_bytes", len(pdf) - 1)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("paper.pdf", pdf, "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "PDF_TOO_LARGE"
    assert response.json()["request_id"]


async def test_pdf_over_500_pages_is_rejected(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("long.pdf", blank_pdf_bytes(page_count=501), "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "PDF_PAGE_LIMIT_EXCEEDED"
    assert response.json()["request_id"]


async def test_encrypted_pdf_is_rejected(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("secret.pdf", encrypted_pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "PDF_ENCRYPTED"
    assert response.json()["request_id"]


async def test_corrupt_pdf_is_rejected(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("broken.pdf", b"%PDF-1.7\nbroken", "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "PDF_CORRUPT"
    assert response.json()["request_id"]


async def test_textless_pdf_is_accepted_for_async_ocr_detection(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    ingestion_queue: RecordingIngestionQueue,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("scan.pdf", blank_pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert ingestion_queue.version_ids == [UUID(response.json()["version_id"])]


async def test_document_versions_are_cursor_paginated_within_the_knowledge_base(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]
    uploaded_ids: set[str] = set()
    for text in ("first version", "second version", "third version"):
        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            files={"file": ("paper.pdf", text_pdf_bytes(text), "application/pdf")},
        )
        assert response.status_code == 202
        uploaded_ids.add(response.json()["version_id"])

    first_page_response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        params={"limit": 2},
    )
    first_page = first_page_response.json()
    second_page_response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        params={"limit": 2, "cursor": first_page["next_cursor"]},
    )
    second_page = second_page_response.json()

    assert first_page_response.status_code == 200
    assert len(first_page["items"]) == 2
    assert first_page["next_cursor"]
    assert second_page_response.status_code == 200
    assert len(second_page["items"]) == 1
    assert second_page["next_cursor"] is None
    listed_ids = {item["version_id"] for item in first_page["items"] + second_page["items"]}
    assert listed_ids == uploaded_ids


async def test_document_list_rejects_an_invalid_cursor_with_problem_details(
    client: AsyncClient,
) -> None:
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]

    response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        params={"cursor": "not-a-cursor"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_CURSOR"
    assert response.json()["request_id"]


async def test_upload_to_a_missing_knowledge_base_returns_problem_details(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)

    response = await client.post(
        "/api/v1/knowledge-bases/00000000-0000-0000-0000-000000000001/documents",
        files={"file": ("paper.pdf", text_pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "KNOWLEDGE_BASE_NOT_FOUND"
    assert response.json()["request_id"]


async def test_invalid_file_name_is_rejected_before_the_pdf_is_stored(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]

    response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (f"{'a' * 252}.pdf", text_pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_DOCUMENT_NAME"
    assert await stored_pdf_files(tmp_path) == []


async def test_permanently_deleting_a_knowledge_base_removes_its_source_files(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research"},
    )
    knowledge_base_id = create_response.json()["id"]
    upload_response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("paper.pdf", text_pdf_bytes(), "application/pdf")},
    )
    assert upload_response.status_code == 202
    assert len(await stored_pdf_files(tmp_path)) == 1

    delete_response = await client.delete(
        f"/api/v1/knowledge-bases/{knowledge_base_id}",
        params={"confirm": "true"},
    )

    assert delete_response.status_code == 204
    assert await stored_pdf_files(tmp_path) == []
