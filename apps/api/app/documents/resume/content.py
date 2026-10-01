"""The tailored resume document model (stored as JSON in ``tailored_resumes.content``).

Two kinds of content:

- **Record facts** (names, employers, titles, dates, degrees, certification names) are
  copied verbatim from the candidate's profile records when the resume is built. They are
  never generated, so they can't be invented or altered.
- **Claims** (summary sentences, bullets, listed skills) are generated text. Each carries
  the evidence it rests on and is verified against that evidence before it is kept.
"""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

# Generous limits (a real resume is far smaller) that bound what an edit can make the
# server embed, verify and store.
TEXT = 2000
NAME = 300
URL = 2000
ITEMS = 50


class Claim(BaseModel):
    claim_id: uuid.UUID | None = None  # set once stored as a GeneratedClaim
    text: str = Field(max_length=TEXT)
    evidence_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class Header(BaseModel):
    full_name: str = Field(max_length=NAME)
    headline: str | None = Field(default=None, max_length=NAME)
    contact_email: str | None = Field(default=None, max_length=NAME)
    phone: str | None = Field(default=None, max_length=NAME)
    location: str | None = Field(default=None, max_length=NAME)
    website_url: str | None = Field(default=None, max_length=URL)
    linkedin_url: str | None = Field(default=None, max_length=URL)
    github_url: str | None = Field(default=None, max_length=URL)


class ExperienceEntry(BaseModel):
    record_id: uuid.UUID
    title: str = Field(max_length=NAME)
    company_name: str = Field(max_length=NAME)
    location: str | None = Field(default=None, max_length=NAME)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    bullets: list[Claim] = Field(default_factory=list, max_length=20)


class ProjectEntry(BaseModel):
    record_id: uuid.UUID
    title: str = Field(max_length=NAME)
    role: str | None = Field(default=None, max_length=NAME)
    url: str | None = Field(default=None, max_length=URL)
    start_date: date | None = None
    end_date: date | None = None
    bullets: list[Claim] = Field(default_factory=list, max_length=20)


class EducationEntry(BaseModel):
    record_id: uuid.UUID
    institution: str = Field(max_length=NAME)
    degree: str | None = Field(default=None, max_length=NAME)
    field_of_study: str | None = Field(default=None, max_length=NAME)
    start_date: date | None = None
    end_date: date | None = None
    gpa: Decimal | None = None
    gpa_scale: Decimal | None = None


class CertificationEntry(BaseModel):
    record_id: uuid.UUID
    name: str = Field(max_length=NAME)
    issuer: str | None = Field(default=None, max_length=NAME)
    issue_date: date | None = None


class AchievementEntry(BaseModel):
    record_id: uuid.UUID
    title: str = Field(max_length=NAME)
    achieved_on: date | None = None


class CourseworkEntry(BaseModel):
    record_id: uuid.UUID
    course_name: str = Field(max_length=NAME)


class ResumeContent(BaseModel):
    header: Header
    summary: list[Claim] = Field(default_factory=list, max_length=ITEMS)
    skills: list[Claim] = Field(default_factory=list, max_length=200)  # text = the skill name
    experience: list[ExperienceEntry] = Field(default_factory=list, max_length=ITEMS)
    projects: list[ProjectEntry] = Field(default_factory=list, max_length=ITEMS)
    education: list[EducationEntry] = Field(default_factory=list, max_length=ITEMS)
    certifications: list[CertificationEntry] = Field(default_factory=list, max_length=ITEMS)
    achievements: list[AchievementEntry] = Field(default_factory=list, max_length=ITEMS)
    coursework: list[CourseworkEntry] = Field(default_factory=list, max_length=ITEMS)

    def claims(self) -> list[tuple[str, int, Claim]]:
        """Every claim with its section key and position (the claim-extraction step)."""
        found: list[tuple[str, int, Claim]] = []
        found += [("summary", i, c) for i, c in enumerate(self.summary)]
        found += [("skills", i, c) for i, c in enumerate(self.skills)]
        for section, entries in (("experience", self.experience), ("projects", self.projects)):
            for entry in entries:
                found += [
                    (f"{section}:{entry.record_id}", i, c) for i, c in enumerate(entry.bullets)
                ]
        return found
