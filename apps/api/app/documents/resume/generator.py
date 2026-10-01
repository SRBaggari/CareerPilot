"""Resume generation: select, order, and word content for one job.

Both generators only *choose* among the candidate's existing records and evidence (by ID).
Record facts (employers, titles, dates, degrees, certification names) are always copied
from the profile, so no generator can add a job, project, or certification or alter a date.
The output is a draft; every claim in it is verified before anything is kept.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.ai.provider import LLMJsonResult, LLMProvider
from app.ai.untrusted import safe_json, with_rules
from app.documents.resume.content import (
    AchievementEntry,
    CertificationEntry,
    Claim,
    CourseworkEntry,
    EducationEntry,
    ExperienceEntry,
    Header,
    ProjectEntry,
    ResumeContent,
)
from app.documents.resume.workspace import EvidenceItem, Workspace
from app.profiles.models import (
    Achievement,
    CandidateProfile,
    Certification,
    Coursework,
    Education,
    Project,
    WorkExperience,
)

MAX_PROJECTS, MAX_BULLETS_PROJECT, MAX_BULLETS_JOB = 4, 3, 4
MAX_SKILLS, MAX_COURSEWORK, MAX_ACHIEVEMENTS, MAX_CERTIFICATIONS = 16, 8, 4, 6
COURSEWORK_MIN_RELEVANCE = 0.2


@dataclass
class Draft:
    content: ResumeContent
    notes: list[str] = field(default_factory=list)
    # Generated claims rejected before verification (e.g. a skill not in the profile).
    prefiltered: list[tuple[str, str, str]] = field(default_factory=list)  # (section, text, why)


def _claim(item: EvidenceItem) -> Claim:
    return Claim(text=item.content, evidence_ids=[item.id])


def skill_evidence(ws: Workspace, name: str) -> list[EvidenceItem]:
    found = list(ws.skill_evidence.get(name.lower(), []))
    pattern = re.compile(rf"(?<![\w]){re.escape(name.lower())}(?![\w])")
    found += [
        e
        for e in ws.evidence.values()
        if e not in found and pattern.search(f"{e.context}\n{e.content}".lower())
    ]
    return sorted(found, key=lambda e: -e.relevance)


# Record facts are copied from the profile, never generated: these are the only way an
# entry is built, both for generation and when re-deriving an edited resume.
def header_of(p: CandidateProfile) -> Header:
    return Header(
        full_name=p.full_name,
        headline=p.headline,
        contact_email=p.contact_email,
        phone=p.phone,
        location=p.location,
        website_url=p.website_url,
        linkedin_url=p.linkedin_url,
        github_url=p.github_url,
    )


def experience_of(j: WorkExperience, bullets: list[Claim] | None = None) -> ExperienceEntry:
    return ExperienceEntry(
        record_id=j.id,
        title=j.title,
        company_name=j.company_name,
        location=j.location,
        start_date=j.start_date,
        end_date=j.end_date,
        is_current=j.is_current,
        bullets=bullets or [],
    )


def project_of(r: Project, bullets: list[Claim] | None = None) -> ProjectEntry:
    return ProjectEntry(
        record_id=r.id,
        title=r.title,
        role=r.role,
        url=r.repository_url or r.project_url,
        start_date=r.start_date,
        end_date=r.end_date,
        bullets=bullets or [],
    )


def education_of(e: Education) -> EducationEntry:
    return EducationEntry(
        record_id=e.id,
        institution=e.institution,
        degree=e.degree,
        field_of_study=e.field_of_study,
        start_date=e.start_date,
        end_date=e.end_date,
        gpa=e.gpa,
        gpa_scale=e.gpa_scale,
    )


def certification_of(c: Certification) -> CertificationEntry:
    return CertificationEntry(record_id=c.id, name=c.name, issuer=c.issuer, issue_date=c.issue_date)


def achievement_of(a: Achievement) -> AchievementEntry:
    return AchievementEntry(record_id=a.id, title=a.title, achieved_on=a.achieved_on)


def coursework_of(c: Coursework) -> CourseworkEntry:
    return CourseworkEntry(record_id=c.id, course_name=c.course_name)


def base_content(ws: Workspace) -> ResumeContent:
    """Record facts only: header, all jobs, education, and records in relevance order."""
    p = ws.profile
    rel = ws.record_relevance
    # Current role first, then most recent end (or start) date first.
    jobs = sorted(
        p.work_experiences,
        key=lambda j: (not j.is_current, -(j.end_date or j.start_date or date.min).toordinal()),
    )
    return ResumeContent(
        header=header_of(p),
        experience=[experience_of(j) for j in jobs],
        projects=[
            project_of(r)
            for r in sorted(p.projects, key=lambda r: -rel.get(r.id, 0.0))
            if ws.by_subject.get(r.id)
        ],
        education=[
            education_of(e)
            for e in sorted(
                p.educations, key=lambda e: (e.end_date is None, e.end_date), reverse=True
            )
        ],
        certifications=[
            certification_of(c)
            for c in sorted(p.certifications, key=lambda c: -rel.get(c.id, 0.0))[
                :MAX_CERTIFICATIONS
            ]
        ],
        achievements=[
            achievement_of(a)
            for a in sorted(p.achievements, key=lambda a: -rel.get(a.id, 0.0))[:MAX_ACHIEVEMENTS]
        ],
        coursework=[
            coursework_of(c)
            for c in sorted(p.coursework, key=lambda c: -rel.get(c.id, 0.0))
            if rel.get(c.id, 0.0) >= COURSEWORK_MIN_RELEVANCE
        ][:MAX_COURSEWORK],
    )


def rule_skills(ws: Workspace) -> tuple[list[Claim], list[str]]:
    """Profile skills that evidence supports, job-relevant first; plus the ones left out."""
    required = {c.lower() for c in ws.required_concepts}
    preferred = {c.lower() for c in ws.preferred_concepts}
    ranked, unsupported = [], []
    for candidate_skill in ws.profile.skills:
        name = candidate_skill.skill.name
        support = skill_evidence(ws, name)
        if not support:
            unsupported.append(name)
            continue
        key = name.lower()
        tier = 0 if key in required else 1 if key in preferred else 2
        ranked.append(
            (
                tier,
                -support[0].relevance,
                Claim(text=name, evidence_ids=[e.id for e in support[:3]]),
            )
        )
    ranked.sort(key=lambda t: (t[0], t[1]))
    return [claim for *_, claim in ranked][:MAX_SKILLS], sorted(unsupported)


class RuleGenerator:
    """Deterministic: selects and orders by relevance; bullets are the evidence verbatim."""

    name = "rules"

    def generate(self, ws: Workspace) -> Draft:
        content = base_content(ws)
        for job in content.experience:
            job.bullets = [_claim(e) for e in ws.evidence_for(job.record_id)[:MAX_BULLETS_JOB]]
        content.projects = content.projects[:MAX_PROJECTS]
        for project in content.projects:
            project.bullets = [
                _claim(e) for e in ws.evidence_for(project.record_id)[:MAX_BULLETS_PROJECT]
            ]
        content.skills, unsupported = rule_skills(ws)
        notes = []
        if unsupported:
            notes.append(
                "Left out of Skills because no verified evidence mentions them: "
                + ", ".join(unsupported)
                + ". Add a highlight that shows each one."
            )
        return Draft(content, notes)


# --- LLM generator ----------------------------------------------------------------------

PROMPT_VERSION = "resume-tailoring-v1"
SYSTEM_PROMPT = """You tailor a candidate's resume to one job using ONLY their own evidence.

You receive the job's requirements and the candidate's records (jobs, projects, coursework,
achievements), each with verified evidence statements that have IDs, plus the skills the
candidate lists. Choose and order what is most relevant, and word the bullets for this job.

Hard rules - violations are detected and removed:
- Never invent skills, projects, employers, certifications, metrics, or dates.
- Every bullet and summary sentence must cite the evidence IDs it rests on, and say nothing
  those statements don't say. Keep every number exactly as written in the evidence.
- Don't exaggerate: no "led", "managed", "architected", "senior", "expert" unless the
  evidence says so. Don't add adjectives like "scalable" or "production-grade".
- Only use record IDs and evidence IDs you were given; bullets for a record may only cite
  that record's evidence. Skills must come from the candidate's skill list.
- Rephrasing is allowed to use the job's vocabulary for the same fact (e.g. "RAG" vs
  "retrieval-augmented generation"); when in doubt, stay close to the evidence wording.
- The summary is optional: at most two sentences, each citing evidence."""

_CLAIM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "evidence_ids"],
    "additionalProperties": False,
}
TAILOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": _CLAIM},
        "skills": {"type": "array", "items": {"type": "string"}},
        "project_ids": {"type": "array", "items": {"type": "string"}},
        "coursework_ids": {"type": "array", "items": {"type": "string"}},
        "achievement_ids": {"type": "array", "items": {"type": "string"}},
        "bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"record_id": {"type": "string"}, **_CLAIM["properties"]},
                "required": ["record_id", "text", "evidence_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "summary",
        "skills",
        "project_ids",
        "coursework_ids",
        "achievement_ids",
        "bullets",
    ],
    "additionalProperties": False,
}


def build_prompt(ws: Workspace) -> str:
    def records(items: list[Any], label: Any) -> list[dict[str, Any]]:
        return [
            {
                "record_id": str(i.id),
                "record": label(i),
                "evidence": [
                    {"evidence_id": str(e.id), "text": e.content} for e in ws.evidence_for(i.id)
                ],
            }
            for i in items
        ]

    p = ws.profile
    payload = {
        "job": {
            "title": ws.job.title,
            "company": ws.job.company_name,
            "requirements": [
                f"[{r.importance.value}] {r.description}"
                for r in ws.job.requirements
                if r.importance.value != "informational"
            ],
        },
        "jobs": records(p.work_experiences, lambda j: f"{j.title} at {j.company_name}"),
        "projects": records(p.projects, lambda r: r.title),
        "achievements": records(p.achievements, lambda a: a.title),
        "coursework": [{"record_id": str(c.id), "record": c.course_name} for c in p.coursework],
        "skills": [s.skill.name for s in p.skills],
        "profile_evidence": [
            {"evidence_id": str(e.id), "text": e.content}
            for e in ws.evidence.values()
            if e.subject_id is None
        ],
    }
    body = safe_json(payload, indent=1)
    return f"<candidate_and_job>\n{body}\n</candidate_and_job>\n\nTailor the resume."


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


class LLMGenerator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.name = f"llm:{provider.model}"[:50]

    async def generate(self, ws: Workspace) -> tuple[Draft, LLMJsonResult]:
        result = await self.provider.complete_json(
            system=with_rules(SYSTEM_PROMPT), prompt=build_prompt(ws), schema=TAILOR_SCHEMA
        )
        return map_llm_output(result.data, ws), result


def map_llm_output(data: dict[str, Any], ws: Workspace) -> Draft:
    """Apply the model's choices to the record facts, keeping only known IDs."""
    content = base_content(ws)
    draft = Draft(content)
    rule = RuleGenerator().generate(ws)

    def ids(values: Any) -> list[uuid.UUID]:
        return [u for u in (_uuid(v) for v in values or []) if u is not None]

    by_project = {p.record_id: p for p in content.projects}
    chosen = [by_project[i] for i in dict.fromkeys(ids(data.get("project_ids"))) if i in by_project]
    unknown_projects = [v for v in data.get("project_ids") or [] if _uuid(v) not in by_project]
    for value in unknown_projects:
        draft.prefiltered.append(("projects", str(value), "Not one of the candidate's projects."))
    content.projects = (chosen or content.projects)[:MAX_PROJECTS]

    by_course = {c.id: c for c in ws.profile.coursework}
    courses = [
        by_course[i] for i in dict.fromkeys(ids(data.get("coursework_ids"))) if i in by_course
    ]
    if courses:
        content.coursework = [coursework_of(c) for c in courses][:MAX_COURSEWORK]
    by_achievement = {a.id: a for a in ws.profile.achievements}
    wins = [
        by_achievement[i]
        for i in dict.fromkeys(ids(data.get("achievement_ids")))
        if i in by_achievement
    ]
    if wins:
        content.achievements = [achievement_of(a) for a in wins][:MAX_ACHIEVEMENTS]

    entries: dict[uuid.UUID, ExperienceEntry | ProjectEntry] = {
        **{e.record_id: e for e in content.experience},
        **{p.record_id: p for p in content.projects},
    }
    for bullet in data.get("bullets") or []:
        record = _uuid(bullet.get("record_id"))
        text = str(bullet.get("text") or "").strip()
        if record is None or record not in entries or not text:
            draft.prefiltered.append(
                ("bullets", text, "Refers to a record the candidate doesn't have.")
            )
            continue
        own = {e.id for e in ws.by_subject.get(record, [])}
        cited = [i for i in ids(bullet.get("evidence_ids")) if i in own]
        entries[record].bullets.append(Claim(text=text, evidence_ids=cited))
    # Records the model didn't write bullets for keep their verbatim evidence.
    rule_bullets = {e.record_id: e.bullets for e in rule.content.experience}
    rule_bullets |= {p.record_id: p.bullets for p in rule.content.projects}
    for record_id, entry in entries.items():
        limit = MAX_BULLETS_JOB if isinstance(entry, ExperienceEntry) else MAX_BULLETS_PROJECT
        entry.bullets = (entry.bullets or rule_bullets.get(record_id, []))[:limit]

    profile_skills = {s.skill.name.lower(): s.skill.name for s in ws.profile.skills}
    for name in dict.fromkeys(str(s).strip() for s in data.get("skills") or []):
        canonical = profile_skills.get(name.lower())
        if canonical is None:
            draft.prefiltered.append(("skills", name, "Not in the candidate's skill list."))
            continue
        support = skill_evidence(ws, canonical)
        content.skills.append(Claim(text=canonical, evidence_ids=[e.id for e in support[:3]]))
    content.skills = content.skills[:MAX_SKILLS] or rule.content.skills
    draft.notes = rule.notes

    for sentence in (data.get("summary") or [])[:2]:
        cited = [i for i in ids(sentence.get("evidence_ids")) if i in ws.evidence]
        text = str(sentence.get("text") or "").strip()
        if text:
            content.summary.append(Claim(text=text, evidence_ids=cited))
    return draft
