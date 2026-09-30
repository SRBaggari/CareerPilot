"""The cover letter document model (stored as JSON in ``cover_letters.content``).

- **Job facts** (``job_title``, ``company_name``) come from the analyzed job posting.
- **The signature** comes from the candidate's profile.
- **Sentences** are generated text. A sentence that says something about the candidate
  cites the evidence it rests on and must be SUPPORTED by the verification engine. A
  greeting, a statement of intent or a courtesy ("I would welcome the chance to discuss
  the role.") makes no factual claim; the engine checks that it really doesn't.
"""

import uuid

from pydantic import BaseModel, Field


class LetterSentence(BaseModel):
    claim_id: uuid.UUID | None = None  # set once stored as a GeneratedClaim
    text: str
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)


class LetterParagraph(BaseModel):
    sentences: list[LetterSentence] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences)


class Signature(BaseModel):
    full_name: str
    contact_email: str | None = None
    phone: str | None = None
    location: str | None = None


class CoverLetterContent(BaseModel):
    job_title: str
    company_name: str
    signature: Signature
    greeting: str
    paragraphs: list[LetterParagraph] = Field(default_factory=list)
    closing: str = "Sincerely,"

    def sentences(self) -> list[tuple[str, int, LetterSentence]]:
        """Every generated sentence with its section key ("paragraphs:<n>") and position."""
        return [
            (f"paragraphs:{p}", n, sentence)
            for p, paragraph in enumerate(self.paragraphs)
            for n, sentence in enumerate(paragraph.sentences)
        ]

    def word_count(self) -> int:
        body = " ".join(p.text for p in self.paragraphs)
        return len(f"{self.greeting} {body} {self.closing}".split())
