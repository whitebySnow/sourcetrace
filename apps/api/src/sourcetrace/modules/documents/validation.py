import asyncio
from tempfile import SpooledTemporaryFile

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from sourcetrace.modules.documents.service import PdfMetadata


class EncryptedPdfError(ValueError):
    pass


class CorruptPdfError(ValueError):
    pass


class PdfPageLimitExceededError(ValueError):
    pass


class PdfTextNotFoundError(ValueError):
    pass


class PypdfDocumentValidator:
    def __init__(self, *, max_pages: int) -> None:
        self._max_pages = max_pages

    async def validate(self, content: SpooledTemporaryFile[bytes]) -> PdfMetadata:
        return await asyncio.to_thread(self._validate_sync, content)

    def _validate_sync(self, content: SpooledTemporaryFile[bytes]) -> PdfMetadata:
        try:
            content.seek(0)
            reader = PdfReader(content, strict=True)
            if reader.is_encrypted:
                raise EncryptedPdfError
            page_count = len(reader.pages)
            if page_count > self._max_pages:
                raise PdfPageLimitExceededError
            has_text = any((page.extract_text() or "").strip() for page in reader.pages)
            if page_count == 0 or not has_text:
                raise PdfTextNotFoundError
            return PdfMetadata(page_count=page_count)
        except (EncryptedPdfError, PdfPageLimitExceededError, PdfTextNotFoundError):
            raise
        except (PdfReadError, EOFError, ValueError, TypeError) as error:
            raise CorruptPdfError from error
        finally:
            content.seek(0)
