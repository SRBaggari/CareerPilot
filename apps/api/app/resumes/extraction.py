"""Text extraction from uploaded PDF and DOCX resumes.

The format is decided from the file's content (magic bytes), never trusted from the
filename or Content-Type alone, and inputs are bounded to guard against malicious files.
"""

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Any

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.profiles.models import DocumentFormat

MAX_PDF_PAGES = 20
MAX_DOCX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024  # a resume's XML is far smaller
MAX_DOCX_ENTRIES = 2000
MIN_TEXT_CHARS = 50
MAX_TEXT_CHARS = 100_000
_LEADING = bytes([0xEF, 0xBB, 0xBF, 0x20, 0x09, 0x0D, 0x0A])  # BOM and whitespace
EXTRACTION_TIMEOUT_SECONDS = 20


class ResumeFileError(ValueError):
    """The upload is not a readable resume. The message is safe to show to the user."""


@dataclass(frozen=True)
class ExtractedText:
    text: str
    page_count: int | None


def detect_format(data: bytes, filename: str) -> DocumentFormat:
    """Identify the document type from its bytes and check it matches the extension."""
    name = filename.lower()
    if name.endswith(".doc") or data.startswith(b"\xd0\xcf\x11\xe0"):
        raise ResumeFileError("Legacy .doc files aren't supported. Save it as .docx or PDF.")
    # The signature must start the file (after optional whitespace or a BOM): a "%PDF-"
    # buried in other content would let polyglot files through.
    if data.lstrip(_LEADING).startswith(b"%PDF-"):
        detected = DocumentFormat.PDF
    elif data.startswith(b"PK\x03\x04"):
        detected = DocumentFormat.DOCX
    else:
        raise ResumeFileError("Unsupported file. Upload a PDF or DOCX resume.")
    expected_ext = f".{detected.value}"
    if not name.endswith(expected_ext):
        raise ResumeFileError(f"The file content is {detected.value.upper()}, but its name "
                              f"doesn't end in {expected_ext}.")  # fmt: skip
    return detected


_BULLET_GLYPHS = "\u2022\u25cf\u25aa\u25e6\u2023\u2219\uf0b7\uf0a7\uf076\uf0d8\u27a2\u25ba"


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\x00", "")
    text = re.sub(f"[{_BULLET_GLYPHS}]", "\u2022", text)  # one canonical bullet
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_pdf(data: bytes) -> ExtractedText:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ResumeFileError("This PDF is password-protected. Upload an unlocked copy.")
        page_count = len(reader.pages)
        if page_count > MAX_PDF_PAGES:
            raise ResumeFileError(f"PDF has {page_count} pages; the limit is {MAX_PDF_PAGES}.")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ResumeFileError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise ResumeFileError("The PDF could not be read. It may be damaged.") from exc
    except Exception as exc:  # crafted PDFs raise many kinds (recursion, index, ...)
        raise ResumeFileError("The PDF could not be read. It may be damaged.") from exc
    return ExtractedText("\n".join(pages), page_count)


def _check_docx_container(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = {e.filename for e in entries}
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, ValueError) as exc:
        raise ResumeFileError("The DOCX file is damaged.") from exc
    if "word/document.xml" not in names:
        raise ResumeFileError("This file is not a Word document.")
    total = sum(e.file_size for e in entries)
    if len(entries) > MAX_DOCX_ENTRIES or total > MAX_DOCX_UNCOMPRESSED_BYTES:
        raise ResumeFileError("The DOCX file is too large to process.")


def _is_list_paragraph(paragraph: Paragraph) -> bool:
    properties = paragraph._p.pPr
    has_numbering = properties is not None and properties.numPr is not None
    style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
    return has_numbering or "list" in style


def _extract_docx(data: bytes) -> ExtractedText:
    _check_docx_container(data)
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises a variety of parser errors
        raise ResumeFileError("The DOCX file could not be read.") from exc

    try:
        return _docx_text(document)
    except Exception as exc:
        raise ResumeFileError("The DOCX file could not be read.") from exc


def _docx_text(document: Any) -> ExtractedText:
    lines: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                lines.append(f"\u2022 {text}" if _is_list_paragraph(block) else text)
        elif isinstance(block, Table):
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    lines.append(" | ".join(dict.fromkeys(cells)))  # merged cells repeat
    return ExtractedText("\n".join(lines), None)


def extract_text(data: bytes, file_format: DocumentFormat) -> ExtractedText:
    raw = _extract_pdf(data) if file_format == DocumentFormat.PDF else _extract_docx(data)
    text = normalize_text(raw.text)
    if len(text) < MIN_TEXT_CHARS:
        raise ResumeFileError(
            "No readable text was found. Scanned or image-only resumes aren't supported yet; "
            "upload a PDF with selectable text or a DOCX."
        )
    if len(text) > MAX_TEXT_CHARS:
        raise ResumeFileError("The resume contains too much text to be a resume.")
    return ExtractedText(text, raw.page_count)
