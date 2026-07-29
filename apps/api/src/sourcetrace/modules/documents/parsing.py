import asyncio
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from sourcetrace.modules.documents.ingestion import (
    ParsedPage,
    PermanentIngestionError,
    TransientIngestionError,
)


class SourceFileLocatorPort(Protocol):
    def source_path(self, storage_key: str) -> Path: ...


class PypdfDocumentParser:
    version = "pypdf-v2"

    def __init__(self, storage: SourceFileLocatorPort) -> None:
        self._storage = storage

    async def parse(self, storage_key: str) -> list[ParsedPage]:
        source_path = self._storage.source_path(storage_key)
        return await asyncio.to_thread(self._parse_sync, source_path)

    @staticmethod
    def _parse_sync(source_path: Path) -> list[ParsedPage]:
        try:
            reader = PdfReader(source_path, strict=True)
            if reader.is_encrypted:
                raise PermanentIngestionError(
                    "PDF_ENCRYPTED",
                    "Encrypted or password-protected PDFs are not supported",
                )
            pages = [
                ParsedPage(
                    page_number=index,
                    text=(page.extract_text() or "").replace("\x00", "").strip(),
                )
                for index, page in enumerate(reader.pages, start=1)
            ]
        except PermanentIngestionError:
            raise
        except FileNotFoundError as error:
            raise TransientIngestionError(
                "SOURCE_FILE_UNAVAILABLE",
                "The source PDF is temporarily unavailable",
            ) from error
        except OSError as error:
            raise TransientIngestionError(
                "SOURCE_STORAGE_UNAVAILABLE",
                "Source storage is temporarily unavailable",
            ) from error
        except (PdfReadError, EOFError, ValueError, TypeError) as error:
            raise PermanentIngestionError(
                "PDF_CORRUPT",
                "PDF is corrupt or malformed",
            ) from error
        if not pages or not any(page.text for page in pages):
            raise PermanentIngestionError(
                "OCR_NOT_SUPPORTED",
                "PDF contains no extractable text; OCR is not supported",
            )
        return pages
