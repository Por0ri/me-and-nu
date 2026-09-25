"""Public V1 API, organized by first URL resource."""

from fastapi import APIRouter

from app.api.v1.auth.router import router as auth_router
from app.api.v1.contents.router import router as contents_router
from app.api.v1.me.topics import router as my_topics_router
from app.api.v1.onboarding.router import router as onboarding_router
from app.api.v1.policies.router import router as policies_router
from app.api.v1.subtopics.router import router as subtopics_router
from app.api.v1.topics.router import router as topics_router
from app.api.v1.users.me.consents import router as consents_router
from app.api.v1.users.me.router import router as profile_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(onboarding_router)
router.include_router(policies_router)
router.include_router(topics_router)
router.include_router(subtopics_router)
router.include_router(my_topics_router)
router.include_router(contents_router)
router.include_router(profile_router)
router.include_router(consents_router)
