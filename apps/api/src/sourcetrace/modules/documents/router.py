from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.core.config import get_settings
from sourcetrace.core.errors import AppError, ErrorResponse
from sourcetrace.db.session import get_session
from sourcetrace.modules.documents.repository import DocumentRepository
from sourcetrace.modules.documents.schemas import (
    DocumentUploadResponse,
    DocumentVersionItem,
    DocumentVersionListResponse,
)
from sourcetrace.modules.documents.service import (
    DocumentService,
    DocumentUpload,
    DocumentUploadService,
    DocumentVersionRecord,
    InvalidDocumentCursorError,
    InvalidDocumentNameError,
    InvalidPdfSignatureError,
    PdfFileTooLargeError,
)
from sourcetrace.modules.documents.storage import LocalDocumentStorage
from sourcetrace.modules.documents.validation import (
    CorruptPdfError,
    EncryptedPdfError,
    PdfPageLimitExceededError,
    PdfTextNotFoundError,
    PypdfDocumentValidator,
)
from sourcetrace.modules.knowledge_bases.repository import KnowledgeBaseRepository
from sourcetrace.modules.knowledge_bases.service import (
    KnowledgeBaseNotFoundError,
    KnowledgeBaseService,
)

router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/documents", tags=["documents"])


def get_document_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentService:
    return DocumentService(DocumentRepository(session))


def get_knowledge_base_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBaseService:
    return KnowledgeBaseService(KnowledgeBaseRepository(session))


def get_document_upload_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentUploadService:
    settings = get_settings()
    return DocumentUploadService(
        documents=DocumentService(DocumentRepository(session)),
        knowledge_bases=KnowledgeBaseService(KnowledgeBaseRepository(session)),
        validator=PypdfDocumentValidator(max_pages=settings.max_pdf_pages),
        storage=LocalDocumentStorage(settings.upload_dir),
        max_upload_bytes=settings.max_upload_bytes,
    )


UploadServiceDependency = Annotated[
    DocumentUploadService,
    Depends(get_document_upload_service),
]
DocumentServiceDependency = Annotated[DocumentService, Depends(get_document_service)]
KnowledgeBaseServiceDependency = Annotated[
    KnowledgeBaseService,
    Depends(get_knowledge_base_service),
]
PageLimit = Annotated[int, Query(ge=1, le=100)]


def _item(record: DocumentVersionRecord) -> DocumentVersionItem:
    version = record.version
    if version.file_size_bytes is None or version.page_count is None:
        raise RuntimeError("uploaded document version is missing file metadata")
    return DocumentVersionItem(
        document_id=record.document.id,
        version_id=version.id,
        name=record.document.name,
        version_number=version.version_number,
        checksum_sha256=version.checksum_sha256,
        file_size_bytes=version.file_size_bytes,
        page_count=version.page_count,
        status=version.status,
        created_at=version.created_at,
    )


def _upload_response(upload: DocumentUpload, request_id: str) -> DocumentUploadResponse:
    registration = upload.registration
    item = _item(
        DocumentVersionRecord(
            document=registration.document,
            version=registration.version,
        )
    )
    return DocumentUploadResponse(
        **item.model_dump(),
        deduplicated=registration.deduplicated,
        request_id=request_id,
    )


@router.post(
    "",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_200_OK: {
            "model": DocumentUploadResponse,
            "description": "Exact duplicate resolved to the existing document version",
        },
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": ErrorResponse},
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    },
)
async def upload_document(
    knowledge_base_id: UUID,
    request: Request,
    response: Response,
    service: UploadServiceDependency,
    file: Annotated[UploadFile, File()],
) -> DocumentUploadResponse:
    try:
        upload = await service.upload(knowledge_base_id, file)
    except KnowledgeBaseNotFoundError as error:
        raise AppError(
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="Knowledge base not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    except PdfFileTooLargeError as error:
        raise AppError(
            code="PDF_TOO_LARGE",
            message="PDF exceeds the configured upload size limit",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        ) from error
    except InvalidPdfSignatureError as error:
        raise AppError(
            code="UNSUPPORTED_FILE_TYPE",
            message="The uploaded file is not a PDF",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        ) from error
    except EncryptedPdfError as error:
        raise AppError(
            code="PDF_ENCRYPTED",
            message="Encrypted or password-protected PDFs are not supported",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from error
    except PdfPageLimitExceededError as error:
        raise AppError(
            code="PDF_PAGE_LIMIT_EXCEEDED",
            message="PDF exceeds the configured page limit",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from error
    except PdfTextNotFoundError as error:
        raise AppError(
            code="PDF_TEXT_NOT_FOUND",
            message="PDF contains no extractable text; OCR is not supported",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from error
    except CorruptPdfError as error:
        raise AppError(
            code="PDF_CORRUPT",
            message="PDF is corrupt or malformed",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from error
    except InvalidDocumentNameError as error:
        raise AppError(
            code="INVALID_DOCUMENT_NAME",
            message="Document file name is invalid",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from error
    if upload.registration.deduplicated:
        response.status_code = status.HTTP_200_OK
    return _upload_response(upload, request.state.request_id)


@router.get(
    "",
    response_model=DocumentVersionListResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    },
)
async def list_document_versions(
    knowledge_base_id: UUID,
    documents: DocumentServiceDependency,
    knowledge_bases: KnowledgeBaseServiceDependency,
    limit: PageLimit = 20,
    cursor: str | None = None,
) -> DocumentVersionListResponse:
    try:
        await knowledge_bases.get(knowledge_base_id)
        page = await documents.list_versions(
            knowledge_base_id,
            limit=limit,
            cursor=cursor,
        )
    except KnowledgeBaseNotFoundError as error:
        raise AppError(
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="Knowledge base not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    except InvalidDocumentCursorError as error:
        raise AppError(
            code="INVALID_CURSOR",
            message="The pagination cursor is invalid",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from error
    return DocumentVersionListResponse(
        items=[_item(item) for item in page.items],
        next_cursor=page.next_cursor,
    )
