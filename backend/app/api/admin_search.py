"""Bounded private search carried in a header, not in URLs or access logs."""

import re
import unicodedata
from typing import Annotated
from urllib.parse import unquote_to_bytes

from fastapi import Header, Request

from app.core.exceptions import ApiError


def read_admin_search(
    request: Request,
    value: Annotated[str | None, Header(alias="X-Admin-Search")] = None,
) -> str:
    """Decode exactly once; errors must never echo a private search value."""
    def invalid():
        return ApiError(422, "VALIDATION_ERROR", "검색 조건을 확인해 주세요.")

    if "q" in request.query_params or len(request.headers.getlist("X-Admin-Search")) > 1:
        raise invalid()
    if value is None:
        return ""
    if len(value) > 3600 or not value.isascii() or re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise invalid()
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", errors="strict")
    except (UnicodeError, ValueError):
        raise invalid() from None
    if len(decoded) > 300 or any(unicodedata.category(c) in {"Cc", "Cs"} for c in decoded):
        raise invalid()
    return decoded.strip()
