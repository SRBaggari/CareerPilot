"""Schema invariants checked on the ORM metadata (no database needed)."""

from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum

from app.db.base import ENUM_LENGTH
from app.db.models import Base

APPEND_ONLY_TABLES = {
    "application_status_history",
    "claim_verifications",
    "ai_execution_logs",
    "generated_claim_evidence",
    "verification_reports",
}
ASSOCIATION_TABLES = {
    "candidate_evidence_skills",
    "generated_claim_evidence",
    "requirement_match_evidence",
}


def test_all_expected_tables_are_registered() -> None:
    # 25 core tables + profile_suggestions + requirement_matches + verification_reports
    # + application_answers + job_recommendations + association tables
    assert len(Base.metadata.tables) == 30 + len(ASSOCIATION_TABLES)


def test_timestamps() -> None:
    for name, table in Base.metadata.tables.items():
        if name in ("candidate_evidence_skills", "requirement_match_evidence"):
            continue
        assert "created_at" in table.c, name
        assert ("updated_at" in table.c) == (name not in APPEND_ONLY_TABLES), name


def test_enum_columns_have_named_checks_and_fit_column_length() -> None:
    for table in Base.metadata.tables.values():
        checks = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        for column in table.c:
            if not isinstance(column.type, Enum):
                continue
            assert f"ck_{table.name}_{column.name}_enum" in checks, column
            enum_cls = column.type.enum_class
            assert enum_cls is not None and issubclass(enum_cls, StrEnum)
            assert all(len(m.value) <= ENUM_LENGTH for m in enum_cls), column


def test_evidence_is_protected_from_deletion_while_cited() -> None:
    fk = next(iter(Base.metadata.tables["generated_claim_evidence"].c.evidence_id.foreign_keys))
    assert fk.ondelete is None  # NO ACTION
