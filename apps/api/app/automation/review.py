"""The final review: exactly what will be submitted, as read back from the filled form.

Its hash binds the candidate's confirmation to this exact content: submission refills the
form and proceeds only if the form reads back to the same review.
"""

import hashlib
import json
import uuid

from pydantic import BaseModel


class PersonalInfo(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    linkedin: str | None = None


class ReviewFile(BaseModel):
    field_label: str
    document: str  # "resume" or "cover_letter"
    version: int
    file_name: str
    size_bytes: int
    sha256: str


class ReviewAnswer(BaseModel):
    field_id: str
    question: str  # as the form asks it
    answer: str
    answer_id: uuid.UUID  # the approved application answer it comes from


class ReviewField(BaseModel):
    field_id: str
    label: str
    value: str
    source: str  # "you provided this" / "your profile"


class Destination(BaseModel):
    url: str  # the application page
    host: str
    adapter: str
    form_action: str  # where the form will be sent


class Review(BaseModel):
    destination: Destination
    personal: PersonalInfo
    resume: ReviewFile
    cover_letter: ReviewFile | None
    answers: list[ReviewAnswer]
    additional_fields: list[ReviewField]
    left_blank: list[str]  # optional fields left empty


def review_hash(review: Review) -> str:
    canonical = json.dumps(review.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
