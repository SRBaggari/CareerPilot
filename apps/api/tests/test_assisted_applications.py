import uuid
from datetime import UTC, date, datetime

from app.automation.adapters.base import DetectedForm, FormField
from app.automation.plan import (
    ApprovedAnswer,
    Document,
    Materials,
    build_review,
    normalize_question,
    plan_fill,
    split_name,
)
from app.automation.review import Destination, review_hash
from app.documents.cover_letter import render as letter_render
from app.documents.cover_letter.content import CoverLetterContent, Signature
from app.documents.resume import render as resume_render
from app.documents.resume.content import Header, ResumeContent

ANSWER_ID = uuid.UUID(int=1)
FORM = DetectedForm(
    action_url="http://site.test/jobs/x/submit",
    fields=[
        FormField("first_name", "First name", "personal", "text", True),
        FormField("last_name", "Last name", "personal", "text", True),
        FormField("email", "Email", "personal", "email", True),
        FormField("phone", "Phone", "personal", "tel", False),
        FormField("resume", "Resume (PDF)", "documents", "file", True),
        FormField("cover_letter", "Cover letter (PDF)", "documents", "file", False),
        FormField("q1", "Why are you interested in this role?", "questions", "textarea", True),
        FormField("q2", "Anything else?", "questions", "textarea", False),
        FormField("work_authorization", "Authorized?", "additional", "select", True, ["Yes", "No"]),
        FormField("referral_source", "How did you hear about us?", "additional", "text", False),
    ],
)
RESUME = Document("resume", 2, "Ada-Lovelace-resume-v2.pdf", b"%PDF resume")


def materials(**overrides: object) -> Materials:
    values: dict[str, object] = {
        "personal": {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.test",
            "phone": None,
            "location": None,
            "linkedin": None,
        },
        "resume": RESUME,
        "cover_letter": None,
        "answers": [
            ApprovedAnswer(ANSWER_ID, "Why are you interested in this role??", "Because of X.")
        ],
    }
    values.update(overrides)
    return Materials(**values)  # type: ignore[arg-type]


def test_names_split_on_the_first_space() -> None:
    assert split_name("Ada Lovelace") == ("Ada", "Lovelace")
    assert split_name("  Ada  King Lovelace ") == ("Ada", "King Lovelace")
    assert split_name("Cher") == ("Cher", "")


def test_questions_match_ignoring_case_and_punctuation() -> None:
    assert normalize_question("Why are you interested in this role??") == normalize_question(
        "why are you  interested in this role"
    )


def test_fills_only_from_approved_materials_and_the_candidates_inputs() -> None:
    plan = plan_fill(FORM, materials(), {"work_authorization": "Yes"})
    assert plan.problems == []
    assert plan.values == {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.test",
        "q1": "Because of X.",
        "work_authorization": "Yes",
    }
    assert plan.uploads == {"resume": RESUME}
    assert plan.answers["q1"].answer_id == ANSWER_ID


def test_never_guesses_what_it_does_not_have() -> None:
    plan = plan_fill(FORM, materials(answers=[]), {"work_authorization": "Maybe"})
    problems = {p.field_id: p for p in plan.problems}
    assert set(problems) == {"q1", "work_authorization"}
    assert not problems["q1"].needs_input  # answers are approved elsewhere, never typed here
    assert problems["work_authorization"].needs_input
    assert "Yes, No" in problems["work_authorization"].message
    missing = plan_fill(FORM, materials(), {})
    [problem] = missing.problems
    assert problem.field_id == "work_authorization" and "won't guess" in problem.message


def test_a_required_cover_letter_field_needs_an_approved_letter() -> None:
    fields = [
        FormField("cover_letter", "Cover letter", "documents", "file", True)
        if f.field_id == "cover_letter"
        else f
        for f in FORM.fields
    ]
    plan = plan_fill(
        DetectedForm(FORM.action_url, fields), materials(), {"work_authorization": "No"}
    )
    assert [p.field_id for p in plan.problems] == ["cover_letter"]


def _read_back(plan_values: dict[str, str]) -> dict[str, object]:
    return {
        **plan_values,
        "phone": "",
        "referral_source": "",
        "resume": {"name": RESUME.file_name, "size": len(RESUME.data), "sha256": RESUME.sha256},
        "cover_letter": None,
    }


def test_the_review_is_built_from_the_form_and_its_hash_is_stable() -> None:
    plan = plan_fill(FORM, materials(), {"work_authorization": "Yes"})
    destination = Destination(
        url="http://site.test/jobs/x/apply",
        host="site.test",
        adapter="mock-site",
        form_action=FORM.action_url,
    )
    review, mismatches = build_review(FORM, plan, _read_back(plan.values), destination)
    assert mismatches == []
    assert review.personal.first_name == "Ada" and review.resume.version == 2
    assert review.cover_letter is None
    assert [(a.question, a.answer) for a in review.answers] == [
        ("Why are you interested in this role?", "Because of X.")
    ]
    assert [(f.label, f.value) for f in review.additional_fields] == [("Authorized?", "Yes")]
    assert review.left_blank == [
        "Phone",
        "Cover letter (PDF)",
        "Anything else?",
        "How did you hear about us?",
    ]
    same, _ = build_review(FORM, plan, _read_back(plan.values), destination)
    assert review_hash(review) == review_hash(same) and len(review_hash(review)) == 64
    changed, _ = build_review(
        FORM, plan, {**_read_back(plan.values), "q1": "Because of Y."}, destination
    )
    assert review_hash(changed) != review_hash(review)


def test_values_the_form_did_not_keep_are_reported() -> None:
    plan = plan_fill(FORM, materials(), {"work_authorization": "Yes"})
    destination = Destination(url="u", host="h", adapter="a", form_action="f")
    read_back = {**_read_back(plan.values), "email": "", "resume": {"size": 3, "sha256": "x"}}
    _, mismatches = build_review(FORM, plan, read_back, destination)
    assert mismatches == ["email", "resume"]


def test_pdfs_are_byte_identical_for_the_same_content_and_date() -> None:
    created = datetime(2026, 9, 1, 12, tzinfo=UTC)
    resume = ResumeContent(header=Header(full_name="Ada Lovelace"))
    assert resume_render.to_pdf(resume, created) == resume_render.to_pdf(resume, created)
    letter = CoverLetterContent(
        job_title="Engineer",
        company_name="Northwind",
        signature=Signature(full_name="Ada Lovelace"),
        greeting="Dear Hiring Team,",
    )
    on = date(2026, 9, 1)
    assert letter_render.to_pdf(letter, on, created) == letter_render.to_pdf(letter, on, created)
