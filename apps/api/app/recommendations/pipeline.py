"""The recommendation pipeline for one posting: analysis -> evidence retrieval -> matching
(the existing rule judge, unchanged) -> eligibility -> explanation.

Analysis uses the rule-based job analyzer and the rule judge, so a refresh over many
postings makes no AI calls. "Analyze Job" later runs the full pipeline on the imported job.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.discovery.models import NormalizedJob
from app.jobs.analysis.grounding import ground
from app.jobs.analysis.heuristic import HeuristicJobAnalyzer
from app.jobs.analysis.vocabulary import find_technologies
from app.jobs.models import JobRequirement, RequirementImportance, RequirementType
from app.matching.engine.facts import CandidateFacts
from app.matching.engine.judge import Assessment, RuleJudge
from app.matching.engine.scoring import SKILL_TYPES, Scores, score
from app.matching.models import MatchStatus
from app.profiles.models import CandidateProfile, WorkplaceType
from app.recommendations.schemas import (
    Explanation,
    RelevantProject,
    RequirementResult,
    SkillResult,
)
from app.resumes.extraction import normalize_text
from app.retrieval.schemas import RetrievedEvidence
from app.retrieval.service import retrieve_verified_for_queries

REQUIRED = RequirementImportance.REQUIRED
PREFERRED = RequirementImportance.PREFERRED
M, P, X, U = MatchStatus.MATCHED, MatchStatus.PARTIAL, MatchStatus.MISSING, MatchStatus.UNKNOWN
TOO_MANY_YEARS = 3  # required years beyond the candidate's history that rule a job out
DEADLINE_SOON_DAYS = 7
LABELS = {
    "full_time": "full-time",
    "part_time": "part-time",
    "internship": "internship",
    "contract": "contract",
    "freelance": "freelance",
    "volunteer": "volunteer",
    "other": "other",
    "onsite": "on-site",
    "hybrid": "hybrid",
    "remote": "remote",
}


@dataclass
class Evaluation:
    posting: NormalizedJob
    pairs: list[tuple[JobRequirement, Assessment]] = field(default_factory=list)
    retrieved: dict[uuid.UUID, RetrievedEvidence] = field(default_factory=dict)
    scores: Scores | None = None


def requirements_of(posting: NormalizedJob) -> list[JobRequirement]:
    """The posting's stated requirements (rule-based analysis; informational statements and
    anything not stated in the text are left out)."""
    text = normalize_text(posting.description)
    extraction = ground(HeuristicJobAnalyzer().analyze(text), text)
    return [
        JobRequirement(
            id=uuid.uuid4(),
            requirement_type=r.requirement_type,
            importance=r.importance,
            description=r.description,
            min_years=r.min_years,
            sort_order=n,
        )
        for n, r in enumerate(extraction.requirements)
        if r.importance != RequirementImportance.INFORMATIONAL
    ]


def _query(requirement: JobRequirement) -> str:
    if requirement.requirement_type == RequirementType.TECHNOLOGY:
        return f"Experience with {requirement.description}"
    return requirement.description


async def evaluate(
    session: AsyncSession,
    profile_id: uuid.UUID,
    posting: NormalizedJob,
    facts: CandidateFacts,
    embedder: EmbeddingProvider,
) -> Evaluation:
    evaluation = Evaluation(posting)
    requirements = requirements_of(posting)
    if not requirements:
        return evaluation
    retrieved = await retrieve_verified_for_queries(
        session, profile_id, [_query(r) for r in requirements], embedder, top_k=5
    )
    judge = RuleJudge(embedder.confidence_thresholds)
    evaluation.pairs = [
        (r, judge.assess(r, found, facts)) for r, found in zip(requirements, retrieved, strict=True)
    ]
    evaluation.retrieved = {e.evidence_id: e for found in retrieved for e in found}
    evaluation.scores = score(evaluation.pairs)
    return evaluation


# --- Eligibility --------------------------------------------------------------------------


def _label(value: object) -> str:
    return LABELS.get(str(getattr(value, "value", value)), str(value))


def eligibility(
    evaluation: Evaluation, profile: CandidateProfile, facts: CandidateFacts, today: date
) -> tuple[list[str], list[str]]:
    """(exclusions, concerns). Exclusions filter a job out; concerns are shown with it."""
    posting = evaluation.posting
    exclusions: list[str] = []
    concerns: list[str] = []

    if not evaluation.pairs:
        exclusions.append(
            "The posting states no requirements to compare with your evidence, so a "
            "recommendation couldn't be explained."
        )
    if posting.deadline is not None:
        if posting.deadline < today:
            exclusions.append(f"The application deadline ({posting.deadline}) has passed.")
        elif posting.deadline <= today + timedelta(days=DEADLINE_SOON_DAYS):
            days = (posting.deadline - today).days
            concerns.append(
                f"Apply by {posting.deadline}: {days} day{'s' if days != 1 else ''} left."
            )
    if (
        profile.job_types
        and posting.employment_type
        and posting.employment_type not in profile.job_types
    ):
        wanted = ", ".join(_label(t) for t in profile.job_types)
        exclusions.append(f"It's a {_label(posting.employment_type)} role; you prefer {wanted}.")
    if profile.work_modes and posting.work_mode and posting.work_mode not in profile.work_modes:
        wanted = ", ".join(_label(m) for m in profile.work_modes)
        exclusions.append(f"It's {_label(posting.work_mode)}; you prefer {wanted} work.")
    if (
        profile.preferred_locations
        and posting.location
        and posting.work_mode != WorkplaceType.REMOTE
        and not any(p.lower() in posting.location.lower() for p in profile.preferred_locations)
    ):
        concerns.append(
            f"{posting.location} isn't one of your preferred locations "
            f"({', '.join(profile.preferred_locations)})."
        )

    have = facts.experience_years or 0.0
    for requirement, assessment in evaluation.pairs:
        required = requirement.importance == REQUIRED
        kind = requirement.requirement_type
        if kind == RequirementType.EXPERIENCE and required and requirement.min_years is not None:
            years = float(requirement.min_years)
            if years >= have + TOO_MANY_YEARS:
                exclusions.append(
                    f"Requires {years:g}+ years of experience; your work history adds up to "
                    f"about {have:.1f}."
                )
            elif assessment.status != M:
                concerns.append(f"{requirement.description} {assessment.explanation}")
        elif (required and kind == RequirementType.ELIGIBILITY and assessment.status in (X, U)) or (
            required
            and kind in (RequirementType.EDUCATION, RequirementType.CERTIFICATION)
            and (assessment.status != M)
        ):
            concerns.append(f"{requirement.description} {assessment.explanation}")

    assessable = [a for r, a in evaluation.pairs if r.importance == REQUIRED and a.status != U]
    if evaluation.pairs and assessable and not any(a.status in (M, P) for a in assessable):
        exclusions.append(
            "Your verified evidence doesn't cover any of the job's required requirements."
        )
    return list(dict.fromkeys(exclusions)), list(dict.fromkeys(concerns))


# --- Explanation --------------------------------------------------------------------------


def preference_fit(posting: NormalizedJob, profile: CandidateProfile) -> list[str]:
    """Stated preferences the posting satisfies."""
    fit = []
    title = posting.title.lower()
    for role in profile.preferred_roles:
        words = role.lower().split()
        if words and all(w in title for w in words):
            fit.append(f"Matches your preferred role “{role}”.")
            break
    if profile.work_modes and posting.work_mode in profile.work_modes:
        fit.append(f"{_label(posting.work_mode).capitalize()}, as you prefer.")
    if profile.job_types and posting.employment_type in profile.job_types:
        kind = _label(posting.employment_type)
        fit.append(f"{'An' if kind[0] in 'aeiou' else 'A'} {kind} role, as you prefer.")
    if profile.preferred_locations and posting.work_mode == WorkplaceType.REMOTE:
        fit.append("Remote, so it works from your preferred locations.")
    elif (
        profile.preferred_locations
        and posting.location
        and any(p.lower() in posting.location.lower() for p in profile.preferred_locations)
    ):
        fit.append(f"In {posting.location}, one of your preferred locations.")
    if profile.experience_level and posting.experience_level == profile.experience_level:
        fit.append(
            f"At your experience level ({_label(posting.experience_level).replace('_', ' ')})."
        )
    return fit


def _skills(
    pairs: list[tuple[JobRequirement, Assessment]], status: MatchStatus
) -> list[SkillResult]:
    """Skills with this status, by name: a statement ("Experience with Python and SQL.") is
    shown as the technologies it names, and each skill appears once."""
    found: dict[str, SkillResult] = {}
    for r, a in pairs:
        if a.status != status or r.requirement_type not in SKILL_TYPES:
            continue
        names = (
            [r.description]
            if r.requirement_type == RequirementType.TECHNOLOGY
            else find_technologies(r.description) or [short(r.description)]
        )
        for name in names:
            found.setdefault(
                name.lower(),
                SkillResult(skill=name, importance=r.importance, explanation=a.explanation),
            )
    return list(found.values())


def short(statement: str, limit: int = 70) -> str:
    """A requirement for listing inside a sentence: no final full stop, not too long."""
    text = statement.strip().rstrip(".")
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _projects(evaluation: Evaluation) -> list[RelevantProject]:
    """Projects whose verified evidence the matches rest on."""
    projects: dict[str, RelevantProject] = {}
    for requirement, assessment in evaluation.pairs:
        if assessment.status not in (M, P):
            continue
        for evidence_id in assessment.evidence_ids:
            e = evaluation.retrieved.get(evidence_id)
            if e is None or e.source.record_type != "project":
                continue
            title = e.source.record_label or "Project"
            project = projects.setdefault(
                title,
                RelevantProject(
                    project_id=e.source.record_id, title=title, evidence=[], supports=[]
                ),
            )
            if e.factual_content not in project.evidence:
                project.evidence.append(e.factual_content)
            label = short(requirement.description)
            if label not in project.supports:
                project.supports.append(label)
    return sorted(projects.values(), key=lambda p: -len(p.supports))


def _listed(items: list[str], limit: int = 3) -> str:
    items = items[:limit]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


def explain(evaluation: Evaluation, profile: CandidateProfile) -> Explanation:
    pairs = evaluation.pairs
    required = [(r, a) for r, a in pairs if r.importance == REQUIRED and a.status != U]
    met = [short(r.description) for r, a in required if a.status == M]
    partial = [short(r.description) for r, a in required if a.status == P]
    matched = _skills(pairs, M)
    fit = preference_fit(evaluation.posting, profile)
    projects = _projects(evaluation)

    reasons = []
    if required:
        line = f"Your verified evidence covers {len(met)} of {len(required)} required requirements"
        reasons.append(line + (f": {_listed(met)}." if met else "."))
        if partial:
            reasons.append(f"Partly covers {len(partial)} more: {_listed(partial)}.")
    preferred_met = [
        short(r.description) for r, a in pairs if r.importance == PREFERRED and a.status == M
    ]
    if preferred_met:
        reasons.append(f"Also meets preferred requirements: {_listed(preferred_met)}.")
    if matched:
        reasons.append(
            f"Uses skills your evidence shows: {_listed([s.skill for s in matched], 5)}."
        )
    for project in projects[:2]:
        reasons.append(
            f"Your {project.title} project is relevant: it covers {_listed(project.supports)}."
        )
    reasons += fit

    if required:
        summary = (
            f"Recommended because your evidence covers {len(met)} of {len(required)} "
            "required requirements"
        )
        if matched:
            summary += f", including {_listed([s.skill for s in matched])}"
        summary += f"{'; ' + fit[0][0].lower() + fit[0][1:].rstrip('.') if fit else ''}."
    else:
        summary = "No required requirements could be assessed from the posting."
    return Explanation(
        summary=summary,
        reasons=reasons,
        required_met=len(met),
        required_partial=len(partial),
        required_total=len(required),
        matched_skills=matched,
        partial_skills=_skills(pairs, P),
        missing_skills=_skills(pairs, X),
        requirements=[
            RequirementResult(
                requirement=r.description,
                requirement_type=r.requirement_type,
                importance=r.importance,
                status=a.status,
                explanation=a.explanation,
            )
            for r, a in pairs
        ],
        relevant_projects=projects,
        preference_fit=fit,
    )
