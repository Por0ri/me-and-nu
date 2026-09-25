"""Shared public API schemas and JSON naming rules."""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class ErrorResponse(CamelModel):
    code: str
    message: str
    fields: list[dict[str, str]] | None = None
    request_id: str | None = None


COMMON_ERRORS = {
    401: {
        "model": ErrorResponse,
        "description": "로그인 세션이 필요하거나 만료되었습니다.",
        "content": {"application/json": {"example": {"code": "AUTH_REQUIRED", "message": "로그인이 필요합니다."}}},
    },
    403: {
        "model": ErrorResponse,
        "description": "CSRF 검증 또는 온보딩 상태가 요청에 맞지 않습니다.",
        "content": {"application/json": {"example": {"code": "CSRF_INVALID", "message": "CSRF 토큰이 올바르지 않습니다."}}},
    },
    422: {
        "model": ErrorResponse,
        "description": "요청 값이 올바르지 않습니다.",
        "content": {"application/json": {"example": {"code": "VALIDATION_ERROR", "message": "입력값을 확인해 주세요.", "fields": []}}},
    },
}
