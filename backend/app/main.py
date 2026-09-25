from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as v1_router
from app.api.dev.auth import router as dev_auth_router
from app.api.dev.agent_runs import router as dev_agent_runs_router
from app.api.dev.agent_publication import router as dev_agent_publication_router
from app.api.health import router as health_router
from app.core.config import settings
from app.core.exceptions import install_exception_handlers

def create_app(enable_dev_api: bool | None = None) -> FastAPI:
    dev_api_enabled = settings.enable_dev_api if enable_dev_api is None else enable_dev_api
    description = "MeNu V1.0 PostgreSQL API."
    if dev_api_enabled and settings.enable_dev_auth_bypass:
        description += " 로컬 Swagger 테스트 모드: 로그인과 CSRF 입력 없이 전용 데모 사용자로 보호 API를 호출할 수 있습니다."
    else:
        description += " 개발용 로그인 후 /auth/session의 csrfToken을 Swagger Authorize에 입력하세요."
    api = FastAPI(
        title="큐레이팅 플랫폼 REST API",
        version="1.0.0",
        description=description,
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    api.include_router(health_router)
    api.include_router(v1_router, prefix="/api/v1")
    if dev_api_enabled:
        api.include_router(dev_auth_router, prefix="/api/v1/dev")
        api.include_router(dev_agent_runs_router, prefix="/api/v1/dev")
        api.include_router(dev_agent_publication_router, prefix="/api/v1/dev")
    install_exception_handlers(api)
    return api


app = create_app()
