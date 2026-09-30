"""Cover letter generators.

Both produce a concise letter whose factual sentences cite the candidate's verified
evidence. Neither verifies its own output: the claim verification engine does that, and
unapproved sentences are regenerated (``revise``) or removed by the service.

- ``RuleLetterGenerator``: deterministic. Frames the strongest matched evidence in the
  first person without adding facts ("As a Machine Learning Intern at Acme Analytics, I
  deployed ML models with Docker on AWS."), plus an evidence-backed skills sentence and
  non-factual opening and closing sentences.
- ``LLMLetterGenerator``: an LLM writes the letter from the same evidence (structured
  output), and can rewrite sentences the engine rejected.
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.ai.provider import LLMJsonResult, LLMProvider
from app.documents.cover_letter.content import (
    CoverLetterContent,
    LetterParagraph,
    LetterSentence,
    Signature,
)
from app.documents.resume.generator import skill_evidence
from app.documents.resume.workspace import EvidenceItem, Workspace
from app.jobs.models import RequirementImportance
from app.matching.models import MatchStatus
from app.matching.schemas import MatchReportOut
from app.profiles.models import CandidateProfile

MAX_PARAGRAPHS, MAX_SENTENCES = 5, 6
MAX_WORK_SENTENCES, MAX_PROJECT_SENTENCES, MAX_SKILLS = 3, 2, 5
_IRREGULAR_PAST = frozenset(
    [
        "built",
        "led",
        "wrote",
        "ran",
        "made",
        "won",
        "taught",
        "drew",
        "grew",
        "held",
        "kept",
        "spoke",
        "shipped",
        "set",
        "began",
        "bought",
        "brought",
        "chose",
        "did",
        "found",
        "gave",
        "got",
        "sent",
        "spent",
        "took",
        "thought",
        "understood",
    ]
)


@dataclass
class LetterDraft:
    content: CoverLetterContent
    notes: list[str] = field(default_factory=list)


def greeting_for(company: str) -> str:
    return f"Dear {company} Hiring Team,"


def opening_for(title: str, company: str) -> str:
    return f"I am writing to apply for the {title} position at {company}."


def closing_sentences(title: str) -> list[str]:
    return [
        f"I would welcome the opportunity to discuss the {title} role with you.",
        "Thank you for your time and consideration.",
    ]


def signature_from(p: CandidateProfile) -> Signature:
    """The letter's signature: always the profile as it is."""
    return Signature(
        full_name=p.full_name, contact_email=p.contact_email, phone=p.phone, location=p.location
    )


def _first_person(evidence: str) -> str | None:
    """'Deployed ML models ...' -> 'I deployed ML models ...'; None if it isn't an action."""
    text = evidence.strip().rstrip(".")
    first = text.split(" ", 1)[0]
    if not re.fullmatch(r"[A-Z][a-z]+", first):
        return None
    lower = first.lower()
    if not (lower.endswith("ed") or lower in _IRREGULAR_PAST):
        return None
    return f"I {lower}{text[len(first) :]}"


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def frame(item: EvidenceItem, ws: Workspace, *, follow_up: bool = False) -> str | None:
    """A first-person sentence that says exactly what the evidence says, in its context.
    A ``follow_up`` about the same item as the previous sentence reads "I also ..."."""
    action = _first_person(item.content)
    if action is None:
        return None
    if follow_up:
        return f"I also {action.removeprefix('I ')}."
    jobs = {j.id: j for j in ws.profile.work_experiences}
    projects = {p.id: p for p in ws.profile.projects}
    if (job := jobs.get(item.subject_id)) is not None:  # type: ignore[arg-type]
        return f"As {_article(job.title)} {job.title} at {job.company_name}, {action}."
    if (project := projects.get(item.subject_id)) is not None:  # type: ignore[arg-type]
        return f"In my {project.title} project, {action}."
    return None


def _strongest_evidence(ws: Workspace, report: MatchReportOut | None) -> list[EvidenceItem]:
    """Evidence cited for matched requirements (required first), then by job relevance."""
    order: list[uuid.UUID] = []
    if report is not None:
        ranked = sorted(
            (r for r in report.requirements if r.match_status == MatchStatus.MATCHED),
            key=lambda r: r.importance != RequirementImportance.REQUIRED,
        )
        order += [i for r in ranked for i in r.evidence_ids]
    order += [e.id for e in sorted(ws.evidence.values(), key=lambda e: -e.relevance)]
    return [ws.evidence[i] for i in dict.fromkeys(order) if i in ws.evidence]


def _missing(report: MatchReportOut | None) -> list[str]:
    if report is None:
        return []
    return [
        r.requirement
        for r in report.requirements
        if r.importance == RequirementImportance.REQUIRED and r.match_status == MatchStatus.MISSING
    ]


class RuleLetterGenerator:
    name = "rules"

    def generate(self, ws: Workspace, report: MatchReportOut | None) -> LetterDraft:
        title, company = ws.job.title, ws.job.company_name
        jobs = {j.id for j in ws.profile.work_experiences}
        projects = {p.id for p in ws.profile.projects}
        work: list[LetterSentence] = []
        project: list[LetterSentence] = []
        last: dict[str, uuid.UUID | None] = {"work": None, "project": None}
        for item in _strongest_evidence(ws, report):
            if item.subject_id in jobs and len(work) < MAX_WORK_SENTENCES:
                group, target = "work", work
            elif item.subject_id in projects and len(project) < MAX_PROJECT_SENTENCES:
                group, target = "project", project
            else:
                continue
            text = frame(item, ws, follow_up=last[group] == item.subject_id)
            if text is None:
                continue
            target.append(LetterSentence(text=text, evidence_ids=[item.id]))
            last[group] = item.subject_id

        # Skills the job asks for that the candidate lists and the evidence shows.
        wanted = {c.lower() for c in ws.required_concepts + ws.preferred_concepts}
        skills: list[tuple[str, list[uuid.UUID]]] = []
        for candidate_skill in ws.profile.skills:
            name = candidate_skill.skill.name
            support = skill_evidence(ws, name)
            if name.lower() in wanted and support and len(skills) < MAX_SKILLS:
                skills.append((name, [e.id for e in support[:2]]))
        body: list[LetterParagraph] = []
        if work:
            body.append(LetterParagraph(sentences=work))
        if project or skills:
            extra = list(project)
            if skills:
                names = [n for n, _ in skills]
                listed = (
                    names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
                )
                ids = list(dict.fromkeys(i for _, e in skills for i in e))
                extra.append(LetterSentence(text=f"I have worked with {listed}.", evidence_ids=ids))
            body.append(LetterParagraph(sentences=extra))

        content = CoverLetterContent(
            job_title=title,
            company_name=company,
            signature=signature_from(ws.profile),
            greeting=greeting_for(company),
            paragraphs=[
                LetterParagraph(sentences=[LetterSentence(text=opening_for(title, company))]),
                *body,
                LetterParagraph(
                    sentences=[LetterSentence(text=t) for t in closing_sentences(title)]
                ),
            ],
        )
        notes = []
        if not body:
            notes.append(
                "Your verified evidence doesn't describe work or projects in a way the letter "
                "can state as fact yet. Add highlights to your profile to strengthen it."
            )
        if missing := _missing(report):
            notes.append(
                "Not mentioned, because your evidence doesn't show them: "
                + "; ".join(missing[:5])
                + "."
            )
        return LetterDraft(content, notes)


# --- LLM generator ----------------------------------------------------------------------

PROMPT_VERSION = "cover-letter-v1"
SYSTEM_PROMPT = """You write a concise, professional cover letter for a job candidate using
ONLY their own verified evidence.

You receive the job (title, company, requirements with how the candidate's evidence
matches them) and the candidate's evidence statements, each with an ID.

Hard rules - violations are detected and removed:
- Every sentence that says something about the candidate (experience, skills, results,
  education) must cite the evidence IDs it rests on and say nothing those statements don't
  say. Keep numbers exactly as written in the evidence. Never invent achievements,
  experience, employers, projects, metrics or dates.
- No generic self-praise presented as fact ("passionate", "hard-working", "team player",
  "strong communication skills", "proven track record") unless the evidence says it.
- Don't claim requirements the evidence doesn't meet; simply don't mention them.
- Sentences that only state intent, interest or courtesy ("I am writing to apply for ...",
  "I would welcome the opportunity to discuss ...", "Thank you for your time.") cite no
  evidence and must not mention skills, numbers, or accomplishments.
- 3-4 short paragraphs, at most 300 words in total. Plain, specific, no clichés."""

_SENTENCE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "evidence_ids"],
    "additionalProperties": False,
}
LETTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "greeting": {"type": "string"},
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"sentences": {"type": "array", "items": _SENTENCE}},
                "required": ["sentences"],
                "additionalProperties": False,
            },
        },
        "closing": {"type": "string"},
    },
    "required": ["greeting", "paragraphs", "closing"],
    "additionalProperties": False,
}
REVISE_PROMPT = """Some sentences of a cover letter failed fact-checking against the
candidate's evidence. Rewrite each so it states only what the listed evidence says (cite
the IDs you use), or return empty text to drop it. Same hard rules as before: no invented
facts, numbers, qualities or experience."""
REVISE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "revisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"sentence_id": {"type": "string"}, **_SENTENCE["properties"]},
                "required": ["sentence_id", "text", "evidence_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["revisions"],
    "additionalProperties": False,
}


def build_prompt(ws: Workspace, report: MatchReportOut | None) -> str:
    p = ws.profile

    def records(items: list[Any], label: Any) -> list[dict[str, Any]]:
        return [
            {
                "record": label(i),
                "evidence": [
                    {"evidence_id": str(e.id), "text": e.content} for e in ws.evidence_for(i.id)
                ],
            }
            for i in items
            if ws.evidence_for(i.id)
        ]

    requirements = (
        [
            {
                "requirement": r.requirement,
                "importance": r.importance.value,
                "match": r.match_status.value,
                "evidence_ids": [str(i) for i in r.evidence_ids],
            }
            for r in report.requirements
        ]
        if report is not None
        else []
    )
    payload = {
        "job": {
            "title": ws.job.title,
            "company": ws.job.company_name,
            "requirements": requirements,
        },
        "candidate": {
            "name": p.full_name,
            "jobs": records(p.work_experiences, lambda j: f"{j.title} at {j.company_name}"),
            "projects": records(p.projects, lambda r: r.title),
            "education": records(p.educations, lambda e: e.institution),
            "other_evidence": [
                {"evidence_id": str(e.id), "text": e.content}
                for e in ws.evidence.values()
                if e.subject_id is None
            ],
        },
    }
    return (
        f"<job_and_candidate>\n{json.dumps(payload, indent=1)}\n</job_and_candidate>\n\n"
        "Write the cover letter."
    )


def _ids(values: Any, allowed: dict[uuid.UUID, EvidenceItem]) -> list[uuid.UUID]:
    found = []
    for value in values or []:
        try:
            evidence_id = uuid.UUID(str(value))
        except ValueError:
            continue
        if evidence_id in allowed:
            found.append(evidence_id)
    return list(dict.fromkeys(found))


def map_letter(data: dict[str, Any], ws: Workspace) -> LetterDraft:
    """The model's letter, with record facts from the job and profile and only the
    candidate's own verified evidence IDs kept."""
    paragraphs = []
    for paragraph in (data.get("paragraphs") or [])[:MAX_PARAGRAPHS]:
        sentences = [
            LetterSentence(text=text, evidence_ids=_ids(s.get("evidence_ids"), ws.evidence))
            for s in (paragraph.get("sentences") or [])[:MAX_SENTENCES]
            if (text := " ".join(str(s.get("text") or "").split()))
        ]
        if sentences:
            paragraphs.append(LetterParagraph(sentences=sentences))
    greeting = " ".join(str(data.get("greeting") or "").split())
    closing = " ".join(str(data.get("closing") or "").split())
    content = CoverLetterContent(
        job_title=ws.job.title,
        company_name=ws.job.company_name,
        signature=signature_from(ws.profile),
        greeting=greeting or greeting_for(ws.job.company_name),
        paragraphs=paragraphs,
        closing=closing or "Sincerely,",
    )
    return LetterDraft(content)


@dataclass(frozen=True)
class Revision:
    sentence_id: str
    text: str
    evidence_ids: list[uuid.UUID]


class LLMLetterGenerator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.name = f"llm:{provider.model}"[:50]

    async def generate(
        self, ws: Workspace, report: MatchReportOut | None
    ) -> tuple[LetterDraft, LLMJsonResult]:
        result = await self.provider.complete_json(
            system=SYSTEM_PROMPT, prompt=build_prompt(ws, report), schema=LETTER_SCHEMA
        )
        return map_letter(result.data, ws), result

    async def revise(
        self, ws: Workspace, failed: list[tuple[str, str, str, list[EvidenceItem]]]
    ) -> tuple[list[Revision], LLMJsonResult]:
        """``failed``: (sentence id, text, why it failed, evidence it may use)."""
        items = [
            {
                "sentence_id": sid,
                "sentence": text,
                "problem": why,
                "evidence": [{"evidence_id": str(e.id), "text": e.content} for e in evidence],
            }
            for sid, text, why, evidence in failed
        ]
        prompt = f"<sentences>\n{json.dumps(items, indent=1)}\n</sentences>\n\nRevise them."
        result = await self.provider.complete_json(
            system=f"{SYSTEM_PROMPT}\n\n{REVISE_PROMPT}", prompt=prompt, schema=REVISE_SCHEMA
        )
        offered = {sid: {e.id: e for e in evidence} for sid, _, _, evidence in failed}
        revisions = []
        for entry in result.data.get("revisions") or []:
            sid = str(entry.get("sentence_id"))
            if sid not in offered:
                continue
            text = " ".join(str(entry.get("text") or "").split())
            revisions.append(Revision(sid, text, _ids(entry.get("evidence_ids"), offered[sid])))
        return revisions, result
