"""Behavioural tests for relationships and integrity constraints.

All rows are created inside a rolled-back transaction; nothing is persisted.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.applications.models import (
    APPROVAL_REQUIRED_STATUSES,
    Application,
    ApplicationStatus,
    ApprovalState,
)
from app.db.vector import EMBEDDING_DIMENSIONS
from app.documents.models import (
    ClaimVerification,
    GeneratedClaim,
    TailoredResume,
    VerificationMethod,
    VerificationVerdict,
)
from app.jobs.models import Job, JobSource
from app.profiles.models import (
    CandidateEvidence,
    CandidateProfile,
    DocumentFormat,
    EvidenceOrigin,
    EvidenceSourceType,
    Project,
    Resume,
)
from app.users.models import User

pytestmark = pytest.mark.anyio


@dataclass
class Graph:
    user: User
    profile: CandidateProfile
    project: Project
    evidence: CandidateEvidence
    job: Job
    resume: TailoredResume


async def _graph(db: AsyncSession) -> Graph:
    user = User(email="test-user@example.test")
    profile = CandidateProfile(user=user, full_name="Test Candidate")
    project = Project(profile=profile, title="Multi-Agent Research Assistant")
    evidence = CandidateEvidence(
        profile=profile,
        project=project,
        source_type=EvidenceSourceType.PROJECT,
        origin=EvidenceOrigin.USER_ENTERED,
        content="Implemented RAG pipeline using document retrieval and question answering.",
    )
    job = Job(
        source=JobSource.MANUAL,
        title="ML Engineer",
        company_name="Example Co",
        description="Job description",
    )
    db.add_all([user, profile, project, evidence, job])
    await db.flush()
    resume = TailoredResume(candidate_profile_id=profile.id, job_id=job.id, content={})
    db.add(resume)
    await db.flush()
    return Graph(user, profile, project, evidence, job, resume)


async def test_generated_claim_traces_back_to_evidence(db: AsyncSession) -> None:
    g = await _graph(db)
    claim = GeneratedClaim(
        tailored_resume=g.resume,
        claim_text="Built a RAG-based research assistant",
        evidence=[g.evidence],
    )
    claim.verifications.append(
        ClaimVerification(verdict=VerificationVerdict.SUPPORTED, method=VerificationMethod.LLM)
    )
    db.add(claim)
    await db.flush()
    db.expunge_all()

    loaded = await db.scalar(
        select(GeneratedClaim)
        .where(GeneratedClaim.id == claim.id)
        .options(
            selectinload(GeneratedClaim.evidence).selectinload(CandidateEvidence.project),
            selectinload(GeneratedClaim.verifications),
        )
    )
    assert loaded is not None
    [cited] = loaded.evidence
    assert cited.content.startswith("Implemented RAG pipeline")
    assert cited.project is not None
    assert cited.project.title == "Multi-Agent Research Assistant"
    assert [v.verdict for v in loaded.verifications] == [VerificationVerdict.SUPPORTED]


async def test_evidence_subject_must_match_source_type(db: AsyncSession) -> None:
    g = await _graph(db)
    db.add(
        CandidateEvidence(
            candidate_profile_id=g.profile.id,
            source_type=EvidenceSourceType.PROJECT,  # but no project_id
            origin=EvidenceOrigin.USER_ENTERED,
            content="Something",
        )
    )
    with pytest.raises(IntegrityError, match="project_matches_type"):
        await db.flush()


async def test_profile_level_evidence_has_no_subject(db: AsyncSession) -> None:
    g = await _graph(db)
    db.add(
        CandidateEvidence(
            candidate_profile_id=g.profile.id,
            source_type=EvidenceSourceType.PROFILE,
            project_id=g.project.id,  # subject not allowed for profile-level evidence
            origin=EvidenceOrigin.USER_ENTERED,
            content="Something",
        )
    )
    with pytest.raises(IntegrityError, match="project_matches_type"):
        await db.flush()


async def test_resume_source_requires_extracted_origin(db: AsyncSession) -> None:
    g = await _graph(db)
    upload = Resume(
        candidate_profile_id=g.profile.id,
        file_name="resume.pdf",
        file_format=DocumentFormat.PDF,
        storage_key="k",
        file_size_bytes=1,
        sha256="0" * 64,
    )
    db.add(upload)
    await db.flush()
    g.evidence.source_resume_id = upload.id  # origin is still user_entered
    with pytest.raises(IntegrityError, match="source_resume_implies_extracted"):
        await db.flush()


async def test_blank_evidence_rejected(db: AsyncSession) -> None:
    g = await _graph(db)
    g.evidence.content = "   "
    with pytest.raises(IntegrityError, match="content_not_blank"):
        await db.flush()


async def test_claim_belongs_to_exactly_one_document(db: AsyncSession) -> None:
    await _graph(db)
    db.add(GeneratedClaim(claim_text="Orphan claim"))
    with pytest.raises(IntegrityError, match="exactly_one_document"):
        await db.flush()


async def test_cited_evidence_cannot_be_deleted(db: AsyncSession) -> None:
    g = await _graph(db)
    db.add(GeneratedClaim(tailored_resume=g.resume, claim_text="Claim", evidence=[g.evidence]))
    await db.flush()
    await db.execute(text("DELETE FROM candidate_evidence WHERE id = :id"), {"id": g.evidence.id})
    # The FK is deferred; force the check that COMMIT would perform.
    with pytest.raises(IntegrityError, match="generated_claim_evidence"):
        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


async def test_deleting_user_removes_all_their_data(db: AsyncSession) -> None:
    g = await _graph(db)
    db.add(GeneratedClaim(tailored_resume=g.resume, claim_text="Claim", evidence=[g.evidence]))
    await db.flush()

    await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": g.user.id})
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # what COMMIT would check

    for table in (
        "candidate_profiles",
        "projects",
        "candidate_evidence",
        "tailored_resumes",
        "generated_claims",
        "generated_claim_evidence",
    ):
        count = await db.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        assert count == 0, table
    # Jobs are shared, not personal data.
    assert await db.scalar(select(func.count()).select_from(Job)) == 1


@pytest.mark.parametrize("status", APPROVAL_REQUIRED_STATUSES)
async def test_application_status_requires_approval(
    db: AsyncSession, status: ApplicationStatus
) -> None:
    g = await _graph(db)
    db.add(Application(candidate_profile_id=g.profile.id, job_id=g.job.id, status=status))
    with pytest.raises(IntegrityError, match="approval_required"):
        await db.flush()


async def test_application_cannot_be_submitted_before_approval(db: AsyncSession) -> None:
    g = await _graph(db)
    now = datetime.now(UTC)
    db.add(
        Application(
            candidate_profile_id=g.profile.id,
            job_id=g.job.id,
            status=ApplicationStatus.SUBMITTED,
            approved_at=now,
            submitted_at=now - timedelta(minutes=1),
        )
    )
    with pytest.raises(IntegrityError, match="submitted_after_approval"):
        await db.flush()


async def test_approved_then_submitted_application_is_valid(db: AsyncSession) -> None:
    g = await _graph(db)
    now = datetime.now(UTC)
    db.add(
        Application(
            candidate_profile_id=g.profile.id,
            job_id=g.job.id,
            tailored_resume_id=g.resume.id,
            status=ApplicationStatus.SUBMITTED,
            approval_state=ApprovalState.SUBMITTED,
            approved_at=now,
            submitted_at=now + timedelta(minutes=5),
        )
    )
    await db.flush()


async def test_enum_columns_reject_unknown_values(db: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_skills_category_enum"):
        await db.execute(
            text("INSERT INTO skills (name, normalized_name, category) VALUES ('X', 'x', 'bogus')")
        )


async def test_only_one_primary_resume_per_profile(db: AsyncSession) -> None:
    g = await _graph(db)
    for digest in ("a" * 64, "b" * 64):
        db.add(
            Resume(
                candidate_profile_id=g.profile.id,
                file_name="r.pdf",
                file_format=DocumentFormat.PDF,
                storage_key=digest,
                file_size_bytes=1,
                sha256=digest,
                is_primary=True,
            )
        )
    with pytest.raises(IntegrityError, match="uq_resumes_primary_per_profile"):
        await db.flush()


async def test_evidence_embedding_supports_cosine_search(db: AsyncSession) -> None:
    g = await _graph(db)
    near = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
    far = [0.0, 1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 2)
    g.evidence.embedding = far
    other = CandidateEvidence(
        candidate_profile_id=g.profile.id,
        source_type=EvidenceSourceType.PROFILE,
        origin=EvidenceOrigin.USER_ENTERED,
        content="Closest statement",
        embedding=near,
    )
    db.add(other)
    await db.flush()

    query_vector = [0.9, 0.1] + [0.0] * (EMBEDDING_DIMENSIONS - 2)
    ranked = await db.scalars(
        select(CandidateEvidence.content).order_by(
            CandidateEvidence.embedding.cosine_distance(query_vector)
        )
    )
    assert ranked.first() == "Closest statement"
