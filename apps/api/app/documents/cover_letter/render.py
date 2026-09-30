"""Render a cover letter to DOCX or PDF: exactly the previewed text, nothing added except
the letter date and the "Re:" line, which come from the letter's own metadata."""

import io
from datetime import date

from docx import Document
from docx.shared import Pt
from fpdf import FPDF

from app.documents.cover_letter.content import CoverLetterContent
from app.documents.resume.render import pdf_text


def _header(content: CoverLetterContent) -> tuple[str, str]:
    s = content.signature
    contact = " | ".join(x for x in (s.contact_email, s.phone, s.location) if x)
    return s.full_name, contact


def _dated(on: date) -> str:
    return f"{on.day} {on.strftime('%B %Y')}"


def to_docx(content: CoverLetterContent, on: date) -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    name, contact = _header(content)
    doc.add_paragraph().add_run(name).bold = True
    if contact:
        doc.add_paragraph(contact)
    doc.add_paragraph(_dated(on))
    doc.add_paragraph(f"Re: {content.job_title}, {content.company_name}")
    doc.add_paragraph(content.greeting)
    for paragraph in content.paragraphs:
        doc.add_paragraph(paragraph.text)
    doc.add_paragraph(content.closing)
    doc.add_paragraph(name)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def to_pdf(content: CoverLetterContent, on: date) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_margins(22, 20, 22)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin
    name, contact = _header(content)

    def block(text: str, *, style: str = "", size: float = 11, after: float = 4) -> None:
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(width, 5.8, pdf_text(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(after)

    block(name, style="B", size=14, after=0.5)
    if contact:
        block(contact, size=9.5, after=6)
    block(_dated(on))
    block(f"Re: {content.job_title}, {content.company_name}", style="B")
    block(content.greeting)
    for paragraph in content.paragraphs:
        block(paragraph.text)
    block(content.closing, after=1)
    block(name)
    return bytes(pdf.output())
