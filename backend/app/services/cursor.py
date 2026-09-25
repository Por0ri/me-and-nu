import base64
import binascii
import json
from typing import Any

from app.core.exceptions import ApiError


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(value: str, *, expected: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw)
        if not isinstance(payload, dict) or any(
            payload.get(key) != wanted for key, wanted in expected.items()
        ):
            raise ValueError("cursor context mismatch")
        return payload
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ApiError(422, "INVALID_CURSOR", "유효하지 않은 cursor입니다.") from exc
