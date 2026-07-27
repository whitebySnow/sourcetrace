from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentVersionItem(BaseModel):
    document_id: UUID
    version_id: UUID
    name: str
    version_number: int
    checksum_sha256: str
    file_size_bytes: int
    page_count: int
    status: str = Field(min_length=1, max_length=24)
    created_at: datetime


class DocumentUploadResponse(DocumentVersionItem):
    deduplicated: bool
    request_id: str


class DocumentVersionListResponse(BaseModel):
    items: list[DocumentVersionItem]
    next_cursor: str | None = Field(default=None)
