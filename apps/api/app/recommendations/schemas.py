import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.applications.models import ApplicationStatus
from app.discovery.schemas import SourceOut
from app.jobs.models import RequirementImportance, RequirementType
from app.matching.models import MatchStatus
from app.profiles.models import EmploymentType, ExperienceLevel, WorkplaceType
from app.recommendations.models import RecommendationStatus

DISCLAIMER = (
    "Recommendations explain how your verified evidence covers each job's stated "
    "requirements. They are not a prediction of being hired."
)


class RequirementResult(BaseModel):
    requirement: str
    requirement_type: RequirementType
    importance: RequirementImportance
    status: MatchStatus
    explanation: str  # names the evidence it rests on


class SkillResult(BaseModel):
    skill: str
    importance: RequirementImportance
    explanation: str


class RelevantProject(BaseModel):
    project_id: uuid.UUID | None
    title: str
    evidence: list[str]  # the project's verified evidence behind the match, verbatim
    supports: list[str]  # the job requirements that evidence covers


class Explanation(BaseModel):
    summary: str  # one plain sentence: why this job is recommended
    reasons: list[str]  # every reason, each tied to evidence or a stated preference
    required_met: int
    required_partial: int
    required_total: int
    matched_skills: list[SkillResult]
    partial_skills: list[SkillResult]
    missing_skills: list[SkillResult]
    requirements: list[RequirementResult]
    relevant_projects: list[RelevantProject]
    preference_fit: list[str]
    disclaimer: str = DISCLAIMER


class ApplicationRef(BaseModel):
    id: uuid.UUID
    status: ApplicationStatus


class RecommendationOut(BaseModel):
    id: uuid.UUID
    source: str
    source_identifier: str
    title: str
    company: str
    location: str | None
    url: str | None
    work_mode: WorkplaceType | None
    employment_type: EmploymentType | None
    experience_level: ExperienceLevel | None
    posted_date: date | None
    deadline: date | None
    status: RecommendationStatus
    eligible: bool
    exclusions: list[str]  # why it was filtered out (empty when eligible)
    concerns: list[str]  # eligibility concerns worth checking before applying
    explanation: Explanation
    required_coverage: float | None
    overall_coverage: float | None
    rank: int | None
    computed_at: datetime
    is_stale: bool  # the profile changed since this was computed
    job_id: uuid.UUID | None  # set once analyzed / imported
    application: ApplicationRef | None


class RecommendationListOut(BaseModel):
    recommendations: list[RecommendationOut]
    counts: dict[str, int]  # recommended / saved / ignored / filtered_out
    refreshed_at: datetime | None
    errors: list[str]  # job sources that failed during the last refresh
    sources: list[SourceOut]


class ActionResultOut(BaseModel):
    recommendation: RecommendationOut
    job_id: uuid.UUID | None
    application_id: uuid.UUID | None = None
