"""What to put in each detected field, taken only from the candidate's approved materials.

- Personal information comes from the candidate's profile.
- The resume and cover letter are the approved documents attached to the application.
- Question fields get the approved answer to the same question, and nothing else: a
  required question without one stops for the candidate to add and approve an answer.
- Anything else (e.g. work authorization) is never guessed: the candidate provides it.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.automation.adapters.base import DetectedForm, FormField
from app.automation.review import (
    Destination,
    PersonalInfo,
    Review,
    ReviewAnswer,
    ReviewField,
    ReviewFile,
)

PERSONAL_FIELDS = ("first_name", "last_name", "email", "phone", "location", "linkedin")


@dataclass(frozen=True)
class Document:
    kind: str  # "resume" or "cover_letter"
    version: int
    file_name: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class ApprovedAnswer:
    answer_id: uuid.UUID
    question: str
    text: str


@dataclass(frozen=True)
class Materials:
    personal: dict[str, str | None]  # PERSONAL_FIELDS -> value
    resume: Document
    cover_letter: Document | None
    answers: list[ApprovedAnswer]


class Problem(BaseModel):
    field_id: str
    label: str
    kind: str
    required: bool
    options: list[str] = []
    message: str
    needs_input: bool  # the candidate can resolve it by providing a value here


@dataclass
class FillPlan:
    values: dict[str, str] = field(default_factory=dict)  # text, textarea and select fields
    uploads: dict[str, Document] = field(default_factory=dict)
    answers: dict[str, ApprovedAnswer] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)


def split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split()
    if len(parts) < 2:
        return (parts[0] if parts else "", "")
    return parts[0], " ".join(parts[1:])


def normalize_question(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _problem(f: FormField, message: str, *, needs_input: bool) -> Problem:
    return Problem(
        field_id=f.field_id,
        label=f.label,
        kind=f.kind,
        required=f.required,
        options=f.options,
        message=message,
        needs_input=needs_input,
    )


def _from_candidate(f: FormField, inputs: dict[str, str], plan: FillPlan) -> None:
    value = (inputs.get(f.field_id) or "").strip()
    if f.options and value and value not in f.options:
        plan.problems.append(
            _problem(f, f"Choose one of: {', '.join(f.options)}.", needs_input=True)
        )
    elif value:
        plan.values[f.field_id] = value
    elif f.required:
        plan.problems.append(
            _problem(
                f,
                "CareerPilot doesn't have this in your records and won't guess: provide it.",
                needs_input=True,
            )
        )


def plan_fill(form: DetectedForm, materials: Materials, inputs: dict[str, str]) -> FillPlan:
    plan = FillPlan()
    answers = {normalize_question(a.question): a for a in materials.answers}
    for f in form.fields:
        if f.section == "personal" and f.field_id in PERSONAL_FIELDS:
            value = materials.personal.get(f.field_id)
            if value:
                plan.values[f.field_id] = value
            elif f.required:
                plan.problems.append(
                    _problem(
                        f,
                        "Your profile doesn't have this. Add it to your profile, then prepare "
                        "again.",
                        needs_input=False,
                    )
                )
        elif f.kind == "file":
            document = {"resume": materials.resume, "cover_letter": materials.cover_letter}.get(
                f.field_id
            )
            if document is not None:
                plan.uploads[f.field_id] = document
            elif f.required:
                message = (
                    "The form requires a cover letter: attach an approved cover letter to the "
                    "application first."
                    if f.field_id == "cover_letter"
                    else "CareerPilot only attaches your approved resume and cover letter; "
                    "this file field needs something else, so apply on the site yourself."
                )
                plan.problems.append(_problem(f, message, needs_input=False))
        elif f.section == "questions":
            answer = answers.get(normalize_question(f.label))
            if answer is not None:
                plan.values[f.field_id] = answer.text
                plan.answers[f.field_id] = answer
            elif f.required:
                plan.problems.append(
                    _problem(
                        f,
                        "No approved answer to this question. Add it under Application "
                        "questions, approve the answer, then prepare again.",
                        needs_input=False,
                    )
                )
        else:
            _from_candidate(f, inputs, plan)
    return plan


def _file(f: FormField, found: dict[str, Any] | None, document: Document) -> ReviewFile:
    found = found or {}
    return ReviewFile(
        field_label=f.label,
        document=document.kind,
        version=document.version,
        file_name=str(found.get("name") or ""),
        size_bytes=int(found.get("size") or 0),
        sha256=str(found.get("sha256") or ""),
    )


def build_review(
    form: DetectedForm,
    plan: FillPlan,
    read_back: dict[str, Any],
    destination: Destination,
) -> tuple[Review, list[str]]:
    """The review, built from what the form actually contains after filling (not from what
    was intended), plus any differences between the two."""
    mismatches: list[str] = []
    for field_id, expected in plan.values.items():
        if read_back.get(field_id) != expected:
            mismatches.append(field_id)
    for field_id, document in plan.uploads.items():
        found = read_back.get(field_id) or {}
        if found.get("size") != len(document.data) or (
            found.get("sha256") and found["sha256"] != document.sha256
        ):
            mismatches.append(field_id)

    by_id = {f.field_id: f for f in form.fields}

    def value(field_id: str) -> str | None:
        found = read_back.get(field_id)
        return found if isinstance(found, str) and found else None

    personal = PersonalInfo(
        first_name=value("first_name") or "",
        last_name=value("last_name") or "",
        email=value("email") or "",
        phone=value("phone"),
        location=value("location"),
        linkedin=value("linkedin"),
    )
    resume_field = by_id["resume"]
    letter_field = by_id.get("cover_letter")
    letter = plan.uploads.get("cover_letter")
    review = Review(
        destination=destination,
        personal=personal,
        resume=_file(resume_field, read_back.get("resume"), plan.uploads["resume"]),
        cover_letter=_file(letter_field, read_back.get("cover_letter"), letter)
        if letter is not None and letter_field is not None
        else None,
        answers=[
            ReviewAnswer(
                field_id=field_id,
                question=by_id[field_id].label,
                answer=value(field_id) or "",
                answer_id=answer.answer_id,
            )
            for field_id, answer in plan.answers.items()
        ],
        additional_fields=[
            ReviewField(
                field_id=f.field_id,
                label=f.label,
                value=value(f.field_id) or "",
                source="you provided this",
            )
            for f in form.fields
            if f.field_id in plan.values
            and f.field_id not in PERSONAL_FIELDS
            and f.field_id not in plan.answers
        ],
        left_blank=[
            f.label
            for f in form.fields
            if f.field_id not in plan.values and f.field_id not in plan.uploads
        ],
    )
    return review, mismatches
