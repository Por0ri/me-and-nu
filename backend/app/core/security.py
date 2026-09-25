import hashlib
import hmac
import secrets

from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()

SESSION_COOKIE_NAME = settings.session_cookie_name
SESSION_EXPIRE_MINUTES = settings.session_expire_minutes


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(
    plain_password: str,
    hashed_password: str,
) -> bool:
    return password_hash.verify(
        plain_password,
        hashed_password,
    )


def create_session_id() -> str:
    return secrets.token_urlsafe(32)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_token_for_session(session_id: str) -> str:
    # The opaque HttpOnly session value is random. A deterministic token lets
    # /auth/session return CSRF after a page reload without storing it in clear.
    return hmac.new(
        session_id.encode("utf-8"),
        b"menu-csrf-v1",
        hashlib.sha256,
    ).hexdigest()


def set_session_cookie(response: "Response", raw_token: str) -> None:
    from fastapi import Response

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=SESSION_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def delete_session_cookie(response: "Response") -> None:
    from fastapi import Response

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
