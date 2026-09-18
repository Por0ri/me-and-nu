from fastapi import APIRouter

from app.api.routes import auth, contents

api_router = APIRouter()

api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["인증"],
)

api_router.include_router(
    contents.router,
    prefix="/contents",
    tags=["콘텐츠"],
)