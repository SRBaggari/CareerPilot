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


class Claim(BaseModel):
    claim_id: uuid.UUID | None = None  # set once stored as a GeneratedClaim
    text: str
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)


class Header(BaseModel):
    full_name: str
    headline: str | None = None
    contact_email: str | None = None
    phone: str | None = None
    location: str | None = None
    website_url: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None


class ExperienceEntry(BaseModel):
    record_id: uuid.UUID
    title: str
    company_name: str
    location: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    bullets: list[Claim] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    record_id: uuid.UUID
    title: str
    role: str | None = None
    url: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    bullets: list[Claim] = Field(default_factory=list)


class EducationEntry(BaseModel):
    record_id: uuid.UUID
    institution: str
    degree: str | None = None
    field_of_study: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    gpa: Decimal | None = None
    gpa_scale: Decimal | None = None


class CertificationEntry(BaseModel):
    record_id: uuid.UUID
    name: str
    issuer: str | None = None
    issue_date: date | None = None


class AchievementEntry(BaseModel):
    record_id: uuid.UUID
    title: str
    achieved_on: date | None = None


class CourseworkEntry(BaseModel):
    record_id: uuid.UUID
    course_name: str


class ResumeContent(BaseModel):
    header: Header
    summary: list[Claim] = Field(default_factory=list)
    skills: list[Claim] = Field(default_factory=list)  # text = the skill name
    experience: list[ExperienceEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)
    achievements: list[AchievementEntry] = Field(default_factory=list)
    coursework: list[CourseworkEntry] = Field(default_factory=list)

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
