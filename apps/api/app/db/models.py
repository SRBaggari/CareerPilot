"""Registry of ORM models. Importing this module registers every table on ``Base.metadata``
(used by Alembic autogenerate and by mapper configuration)."""

from app.ai.models import AIExecutionLog
from app.applications.models import Application, ApplicationStatusHistory, FollowUp, Interview
from app.db.base import Base
from app.documents.models import (
    ClaimVerification,
    CoverLetter,
    GeneratedClaim,
    TailoredResume,
    generated_claim_evidence,
)
from app.jobs.models import Job, JobRequirement
from app.matching.models import JobMatch, SkillGap
from app.profiles.models import (
    Achievement,
    CandidateEvidence,
    CandidateProfile,
    CandidateSkill,
    Certification,
    Coursework,
    Education,
    Project,
    Resume,
    Skill,
    WorkExperience,
    candidate_evidence_skills,
)
from app.users.models import User

__all__ = [
    "AIExecutionLog",
    "Achievement",
    "Application",
    "ApplicationStatusHistory",
    "Base",
    "CandidateEvidence",
    "CandidateProfile",
    "CandidateSkill",
    "Certification",
    "ClaimVerification",
    "Coursework",
    "CoverLetter",
    "Education",
    "FollowUp",
    "GeneratedClaim",
    "Interview",
    "Job",
    "JobMatch",
    "JobRequirement",
    "Project",
    "Resume",
    "Skill",
    "SkillGap",
    "TailoredResume",
    "User",
    "WorkExperience",
    "candidate_evidence_skills",
    "generated_claim_evidence",
]
