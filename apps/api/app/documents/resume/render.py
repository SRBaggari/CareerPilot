"""Render a tailored resume to DOCX or PDF.

Both formats come from one layout (``_blocks``), so they always carry the same text as the
preview: record facts and verified claims only, nothing added at render time.
"""

import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.shared import Pt
from fpdf import FPDF

from app.documents.resume.content import ResumeContent

BlockKind = Literal["name", "contact", "heading", "entry", "subline", "bullet", "text"]


@dataclass(frozen=True)
class Block:
    kind: BlockKind
    text: str
    right: str = ""  # right-aligned text (dates) for "entry" blocks


def month_year(value: date | None) -> str:
    return value.strftime("%b %Y") if value else ""


def date_range(start: date | None, end: date | None, current: bool = False) -> str:
    finish = "Present" if current else month_year(end)
    begin = month_year(start)
    if begin and finish:
        return f"{begin} \u2013 {finish}"
    return begin or finish


def _decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _blocks(c: ResumeContent) -> list[Block]:
    h = c.header
    out = [Block("name", h.full_name)]
    if h.headline:
        out.append(Block("subline", h.headline))
    contact = [h.contact_email, h.phone, h.location, h.linkedin_url, h.github_url, h.website_url]
    if line := " | ".join(x for x in contact if x):
        out.append(Block("contact", line))

    if c.summary:
        out += [Block("heading", "Summary"), Block("text", " ".join(s.text for s in c.summary))]
    if c.skills:
        out += [Block("heading", "Skills"), Block("text", ", ".join(s.text for s in c.skills))]
    if c.experience:
        out.append(Block("heading", "Experience"))
        for job in c.experience:
            out.append(
                Block(
                    "entry",
                    f"{job.title}, {job.company_name}",
                    date_range(job.start_date, job.end_date, job.is_current),
                )
            )
            if job.location:
                out.append(Block("subline", job.location))
            out += [Block("bullet", b.text) for b in job.bullets]
    if c.projects:
        out.append(Block("heading", "Projects"))
        for project in c.projects:
            title = f"{project.title} ({project.role})" if project.role else project.title
            out.append(Block("entry", title, date_range(project.start_date, project.end_date)))
            if project.url:
                out.append(Block("subline", project.url))
            out += [Block("bullet", b.text) for b in project.bullets]
    if c.education:
        out.append(Block("heading", "Education"))
        for school in c.education:
            degree = ", ".join(x for x in (school.degree, school.field_of_study) if x)
            out.append(
                Block(
                    "entry",
                    f"{degree} \u2013 {school.institution}" if degree else school.institution,
                    date_range(school.start_date, school.end_date),
                )
            )
            if school.gpa is not None:
                scale = f"/{_decimal(school.gpa_scale)}" if school.gpa_scale is not None else ""
                out.append(Block("subline", f"GPA {_decimal(school.gpa)}{scale}"))
    if c.coursework:
        out += [
            Block("heading", "Relevant Coursework"),
            Block("text", ", ".join(x.course_name for x in c.coursework)),
        ]
    if c.certifications:
        out.append(Block("heading", "Certifications"))
        for cert in c.certifications:
            name = f"{cert.name}, {cert.issuer}" if cert.issuer else cert.name
            out.append(Block("entry", name, month_year(cert.issue_date)))
    if c.achievements:
        out.append(Block("heading", "Achievements"))
        out += [Block("entry", a.title, month_year(a.achieved_on)) for a in c.achievements]
    return out


def to_docx(content: ResumeContent) -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    width = doc.sections[0].page_width - doc.sections[0].left_margin - doc.sections[0].right_margin  # type: ignore[operator]
    for block in _blocks(content):
        if block.kind == "name":
            doc.add_heading(block.text, level=0)
        elif block.kind == "heading":
            doc.add_heading(block.text, level=1)
        elif block.kind == "bullet":
            doc.add_paragraph(block.text, style="List Bullet")
        elif block.kind == "entry":
            paragraph = doc.add_paragraph()
            paragraph.add_run(block.text).bold = True
            if block.right:
                paragraph.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)
                paragraph.add_run(f"\t{block.right}")
        else:
            paragraph = doc.add_paragraph(block.text)
            if block.kind in ("contact", "subline"):
                paragraph.runs[0].italic = block.kind == "subline"
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# The PDF core fonts only cover Latin-1; map common typography and drop the rest.
_PDF_REPLACEMENTS = {
    "\u2013": "-",
    "\u2014": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2022": "-",
    "\u2026": "...",
    "\u20b9": "INR ",
    "\u20ac": "EUR ",
    "\u00a0": " ",
}


def pdf_text(text: str) -> str:
    text = re.sub("|".join(_PDF_REPLACEMENTS), lambda m: _PDF_REPLACEMENTS[m.group(0)], text)
    return text.encode("latin-1", "replace").decode("latin-1")


def to_pdf(content: ResumeContent, created: datetime | None = None) -> bytes:
    """``created`` pins the PDF's creation date, so the same content gives the same bytes."""
    pdf = FPDF(format="A4")
    if created is not None:
        pdf.set_creation_date(created)
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin
    for block in _blocks(content):
        text = pdf_text(block.text)
        if block.kind == "name":
            pdf.set_font("Helvetica", "B", 18)
            pdf.multi_cell(width, 9, text, new_x="LMARGIN", new_y="NEXT")
        elif block.kind == "heading":
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 11.5)
            pdf.multi_cell(width, 6, text.upper(), new_x="LMARGIN", new_y="NEXT")
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
            pdf.ln(1.5)
        elif block.kind == "entry":
            pdf.set_font("Helvetica", "", 10)
            right = pdf_text(block.right)
            right_width = pdf.get_string_width(right) + 2 if right else 0
            pdf.set_font("Helvetica", "B", 10)
            y = pdf.get_y()
            pdf.multi_cell(width - right_width, 5.5, text, new_x="LMARGIN", new_y="NEXT")
            if right:
                end_y = pdf.get_y()
                pdf.set_xy(pdf.l_margin + width - right_width, y)
                pdf.set_font("Helvetica", "", 10)
                pdf.cell(right_width, 5.5, right, align="R")
                pdf.set_xy(pdf.l_margin, end_y)
        elif block.kind == "bullet":
            pdf.set_font("Helvetica", "", 10)
            pdf.set_x(pdf.l_margin + 3)
            pdf.cell(4, 5, "-")
            pdf.multi_cell(width - 7, 5, text, new_x="LMARGIN", new_y="NEXT")
        else:
            italic = "I" if block.kind == "subline" else ""
            pdf.set_font("Helvetica", italic, 9.5 if block.kind == "contact" else 10)
            pdf.multi_cell(width, 5, text, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
