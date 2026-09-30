"""Claim extraction: turn a document into the list of claims to verify.

Every statement is extracted, including record facts (employers, titles, dates, degrees),
so the engine checks the whole document and not only what a generator chose to label as
generated.
"""

import re

from app.documents.cover_letter.content import CoverLetterContent
from app.documents.resume.content import ResumeContent
from app.verification.types import ClaimInput, ClaimType


def _dates(start: object, end: object, current: bool = False) -> str:
    finish = "present" if current else (str(end) if end else "")
    begin = str(start) if start else ""
    return f"{begin} to {finish}" if begin and finish else begin or finish


def extract_resume_claims(content: ResumeContent) -> list[ClaimInput]:
    claims: list[ClaimInput] = []
    h = content.header
    claims.append(
        ClaimInput(
            text=h.full_name,
            claim_type=ClaimType.CONTACT,
            section="header",
            facts=h.model_dump(),
        )
    )
    claims += [
        ClaimInput(c.text, ClaimType.SUMMARY, list(c.evidence_ids), "summary", i)
        for i, c in enumerate(content.summary)
    ]
    claims += [
        ClaimInput(c.text, ClaimType.SKILL, list(c.evidence_ids), "skills", i)
        for i, c in enumerate(content.skills)
    ]
    for i, job in enumerate(content.experience):
        section = f"experience:{job.record_id}"
        claims.append(
            ClaimInput(
                text=f"{job.title}, {job.company_name} "
                f"({_dates(job.start_date, job.end_date, job.is_current)})",
                claim_type=ClaimType.EMPLOYMENT,
                section="experience",
                position=i,
                record_id=job.record_id,
                facts=job.model_dump(exclude={"bullets", "record_id"}),
            )
        )
        claims += [
            ClaimInput(
                b.text, ClaimType.EXPERIENCE, list(b.evidence_ids), section, n, job.record_id
            )
            for n, b in enumerate(job.bullets)
        ]
    for i, project in enumerate(content.projects):
        section = f"projects:{project.record_id}"
        claims.append(
            ClaimInput(
                text=project.title,
                claim_type=ClaimType.PROJECT_ENTRY,
                section="projects",
                position=i,
                record_id=project.record_id,
                facts=project.model_dump(exclude={"bullets", "record_id"}),
            )
        )
        claims += [
            ClaimInput(
                b.text, ClaimType.PROJECT, list(b.evidence_ids), section, n, project.record_id
            )
            for n, b in enumerate(project.bullets)
        ]
    record_sections = (
        ("education", ClaimType.EDUCATION, content.education, "institution"),
        ("certifications", ClaimType.CERTIFICATION, content.certifications, "name"),
        ("achievements", ClaimType.ACHIEVEMENT, content.achievements, "title"),
        ("coursework", ClaimType.COURSEWORK, content.coursework, "course_name"),
    )
    for section, claim_type, entries, label in record_sections:
        for i, entry in enumerate(entries):
            claims.append(
                ClaimInput(
                    text=str(getattr(entry, label)),
                    claim_type=claim_type,
                    section=section,
                    position=i,
                    record_id=getattr(entry, "record_id", None),
                    facts=entry.model_dump(exclude={"record_id"}),
                )
            )
    return claims


_BULLET = re.compile(r"^\s*(?:[-*\u2022\u25aa\u2013]|\d+[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\u201c(])")


def split_sentences(text: str) -> list[str]:
    """Sentences of a paragraph or bullet list, in order."""
    found: list[str] = []
    for line in text.splitlines():
        line = _BULLET.sub("", line).strip()
        found += [s.strip() for s in _SENTENCE_END.split(line) if len(s.strip()) >= 3]
    return found


def extract_cover_letter_claims(content: CoverLetterContent) -> list[ClaimInput]:
    """The signature (checked against the profile) and every sentence of the letter.

    Greeting, body and closing are all extracted: the engine decides which sentences make
    factual claims and which only state intent or courtesy.
    """
    allowed = {"allowed_names": [content.job_title, content.company_name]}
    claims = [
        ClaimInput(
            text=content.signature.full_name,
            claim_type=ClaimType.CONTACT,
            section="signature",
            facts=content.signature.model_dump(),
        ),
        ClaimInput(content.greeting, ClaimType.LETTER, section="greeting", facts=allowed),
    ]
    for section, position, sentence in content.sentences():
        claims.append(
            ClaimInput(
                sentence.text,
                ClaimType.LETTER,
                list(sentence.evidence_ids),
                section,
                position,
                facts=allowed,
            )
        )
    claims.append(ClaimInput(content.closing, ClaimType.LETTER, section="closing", facts=allowed))
    return claims


def extract_text_claims(text: str) -> list[ClaimInput]:
    """Split free text (a pasted paragraph or bullet list) into one claim per sentence."""
    claims: list[ClaimInput] = []
    for line in text.splitlines():
        line = _BULLET.sub("", line).strip()
        for sentence in _SENTENCE_END.split(line):
            sentence = sentence.strip()
            if len(sentence) >= 3:
                claims.append(
                    ClaimInput(sentence, ClaimType.STATEMENT, section="text", position=len(claims))
                )
    return claims
