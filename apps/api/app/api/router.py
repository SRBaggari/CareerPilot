from fastapi import APIRouter

from app.api.routes import (
    application_answers,
    candidate_evidence,
    cover_letters,
    discovery,
    health,
    jobs,
    matching,
    profile,
    resumes,
    tailored_resumes,
    verification,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(profile.router)
api_router.include_router(resumes.router)
api_router.include_router(candidate_evidence.router)
api_router.include_router(jobs.router)
api_router.include_router(matching.router)
api_router.include_router(tailored_resumes.router)
api_router.include_router(verification.router)
api_router.include_router(cover_letters.router)
api_router.include_router(application_answers.router)
api_router.include_router(discovery.router)
