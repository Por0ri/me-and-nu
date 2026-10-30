"""Exchange provider authorization codes without exposing provider credentials."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from email_validator import EmailNotValidError, validate_email

from app.core.config import settings
from app.core.exceptions import ApiError

_ENDPOINTS = {
    "kakao": (
        "https://kauth.kakao.com/oauth/authorize",
        "https://kauth.kakao.com/oauth/token",
        "https://kapi.kakao.com/v2/user/me",
    ),
    "naver": (
        "https://nid.naver.com/oauth2.0/authorize",
        "https://nid.naver.com/oauth2.0/token",
        "https://openapi.naver.com/v1/nid/me",
    ),
}


@dataclass(frozen=True)
class OAuthIdentity:
    provider: str
    provider_user_id: str
    email: str | None
    nickname: str | None


def _failed() -> ApiError:
    return ApiError(401, "AUTH_FAILED", "로그인을 완료할 수 없습니다. 다시 시도해 주세요.")


def _unavailable() -> ApiError:
    return ApiError(503, "AUTH_UNAVAILABLE", "로그인을 일시적으로 이용할 수 없습니다. 잠시 후 다시 시도해 주세요.")


def _endpoints(provider: str) -> tuple[str, str, str]:
    if provider not in _ENDPOINTS:
        raise _failed()
    return _ENDPOINTS[provider]


def _credentials(provider: str) -> tuple[str, str]:
    client_id = getattr(settings, f"{provider}_client_id")
    secret = getattr(settings, f"{provider}_client_secret")
    if not client_id or not client_id.strip() or secret is None:
        raise _unavailable()
    client_secret = secret.get_secret_value()
    if not client_secret.strip():
        raise _unavailable()
    return client_id, client_secret


def _nonblank_string(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _failed()
    return value


def _email(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _failed()
    try:
        # Provider data is already supplied: validation must not perform DNS I/O.
        return validate_email(value, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise _failed() from None


def _nickname(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _failed()
    return value.strip() or None


def _kakao_identity(payload: dict[str, Any]) -> OAuthIdentity:
    identifier = payload.get("id")
    if type(identifier) is not int or identifier <= 0 or "code" in payload:
        raise _failed()
    account = payload.get("kakao_account", {})
    if not isinstance(account, dict):
        raise _failed()
    profile = account.get("profile", {})
    if not isinstance(profile, dict):
        raise _failed()
    email = None
    if account.get("is_email_valid") is True and account.get("is_email_verified") is True:
        email = _email(account.get("email"))
    return OAuthIdentity("kakao", str(identifier), email, _nickname(profile.get("nickname")))


def _naver_identity(payload: dict[str, Any]) -> OAuthIdentity:
    profile = payload.get("response")
    if payload.get("resultcode") != "00" or not isinstance(profile, dict):
        raise _failed()
    identifier = _nonblank_string(profile.get("id"))
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in identifier):
        raise _failed()
    return OAuthIdentity("naver", identifier, _email(profile.get("email")), _nickname(profile.get("nickname")))


class OAuthProviderClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def build_authorization_url(self, provider: str, state: str) -> str:
        authorize_url, _, _ = _endpoints(provider)
        client_id, _ = _credentials(provider)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": settings.oauth_callback_url(provider),
            "state": _nonblank_string(state),
        }
        # Consent items are configured in each provider's console; no email scope
        # is required merely to identify a member.
        return f"{authorize_url}?{urlencode(params)}"

    async def exchange_identity(self, provider: str, code: str, state: str) -> OAuthIdentity:
        _, token_url, profile_url = _endpoints(provider)
        client_id, client_secret = _credentials(provider)
        _nonblank_string(state)
        data = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": settings.oauth_callback_url(provider),
            "code": _nonblank_string(code),
        }
        if provider == "naver":
            data["state"] = state
        token_payload = await self._request_json("POST", token_url, data=data)
        access_token = _nonblank_string(token_payload.get("access_token"))
        if any(ord(char) <= 32 or ord(char) >= 127 for char in access_token):
            raise _failed()
        profile = await self._request_json("GET", profile_url, access_token=access_token)
        return _kakao_identity(profile) if provider == "kakao" else _naver_identity(profile)

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        }
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            response = await self._client.request(
                method, url, data=data, headers=headers, follow_redirects=False,
            )
        except httpx.TransportError:
            raise _unavailable() from None
        except httpx.HTTPError:
            raise _failed() from None
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise _unavailable()
        if not 200 <= response.status_code < 300:
            raise _failed()
        try:
            payload = response.json()
        except ValueError:
            raise _failed() from None
        if not isinstance(payload, dict) or "error" in payload or "error_description" in payload:
            raise _failed()
        return payload
