from fastapi import APIRouter

from app.api.routes import candidate_evidence, health, profile, resumes

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(profile.router)
api_router.include_router(resumes.router)
api_router.include_router(candidate_evidence.router)
