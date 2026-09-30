"""Explainable scoring: a weighted share of requirements covered by verified evidence.

Required requirements weigh 1.0 and preferred ones 0.5. MATCHED earns full credit, PARTIAL
half, MISSING none. UNKNOWN requirements can't be assessed, so they are excluded from the
score and reported separately. The result is *evidence coverage*, not a hiring probability.
"""

from dataclasses import dataclass

from app.jobs.models import JobRequirement, RequirementImportance, RequirementType
from app.matching.engine.judge import Assessment
from app.matching.models import MatchStatus

SCORING_VERSION = "coverage-v1"
WEIGHTS = {RequirementImportance.REQUIRED: 1.0, RequirementImportance.PREFERRED: 0.5}
CREDIT = {MatchStatus.MATCHED: 1.0, MatchStatus.PARTIAL: 0.5, MatchStatus.MISSING: 0.0}
SKILL_TYPES = (RequirementType.TECHNOLOGY, RequirementType.SKILL)
DISCLAIMER = (
    "This score shows how much of the job's stated requirements your verified evidence "
    "covers. It is not a prediction or guarantee of being hired."
)


@dataclass(frozen=True)
class Scores:
    overall: float
    required_coverage: float | None
    preferred_coverage: float | None
    semantic: float | None
    skill: float | None


def _coverage(pairs: list[tuple[JobRequirement, Assessment]]) -> float | None:
    assessed = [(r, a) for r, a in pairs if a.status != MatchStatus.UNKNOWN]
    total = sum(WEIGHTS[r.importance] for r, _ in assessed)
    if not total:
        return None
    return round(sum(WEIGHTS[r.importance] * CREDIT[a.status] for r, a in assessed) / total, 4)


def score(pairs: list[tuple[JobRequirement, Assessment]]) -> Scores:
    similarities = [
        max(a.semantic_similarity, 0.0) for _, a in pairs if a.semantic_similarity is not None
    ]
    return Scores(
        overall=_coverage(pairs) or 0.0,
        required_coverage=_coverage(
            [(r, a) for r, a in pairs if r.importance == RequirementImportance.REQUIRED]
        ),
        preferred_coverage=_coverage(
            [(r, a) for r, a in pairs if r.importance == RequirementImportance.PREFERRED]
        ),
        semantic=round(sum(similarities) / len(similarities), 4) if similarities else None,
        skill=_coverage([(r, a) for r, a in pairs if r.requirement_type in SKILL_TYPES]),
    )
