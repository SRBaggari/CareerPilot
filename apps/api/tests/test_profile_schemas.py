"""Profile input validation rules (no database needed)."""

import pytest
from pydantic import ValidationError

from app.profiles.schemas import (
    EducationIn,
    EvidenceIn,
    ProfileIn,
    WorkExperienceIn,
    clean_text_list,
    normalize_url,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "https://example.com"),
        ("github.com/me", "https://github.com/me"),
        ("http://sub.example.co.in:8080/x", "http://sub.example.co.in:8080/x"),
        ("localhost:3000", "https://localhost:3000"),
    ],
)
def test_urls_are_normalized(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        " JavaScript:alert(1)",
        "data:text/html,<script>",
        "https://javascript:alert(1)",
        "ftp://example.com",
        "http://nodot",
        "https://exa mple.com",
        "https://" + "a" * 2000 + ".com",
    ],
)
def test_unsafe_or_invalid_urls_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="URL"):
        normalize_url(raw)


def test_text_lists_are_trimmed_and_deduplicated() -> None:
    assert clean_text_list([" Remote ", "remote", "", "Pune"]) == ["Remote", "Pune"]


def test_blank_strings_become_none_and_required_fields_stay_required() -> None:
    profile = ProfileIn(full_name=" Ada ", headline="   ")
    assert (profile.full_name, profile.headline) == ("Ada", None)
    with pytest.raises(ValidationError):
        ProfileIn(full_name="   ")


def test_unknown_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError, match="extra"):
        EvidenceIn.model_validate(
            {"source_type": "profile", "content": "x", "origin": "user_entered"}
        )


def test_cross_field_rules() -> None:
    with pytest.raises(ValidationError, match="gpa_scale is required"):
        EducationIn(institution="U", gpa="3.5")
    with pytest.raises(ValidationError, match="current position"):
        WorkExperienceIn(company_name="A", title="B", is_current=True, end_date="2024-01-01")
