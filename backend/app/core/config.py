from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


def _oauth_origin(value: str, setting_name: str) -> tuple[str, bool]:
    """Validate an origin without including its value in configuration errors."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if (
            not host
            or parsed.scheme not in {"http", "https"}
            or parsed.path not in {"", "/"}
            or any(char.isspace() for char in value)
            or any(char in value for char in ("?", "#", "\\"))
            or "@" in parsed.netloc
            or parsed.netloc.endswith(":")
            or port == 0
        ):
            raise ValueError
        try:
            loopback = ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if parsed.scheme == "http" and not loopback:
            raise ValueError
    except ValueError:
        raise ValueError(f"{setting_name} requires an origin; HTTP is allowed only on loopback") from None
    return value.removesuffix("/"), loopback


class Settings(BaseSettings):
    database_url: str = Field(repr=False)
    database_echo: bool = False
    redis_url: str | None = Field(default=None, repr=False)
    redis_key_prefix: str = Field(default="me_nu", min_length=1)
    redis_socket_timeout_seconds: float = Field(default=0.5, gt=0)
    redis_max_connections: int = Field(default=20, gt=0)
    session_backend: Literal["postgres", "redis"] = "postgres"
    frontend_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    enable_dev_api: bool = True
    enable_dev_auth_bypass: bool = False
    session_cookie_name: str = "menu_session"
    session_expire_minutes: int = Field(default=30, gt=0)
    session_cookie_secure: bool = False
    oauth_frontend_origin: str = ""
    oauth_public_api_origin: str = "http://localhost:8000"
    oauth_state_ttl_seconds: int = Field(default=600, gt=0, le=1800)
    kakao_oauth_enabled: bool = False
    naver_oauth_enabled: bool = False
    kakao_client_id: str | None = Field(default=None, repr=False)
    kakao_client_secret: SecretStr | None = Field(default=None, repr=False)
    naver_client_id: str | None = Field(default=None, repr=False)
    naver_client_secret: SecretStr | None = Field(default=None, repr=False)
    # 외부 서비스 키 (없으면 해당 기능만 꺼진다)
    tmdb_key: str | None = Field(default=None, repr=False)
    llm_key: str | None = Field(default=None, repr=False)
    # 활동 기록(user_event)의 출처. 실서비스 서버만 production으로 둔다
    event_data_source: Literal["production", "local_demo", "automated_test", "imported"] = "local_demo"

    @model_validator(mode="after")
    def validate_session_backend(self) -> "Settings":
        if self.session_backend == "redis" and not self.redis_url:
            raise ValueError("SESSION_BACKEND=redis requires REDIS_URL")
        return self

    @model_validator(mode="after")
    def validate_oauth(self) -> "Settings":
        enabled = [provider for provider in ("kakao", "naver") if getattr(self, f"{provider}_oauth_enabled")]
        if not enabled:
            return self
        missing = []
        for provider in enabled:
            client_id = getattr(self, f"{provider}_client_id")
            client_secret = getattr(self, f"{provider}_client_secret")
            if not client_id or not client_id.strip():
                missing.append(f"{provider.upper()}_CLIENT_ID")
            if client_secret is None or not client_secret.get_secret_value().strip():
                missing.append(f"{provider.upper()}_CLIENT_SECRET")
        if missing:
            raise ValueError("OAuth configuration requires: " + ", ".join(missing))
        self.oauth_public_api_origin, api_loopback = _oauth_origin(
            self.oauth_public_api_origin, "OAUTH_PUBLIC_API_ORIGIN"
        )
        frontend_loopback = True
        if self.oauth_frontend_origin:
            self.oauth_frontend_origin, frontend_loopback = _oauth_origin(
                self.oauth_frontend_origin, "OAUTH_FRONTEND_ORIGIN"
            )
            if self.oauth_frontend_origin not in self.cors_origins:
                raise ValueError("OAUTH_FRONTEND_ORIGIN must be included in FRONTEND_ORIGINS")
        if not (frontend_loopback and api_loopback) and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE is required for non-loopback OAuth origins")
        return self

    def oauth_callback_url(self, provider: Literal["kakao", "naver"]) -> str:
        if provider not in {"kakao", "naver"}:
            raise ValueError("Unsupported OAuth provider")
        return f"{self.oauth_public_api_origin.removesuffix('/')}/api/v1/auth/login/{provider}/callback"

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.frontend_origins.split(",")
            if origin.strip()
        ]

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )


settings = Settings()
