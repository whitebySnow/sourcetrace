from io import BytesIO
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import UUID

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from sourcetrace.modules.documents.ingestion import PermanentIngestionError
from sourcetrace.modules.documents.parsing import PypdfDocumentParser
from sourcetrace.modules.documents.storage import LocalDocumentStorage


def two_page_pdf(*, include_text: bool) -> bytes:
    writer = PdfWriter()
    first = writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    if include_text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_reference = writer._add_object(font)
        first[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
        )
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 72 720 Td (page one evidence) Tj ET")
        first[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


async def stored_pdf(storage: LocalDocumentStorage, content: bytes) -> str:
    knowledge_base_id = UUID("4a43e866-5694-4d4c-955d-69d1a58a2a17")
    with SpooledTemporaryFile(mode="w+b") as source:
        source.write(content)
        source.seek(0)
        return await storage.store(knowledge_base_id, "a" * 64, source)


async def test_parser_preserves_page_numbers_and_blank_pages(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    storage_key = await stored_pdf(storage, two_page_pdf(include_text=True))

    pages = await PypdfDocumentParser(storage).parse(storage_key)

    assert [(page.page_number, page.text) for page in pages] == [
        (1, "page one evidence"),
        (2, ""),
    ]


async def test_parser_rejects_a_document_with_no_extractable_text(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    storage_key = await stored_pdf(storage, two_page_pdf(include_text=False))

    with pytest.raises(PermanentIngestionError) as captured:
        await PypdfDocumentParser(storage).parse(storage_key)

    assert captured.value.code == "OCR_NOT_SUPPORTED"
    assert captured.value.safe_message == "PDF contains no extractable text; OCR is not supported"
