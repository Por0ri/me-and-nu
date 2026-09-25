"""One public error contract for route, validation, and unexpected failures."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        fields: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = fields


def _payload(
    code: str,
    message: str,
    fields: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    if fields is not None:
        result["fields"] = fields
    result["requestId"] = None
    return result


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.code, exc.message, exc.fields),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"] if part != "body"),
                "message": str(error["msg"]),
            }
            for error in errors
        ]
        return JSONResponse(
            status_code=400 if any(error["type"] == "json_invalid" for error in errors) else 422,
            content=_payload("VALIDATION_ERROR", "입력값을 확인해 주세요.", fields),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            code = str(exc.detail.get("code", "VALIDATION_ERROR"))
            message = str(exc.detail.get("message", "요청을 처리할 수 없습니다."))
        else:
            code = {401: "AUTH_REQUIRED", 403: "CSRF_INVALID", 404: "NOT_FOUND", 409: "STATE_CONFLICT", 422: "VALIDATION_ERROR"}.get(exc.status_code, "INTERNAL_ERROR")
            message = str(exc.detail) if isinstance(exc.detail, str) else "요청을 처리할 수 없습니다."
        return JSONResponse(status_code=exc.status_code, content=_payload(code, message), headers=exc.headers)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unexpected API failure", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_payload("INTERNAL_ERROR", "서버 오류가 발생했습니다."),
        )
