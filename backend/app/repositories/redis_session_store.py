"""Redis persistence for fixed-lifetime opaque browser sessions."""

import hmac
import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.exceptions import ApiError
from app.core.security import csrf_token_for_session, hash_token
from app.models import AuthSession

logger = logging.getLogger(__name__)


class _SessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_token_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    csrf_token_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    user_id: int | None = Field(gt=0)
    session_state: Literal["anonymous", "onboarding_pending", "active"]
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_session(self) -> "_SessionPayload":
        if (self.session_state == "anonymous") != (self.user_id is None):
            raise ValueError("Session state and user do not match")
        if self.expires_at <= self.created_at:
            raise ValueError("Session expiry must follow creation")
        return self


def _store_unavailable() -> ApiError:
    logger.warning("Session store operation failed")
    return ApiError(
        503,
        "SESSION_STORE_UNAVAILABLE",
        "로그인 세션을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    )


class RedisSessionStore:
    def __init__(self, client: Redis, *, prefix: str = "me_nu") -> None:
        self.client = client
        self.prefix = prefix

    def _key(self, token_hash: str) -> str:
        return f"{self.prefix}:auth-session:v1:{token_hash}"

    async def save(self, row: AuthSession) -> None:
        if row.revoked_at is not None:
            raise ValueError("Revoked sessions cannot be saved")
        payload = _SessionPayload(
            session_token_hash=row.session_token_hash,
            csrf_token_hash=row.csrf_token_hash,
            user_id=row.user_id,
            session_state=row.session_state,
            created_at=row.created_at,
            expires_at=row.expires_at,
        )
        if payload.expires_at <= datetime.now(timezone.utc):
            raise ValueError("Expired sessions cannot be saved")
        try:
            created = await self.client.set(
                self._key(payload.session_token_hash),
                payload.model_dump_json(),
                pxat=int(payload.expires_at.timestamp() * 1000),
                nx=True,
            )
        except RedisError:
            raise _store_unavailable() from None
        if not created:
            raise _store_unavailable()

    async def find(self, raw_token: str) -> AuthSession | None:
        token_hash = hash_token(raw_token)
        try:
            raw_payload = await self.client.get(self._key(token_hash))
        except RedisError:
            raise _store_unavailable() from None
        except UnicodeDecodeError:
            logger.warning("Invalid session store payload")
            return None
        if raw_payload is None:
            return None
        try:
            payload = _SessionPayload.model_validate_json(raw_payload)
        except ValidationError:
            logger.warning("Invalid session store payload")
            return None
        if not hmac.compare_digest(payload.session_token_hash, token_hash) or not hmac.compare_digest(
            payload.csrf_token_hash, hash_token(csrf_token_for_session(raw_token))
        ):
            logger.warning("Invalid session store payload")
            return None
        if payload.expires_at <= datetime.now(timezone.utc):
            return None
        return AuthSession(**payload.model_dump())

    async def revoke(self, row: AuthSession) -> None:
        try:
            await self.client.delete(self._key(row.session_token_hash))
        except RedisError:
            raise _store_unavailable() from None
        row.revoked_at = datetime.now(timezone.utc)
