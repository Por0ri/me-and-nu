from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.api.dev.auth import router as dev_auth_router
from app.api.dev.agent_runs import router as dev_agent_runs_router
from app.api.dev.agent_publication import router as dev_agent_publication_router
from app.api.health import router as health_router
from app.core.config import settings
from app.core.exceptions import install_exception_handlers

def create_app(enable_dev_api: bool | None = None) -> FastAPI:
    dev_api_enabled = settings.enable_dev_api if enable_dev_api is None else enable_dev_api
    description = "현재 구현된 공개 API를 V1.1 계약에 맞춘 로컬 범위입니다. 미구현 V1.1 API는 등록되지 않습니다."
    if dev_api_enabled and settings.enable_dev_auth_bypass:
        description += " 로컬 Swagger 테스트 모드: 로그인과 CSRF 입력 없이 전용 데모 사용자로 보호 API를 호출할 수 있습니다."
    else:
        description += " 개발용 로그인 후 /auth/session의 csrfToken을 Swagger Authorize에 입력하세요."
    api = FastAPI(
        title="큐레이팅 플랫폼 REST API",
        version="1.1.0-local",
        description=description,
    )
    @api.middleware("http")
    async def v1_response_contract(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/v1/") and request.method in {"POST", "PUT", "PATCH"}:
            has_body = request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if has_body and content_type and content_type != "application/json" and not content_type.endswith("+json"):
                return JSONResponse(
                    status_code=415,
                    content={"code": "UNSUPPORTED_MEDIA_TYPE", "message": "JSON Content-Type을 사용해 주세요."},
                    headers={"Cache-Control": "private, no-store"},
                )
        response = await call_next(request)
        if path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

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
