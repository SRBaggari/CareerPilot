"""Section detection, rule-based parsing, grounding, and the LLM parser mapping."""

from datetime import date
from typing import Any

import pytest

from app.ai.provider import LLMJsonResult
from app.profiles.models import DocumentFormat, SuggestionSection
from app.resumes.extraction import extract_text
from app.resumes.grounding import ground
from app.resumes.heuristic import HeuristicResumeParser, find_range, parse_date
from app.resumes.llm_parser import RESUME_SCHEMA, LLMResumeParser, to_iso, to_parsed_resume
from app.resumes.parsed import ParsedItem, ParsedResume, ParsedSkill
from app.resumes.sections import SectionKind, detect_sections, match_heading

from .resume_files import SAMPLE_LINES, SAMPLE_RESUME, make_docx, make_pdf

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _by_section(parsed: ParsedResume, section: SuggestionSection) -> list[dict[str, Any]]:
    return [i.data for i in parsed.items if i.section == section]


# --- Sections ---------------------------------------------------------------------------


def test_sections_are_detected() -> None:
    sections = detect_sections(SAMPLE_RESUME)
    assert sections[SectionKind.HEADER][0] == "PRIYA SHARMA"
    assert sections[SectionKind.SKILLS][0] == "Languages: Python, Java, C++, SQL"
    assert sections[SectionKind.OTHER] == ["English, Hindi, Telugu"]
    assert set(sections) >= {
        SectionKind.EDUCATION, SectionKind.EXPERIENCE, SectionKind.PROJECTS,
        SectionKind.CERTIFICATIONS, SectionKind.ACHIEVEMENTS, SectionKind.COURSEWORK,
    }  # fmt: skip


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("EDUCATION", (SectionKind.EDUCATION, "")),
        ("Work Experience:", (SectionKind.EXPERIENCE, "")),
        ("Honors & Awards", (SectionKind.ACHIEVEMENTS, "")),
        ("Relevant Coursework: Algorithms, DBMS", (SectionKind.COURSEWORK, "Algorithms, DBMS")),
        ("Built an education platform for rural schools", None),
    ],
)  # fmt: skip
def test_heading_matching(line: str, expected: tuple[SectionKind, str] | None) -> None:
    assert match_heading(line) == expected


# --- Rule-based parser ------------------------------------------------------------------


def test_heuristic_parser_extracts_every_section() -> None:
    parsed = HeuristicResumeParser().parse(SAMPLE_RESUME)
    assert parsed.personal == {
        "full_name": "Priya Sharma",
        "contact_email": "priya.sharma@example.test",
        "phone": "+91 98765 43210",
        "linkedin_url": "linkedin.com/in/priya-sharma-dev",
        "github_url": "github.com/priya-dev",
        "location": "Hyderabad, India",
        "summary": "Final-year computer science student focused on machine learning systems.",
    }
    [btech, inter] = _by_section(parsed, SuggestionSection.EDUCATION)
    assert btech["degree"] == "B.Tech"
    assert btech["field_of_study"] == "Computer Science and Engineering"
    assert (btech["gpa"], btech["gpa_scale"], btech["degree_level"]) == ("8.7", "10", "bachelor")
    assert (inter["gpa"], inter["gpa_scale"]) == ("94.2", "100")

    [intern, ta] = _by_section(parsed, SuggestionSection.WORK_EXPERIENCE)
    assert (intern["title"], intern["company_name"]) == (
        "Machine Learning Intern",
        "Acme Analytics Pvt Ltd",
    )
    assert intern["employment_type"] == "internship"
    assert (intern["start_date"], intern["end_date"]) == ("2023-05-01", "2023-08-01")
    assert (ta["title"], ta["company_name"], ta["is_current"]) == (
        "Teaching Assistant", "State Institute of Technology", True,
    )  # fmt: skip

    [mara, _] = [i for i in parsed.items if i.section == SuggestionSection.PROJECT]
    assert mara.data["repository_url"] == "github.com/priya-dev/mara"
    assert mara.highlights[1].endswith("with an automated grading script.")  # wrapped bullet

    assert [c["name"] for c in _by_section(parsed, SuggestionSection.CERTIFICATION)] == [
        "AWS Certified Cloud Practitioner", "Deep Learning Specialization",
    ]  # fmt: skip
    assert _by_section(parsed, SuggestionSection.ACHIEVEMENT)[0]["title"] == (
        "Winner, Smart India Hackathon 2023"
    )
    assert len(_by_section(parsed, SuggestionSection.COURSEWORK)) == 4
    skills = {s.name: s.category for s in parsed.skills}
    assert skills["C++"] == "programming_language"
    assert skills["React (Hooks, Redux)"] == "framework"  # commas inside () are kept
    assert parsed.warnings == []


@pytest.mark.parametrize("builder", [make_pdf, make_docx], ids=["pdf", "docx"])
def test_pdf_and_docx_parse_like_the_source_text(builder: Any) -> None:
    fmt = DocumentFormat.PDF if builder is make_pdf else DocumentFormat.DOCX
    text = extract_text(builder(SAMPLE_LINES), fmt).text
    from_file = HeuristicResumeParser().parse(text)
    from_text = HeuristicResumeParser().parse(SAMPLE_RESUME)
    assert [(i.section, i.data) for i in from_file.items] == [
        (i.section, i.data) for i in from_text.items
    ]
    assert [s.name for s in from_file.skills] == [s.name for s in from_text.skills]


def test_entries_missing_required_fields_become_warnings_not_guesses() -> None:
    parsed = HeuristicResumeParser().parse("Jane Doe\nEXPERIENCE\nSomething vague happened\n")
    assert _by_section(parsed, SuggestionSection.WORK_EXPERIENCE) == []
    assert parsed.warnings and "work experience" in parsed.warnings[0]


@pytest.mark.parametrize(
    ("token", "is_end", "expected"),
    [("May 2023", False, date(2023, 5, 1)), ("Sept. 2021", False, date(2021, 9, 1)),
     ("06/2022", False, date(2022, 6, 1)), ("2020", False, date(2020, 1, 1)),
     ("2024", True, date(2024, 12, 1)), ("13/2022", False, None)],
)  # fmt: skip
def test_parse_date(token: str, is_end: bool, expected: date | None) -> None:
    assert parse_date(token, is_end=is_end) == expected


def test_find_range_handles_open_ended_ranges() -> None:
    assert find_range("Jan 2023 \u2013 Present") == (date(2023, 1, 1), None, True)


# --- Grounding --------------------------------------------------------------------------


def test_grounding_drops_invented_information() -> None:
    source = "Jane Doe\nPROJECTS\nChatbot\n\u2022 Built a chatbot with FastAPI.\nSKILLS\nPython"
    parsed = ParsedResume(
        personal={"full_name": "Jane Doe", "contact_email": "jane@invented.test"},
        items=[
            ParsedItem(SuggestionSection.PROJECT, {"title": "Chatbot"},
                       ["Built a chatbot with FastAPI.", "Served 10 million users."],
                       "Chatbot\n\u2022 Built a chatbot with FastAPI."),
            ParsedItem(SuggestionSection.WORK_EXPERIENCE,
                       {"title": "Staff Engineer", "company_name": "Google"}, [], ""),
        ],
        skills=[ParsedSkill("Python", None, "Python"), ParsedSkill("Kubernetes", None, "")],
    )  # fmt: skip
    grounded = ground(parsed, source)
    assert grounded.personal == {"full_name": "Jane Doe"}
    [project] = grounded.items  # the invented job is gone entirely
    assert project.highlights == ["Built a chatbot with FastAPI."]
    assert [s.name for s in grounded.skills] == ["Python"]
    assert "5 extracted value(s) were discarded" in grounded.warnings[-1]


def test_grounding_tolerates_formatting_differences() -> None:
    source = "Profile: github.com/jane  \u2014  Smart\u00a0India Hackathon \u2018Winner\u2019"
    parsed = ParsedResume(
        personal={"github_url": "https://github.com/jane"},
        items=[ParsedItem(SuggestionSection.ACHIEVEMENT,
                          {"title": "Smart India Hackathon 'Winner'"}, [], "")],
    )  # fmt: skip
    grounded = ground(parsed, source)
    assert grounded.personal == {"github_url": "https://github.com/jane"}
    assert len(grounded.items) == 1 and grounded.warnings == []


# --- LLM parser -------------------------------------------------------------------------


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000
    ) -> LLMJsonResult:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        return LLMJsonResult(self.data, "fake", "fake-model", 100, 50, 5)


LLM_OUTPUT: dict[str, Any] = {
    "full_name": "Priya Sharma", "email": "priya.sharma@example.test", "phone": None,
    "location": None, "linkedin_url": None, "github_url": None, "website_url": None,
    "summary": None,
    "education": [{"institution": "State Institute of Technology", "degree": "B.Tech",
                   "degree_level": "bachelor", "field_of_study": None, "location": None,
                   "start": "2020", "end": "2024", "gpa": "8.7", "gpa_scale": "10",
                   "highlights": [], "source_excerpt": "State Institute of Technology"}],
    "work_experience": [{"title": "Machine Learning Intern", "company": "Acme Analytics Pvt Ltd",
                         "location": None, "employment_type": "internship", "start": "2023-05",
                         "end": "2023-08", "is_current": False,
                         "highlights": ["Deployed the model with FastAPI and Docker on AWS ECS."],
                         "source_excerpt": ""}],
    "projects": [], "certifications": [], "achievements": [], "coursework": [],
    "skills": [{"name": "Python", "category": "programming_language",
                "source_excerpt": "Languages: Python, Java, C++, SQL"}],
}  # fmt: skip


async def test_llm_parser_sends_resume_and_schema_and_maps_output() -> None:
    provider = FakeProvider(LLM_OUTPUT)
    parsed, result = await LLMResumeParser(provider).parse(SAMPLE_RESUME)
    call = provider.calls[0]
    assert SAMPLE_RESUME in call["prompt"] and call["schema"] is RESUME_SCHEMA
    assert "never infer or guess" in call["system"]
    assert result.input_tokens == 100
    [education] = _by_section(parsed, SuggestionSection.EDUCATION)
    assert (education["start_date"], education["end_date"]) == ("2020-01-01", "2024-12-01")
    [job] = _by_section(parsed, SuggestionSection.WORK_EXPERIENCE)
    assert job["company_name"] == "Acme Analytics Pvt Ltd" and "is_current" not in job
    assert parsed.skills[0].category == "programming_language"


def test_llm_schema_is_strict() -> None:
    def check(node: dict[str, Any]) -> None:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        if node.get("type") == "array":
            check(node["items"])

    check(RESUME_SCHEMA)


@pytest.mark.parametrize(
    ("value", "is_end", "expected"),
    [("2023-05", False, "2023-05-01"), ("2023", True, "2023-12-01"), ("Present", True, None),
     (None, False, None)],
)  # fmt: skip
def test_to_iso(value: str | None, is_end: bool, expected: str | None) -> None:
    assert to_iso(value, is_end=is_end) == expected


def test_to_parsed_resume_ignores_missing_sections() -> None:
    assert to_parsed_resume({"full_name": "A B"}).items == []
