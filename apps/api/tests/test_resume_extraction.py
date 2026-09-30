"""PDF/DOCX text extraction and file validation (no database needed)."""

import pytest

from app.profiles.models import DocumentFormat
from app.resumes import extraction
from app.resumes.extraction import ResumeFileError, detect_format, extract_text

from .resume_files import SAMPLE_LINES, encrypt_pdf, make_docx, make_pdf, make_zip


def test_pdf_text_is_extracted_with_bullets_normalized() -> None:
    result = extract_text(make_pdf(SAMPLE_LINES), DocumentFormat.PDF)
    assert result.page_count == 1
    assert "PRIYA SHARMA" in result.text
    assert "\u2022 Implemented RAG pipeline using document retrieval" in result.text


def test_docx_list_paragraphs_become_bullets_and_tables_are_read() -> None:
    data = make_docx(SAMPLE_LINES, table=[["Languages", "Python, Go"]])
    text = extract_text(data, DocumentFormat.DOCX).text
    assert "\u2022 Built a document classification service" in text
    assert "Languages | Python, Go" in text


@pytest.mark.parametrize(
    ("data", "name", "expected"),
    [
        (make_pdf(SAMPLE_LINES), "cv.pdf", DocumentFormat.PDF),
        (make_pdf(SAMPLE_LINES), "CV.PDF", DocumentFormat.PDF),
        (make_docx(SAMPLE_LINES), "cv.docx", DocumentFormat.DOCX),
    ],
    ids=["pdf", "pdf-uppercase-ext", "docx"],
)
def test_format_detected_from_content(data: bytes, name: str, expected: DocumentFormat) -> None:
    assert detect_format(data, name) == expected


@pytest.mark.parametrize(
    ("data", "name", "message"),
    [
        (make_pdf(SAMPLE_LINES), "cv.docx", "content is PDF"),
        (make_docx(SAMPLE_LINES), "cv.pdf", "content is DOCX"),
        (b"\xd0\xcf\x11\xe0legacy", "cv.doc", "Legacy .doc"),
        (b"Plain text resume", "cv.txt", "Unsupported file"),
        (b"\x89PNG\r\n\x1a\n", "cv.pdf", "Unsupported file"),
    ],
    ids=["pdf-named-docx", "docx-named-pdf", "legacy-doc", "plain-text", "png"],
)
def test_wrong_or_unsupported_files_are_rejected(data: bytes, name: str, message: str) -> None:
    with pytest.raises(ResumeFileError, match=message):
        detect_format(data, name)


def test_image_only_pdf_is_rejected_with_guidance() -> None:
    with pytest.raises(ResumeFileError, match="Scanned or image-only"):
        extract_text(make_pdf([""]), DocumentFormat.PDF)


def test_password_protected_pdf_is_rejected() -> None:
    with pytest.raises(ResumeFileError, match="password-protected"):
        extract_text(encrypt_pdf(make_pdf(SAMPLE_LINES)), DocumentFormat.PDF)


def test_pdf_page_limit() -> None:
    too_long = make_pdf(SAMPLE_LINES, pages=extraction.MAX_PDF_PAGES + 1)
    with pytest.raises(ResumeFileError, match="pages"):
        extract_text(too_long, DocumentFormat.PDF)


def test_corrupt_pdf_is_rejected() -> None:
    with pytest.raises(ResumeFileError, match="could not be read"):
        extract_text(b"%PDF-1.4\nthis is not really a pdf", DocumentFormat.PDF)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"PK\x03\x04 not a zip", "damaged"),
        (make_zip({"hello.txt": b"hi"}), "not a Word document"),
    ],
    ids=["broken-zip", "zip-without-document"],
)
def test_corrupt_or_foreign_docx_is_rejected(data: bytes, message: str) -> None:
    with pytest.raises(ResumeFileError, match=message):
        extract_text(data, DocumentFormat.DOCX)


def test_zip_bomb_docx_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extraction, "MAX_DOCX_UNCOMPRESSED_BYTES", 10_000)
    bomb = make_zip({"word/document.xml": b"<w:document/>", "word/big.bin": b"\0" * 20_000})
    with pytest.raises(ResumeFileError, match="too large"):
        extract_text(bomb, DocumentFormat.DOCX)


def test_normalize_text_unifies_bullets_and_whitespace() -> None:
    assert (
        extraction.normalize_text("\uf0b7  A\u00a0\u00a0b\n\n\n\n\u25cf C")
        == "\u2022 A b\n\n\u2022 C"
    )
