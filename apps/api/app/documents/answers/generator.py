"""Answer generators for application questions.

Both answer only from the candidate's verified evidence. Neither verifies its own output:
the claim verification engine does, and failed sentences are regenerated or removed.

- ``RuleAnswerGenerator``: deterministic. Picks evidence for the kind of question and
  frames it in the first person without adding facts.
- ``LLMAnswerGenerator``: an LLM writes the answer from the retrieved evidence (structured
  output), and can rewrite sentences the engine rejected.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.ai.provider import LLMJsonResult, LLMProvider
from app.ai.untrusted import safe_json, with_rules
from app.documents.answers.questions import Understanding
from app.documents.cover_letter.content import LetterSentence
from app.documents.cover_letter.generator import (
    Revision,
    _ids,
    _strongest_evidence,
    frame,
)
from app.documents.models import QuestionType
from app.documents.resume.generator import skill_evidence
from app.documents.resume.workspace import EvidenceItem, Workspace
from app.jobs.analysis.vocabulary import find_technologies
from app.matching.schemas import MatchReportOut

MAX_EVIDENCE_SENTENCES = 3
MAX_SENTENCES = 8


@dataclass
class AnswerDraft:
    sentences: list[LetterSentence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def mentions(item: EvidenceItem, focus: str) -> bool:
    text = f"{item.context}\n{item.content}"
    if focus in find_technologies(text):
        return True
    return re.search(rf"(?<![\w]){re.escape(focus.lower())}(?![\w])", text.lower()) is not None


def interest_sentence(ws: Workspace) -> str:
    return f"I am interested in the {ws.job.title} role at {ws.job.company_name}."


def closing_sentence(ws: Workspace) -> str:
    return f"I would welcome the opportunity to discuss the {ws.job.title} role with you."


def _framed(items: list[EvidenceItem], ws: Workspace, limit: int) -> list[LetterSentence]:
    """First-person sentences for evidence; consecutive items of one record read "I also"."""
    sentences: list[LetterSentence] = []
    previous: uuid.UUID | None = None
    for item in items:
        if len(sentences) >= limit:
            break
        text = frame(item, ws, follow_up=previous is not None and item.subject_id == previous)
        if text is None or any(s.text == text for s in sentences):
            continue
        sentences.append(LetterSentence(text=text, evidence_ids=[item.id]))
        previous = item.subject_id
    return sentences


def _grouped(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Keep each record's evidence together (in order of first appearance)."""
    order: dict[uuid.UUID | None, list[EvidenceItem]] = {}
    for item in items:
        order.setdefault(item.subject_id, []).append(item)
    return [i for group in order.values() for i in group]


def fit_to_words(
    sentences: list[LetterSentence], max_words: int | None
) -> tuple[list[LetterSentence], bool]:
    """Drop sentences from the end until the answer fits the word limit (keeping one)."""
    if max_words is None:
        return sentences, False
    kept = list(sentences)
    while len(kept) > 1 and sum(len(s.text.split()) for s in kept) > max_words:
        kept.pop()
    return kept, len(kept) < len(sentences)


class RuleAnswerGenerator:
    name = "rules"

    def generate(
        self,
        ws: Workspace,
        report: MatchReportOut | None,
        understanding: Understanding,
        retrieved: list[EvidenceItem],
        max_words: int | None = None,
    ) -> AnswerDraft:
        kind, focus = understanding.question_type, understanding.focus
        draft = AnswerDraft()
        projects = {p.id: p for p in ws.profile.projects}

        if kind == QuestionType.SKILL and focus:
            items = [e for e in [*retrieved, *skill_evidence(ws, focus)] if mentions(e, focus)]
            items = list(dict.fromkeys(items))
            draft.sentences = _framed(_grouped(items), ws, MAX_EVIDENCE_SENTENCES)
            if items and not draft.sentences:
                # The evidence names the skill but isn't an action (e.g. a skills list).
                draft.sentences = [
                    LetterSentence(
                        text=f"I have worked with {focus}.", evidence_ids=[e.id for e in items[:2]]
                    )
                ]
            if not draft.sentences:
                draft.notes.append(
                    f"Your verified evidence doesn't show experience with {focus}, so no answer "
                    "was written. If you have it, add a highlight to your profile first, or "
                    "write your own answer."
                )
        elif kind == QuestionType.PROJECT:
            # The project most relevant to the job (match report included), then to the
            # question: each project's best score.
            question_rank = {e.subject_id: n for n, e in reversed(list(enumerate(retrieved)))}
            ranked = sorted(
                (e for e in ws.evidence.values() if e.subject_id in projects),
                key=lambda e: (
                    -ws.record_relevance.get(e.subject_id, e.relevance),  # type: ignore[arg-type]
                    question_rank.get(e.subject_id, len(retrieved)),
                    -e.relevance,
                ),
            )
            if ranked:
                chosen = ranked[0].subject_id
                items = [e for e in dict.fromkeys(ranked) if e.subject_id == chosen]
                draft.sentences = _framed(items, ws, MAX_EVIDENCE_SENTENCES)
            if not draft.sentences:
                draft.sentences = _framed(_grouped(retrieved), ws, MAX_EVIDENCE_SENTENCES)
                draft.notes.append(
                    "No project in your profile has verified evidence, so this describes your "
                    "other work. Add highlights to a project to answer with it."
                )
        elif kind in (QuestionType.FIT, QuestionType.MOTIVATION):
            strongest = _grouped(_strongest_evidence(ws, report))
            body = _framed(strongest, ws, 2 if kind == QuestionType.MOTIVATION else 3)
            if kind == QuestionType.MOTIVATION:
                draft.sentences = [
                    LetterSentence(text=interest_sentence(ws)),
                    *body,
                    LetterSentence(text=closing_sentence(ws)),
                ]
            else:
                draft.sentences = body
        else:
            draft.sentences = _framed(_grouped(retrieved), ws, MAX_EVIDENCE_SENTENCES)
            if kind == QuestionType.BEHAVIORAL:
                draft.notes.append(
                    "This question asks about a specific situation. The draft only states what "
                    "your evidence shows; add the story in your own words, and it will be "
                    "checked when you save."
                )
        if not draft.sentences and not draft.notes:
            draft.notes.append(
                "Your verified evidence doesn't cover this question, so no answer was written."
            )
        draft.sentences, trimmed = fit_to_words(draft.sentences, max_words)
        if trimmed:
            draft.notes.append(f"Shortened to fit the {max_words}-word limit.")
        return draft


# --- LLM generator ----------------------------------------------------------------------

PROMPT_VERSION = "application-answer-v1"
SYSTEM_PROMPT = """You answer a job application question for a candidate using ONLY their own
verified evidence.

You receive the question, how it was interpreted, the job, and evidence statements (each
with an ID) retrieved for the question.

Hard rules - violations are detected and removed:
- Every sentence that says something about the candidate (experience, skills, results,
  projects, education) must cite the evidence IDs it rests on and say nothing those
  statements don't say. Keep numbers exactly as written. Never invent experience,
  achievements, employers, projects, metrics or dates.
- No generic self-praise presented as fact ("passionate", "hard-working", "team player",
  "strong communication skills", "proven track record").
- If the evidence doesn't answer the question (e.g. a skill it never mentions), return no
  sentences rather than guessing.
- Sentences that only state interest or intent ("I am interested in the ... role at ...")
  cite no evidence and mention no skills, numbers, or accomplishments.
- Answer directly, in the first person, in 2-5 sentences, within the word limit if given."""

_SENTENCE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "evidence_ids"],
    "additionalProperties": False,
}
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": _SENTENCE}},
    "required": ["sentences"],
    "additionalProperties": False,
}
REVISE_PROMPT = """Some sentences of the answer failed fact-checking against the candidate's
evidence. Rewrite each so it states only what the listed evidence says (cite the IDs you
use), or return empty text to drop it."""
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


def build_prompt(
    ws: Workspace,
    question: str,
    understanding: Understanding,
    retrieved: list[EvidenceItem],
    max_words: int | None,
) -> str:
    payload = {
        "question": question,
        "interpreted_as": understanding.summary,
        "word_limit": max_words,
        "job": {"title": ws.job.title, "company": ws.job.company_name},
        "evidence": [
            {"evidence_id": str(e.id), "item": e.context, "text": e.content} for e in retrieved
        ],
    }
    return (
        f"<question_and_evidence>\n{safe_json(payload, indent=1)}\n</question_and_evidence>"
        "\n\nAnswer the question."
    )


class LLMAnswerGenerator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.name = f"llm:{provider.model}"[:50]

    async def generate(
        self,
        ws: Workspace,
        question: str,
        understanding: Understanding,
        retrieved: list[EvidenceItem],
        max_words: int | None,
    ) -> tuple[AnswerDraft, LLMJsonResult]:
        result = await self.provider.complete_json(
            system=with_rules(SYSTEM_PROMPT),
            prompt=build_prompt(ws, question, understanding, retrieved, max_words),
            schema=ANSWER_SCHEMA,
        )
        sentences = [
            LetterSentence(text=text, evidence_ids=_ids(s.get("evidence_ids"), ws.evidence))
            for s in (result.data.get("sentences") or [])[:MAX_SENTENCES]
            if (text := " ".join(str(s.get("text") or "").split()))
        ]
        return AnswerDraft(sentences), result

    async def revise(
        self, failed: list[tuple[str, str, str, list[EvidenceItem]]]
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
        prompt = f"<sentences>\n{safe_json(items, indent=1)}\n</sentences>\n\nRevise them."
        result = await self.provider.complete_json(
            system=with_rules(f"{SYSTEM_PROMPT}\n\n{REVISE_PROMPT}"),
            prompt=prompt,
            schema=REVISE_SCHEMA,
        )
        offered = {sid: {e.id: e for e in evidence} for sid, _, _, evidence in failed}
        revisions = []
        for entry in result.data.get("revisions") or []:
            sid = str(entry.get("sentence_id"))
            if sid in offered:
                text = " ".join(str(entry.get("text") or "").split())
                revisions.append(Revision(sid, text, _ids(entry.get("evidence_ids"), offered[sid])))
        return revisions, result
