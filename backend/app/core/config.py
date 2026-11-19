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
    menu_deployment_env: str = ""
    checking_carousel_test_preview_enabled: bool = False
    database_echo: bool = False
    # Unset values keep SQLAlchemy's existing defaults for each deployment.
    database_pool_size: int | None = Field(default=None, gt=0)
    database_max_overflow: int | None = Field(default=None, ge=0)
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
    # 정보사전(RAG 사전 DB) 읽기 전용 주소. 없으면 챗봇은 우리 글로만 답한다
    dict_database_url: str | None = Field(default=None, repr=False)
    # 한 사람이 하루(한국 시각)에 보낼 수 있는 챗봇 질문 수. 0 이면 제한 없음
    chat_daily_question_limit: int = Field(default=30, ge=0)
    # 모든 사용자를 합친 하루 챗봇 질문 수. 계정을 여러 개 쓰는 경우까지 막는 마지막 상한. 0 이면 제한 없음.
    # 서버는 배포 설정 파일 한 줄(CHECKING_ · MAIN_CHAT_DAILY_TOTAL_LIMIT)로 정한다. 기본값은 값이 빠졌을 때의 안전한 수
    chat_daily_total_limit: int = Field(default=100, ge=0)
    # 활동 기록(user_event)의 출처. 실서비스 서버만 production으로 둔다
    event_data_source: Literal["production", "local_demo", "automated_test", "imported"] = "local_demo"
    # 토론방 기능 스위치. 꺼져 있으면 토론 API를 등록하지 않는다 (docs/DEBATE_BACKEND_DESIGN_2026-11-19.md).
    # 켜기 전에 그 환경 DB에 토론 migration을 먼저 적용한다. 같은 DB를 쓰는 서버 중 한 곳에서만 켠다.
    debate_enabled: bool = False
    # 토론방 규칙 값. Checking 시험 때는 줄여서 빠르게 확인할 수 있다 (예: 2표, 방 30분)
    debate_vote_threshold: int = Field(default=10, gt=0)
    debate_voting_period_minutes: int = Field(default=7 * 24 * 60, gt=0)
    debate_room_duration_minutes: int = Field(default=48 * 60, gt=0)
    debate_vote_ticket_max: int = Field(default=3, gt=0, le=100)
    debate_vote_ticket_refill_minutes: int = Field(default=60, gt=0)
    # 한 사람이 동시에 투표 중으로 둘 수 있는 내 주제 수
    debate_active_per_user: int = Field(default=1, gt=0)
    # 1인 채팅 속도 제한: window 초 동안 count 개까지
    debate_message_rate_count: int = Field(default=5, gt=0)
    debate_message_rate_window_seconds: int = Field(default=10, gt=0)
    # 1인 동시 실시간 연결 수 (탭 여러 개·기기 여러 대)와 연결 유지 신호 간격
    debate_stream_per_user: int = Field(default=3, gt=0)
    debate_stream_heartbeat_seconds: int = Field(default=20, gt=0)

    @model_validator(mode="after")
    def validate_session_backend(self) -> "Settings":
        if self.session_backend == "redis" and not self.redis_url:
            raise ValueError("SESSION_BACKEND=redis requires REDIS_URL")
        return self

    @model_validator(mode="after")
    def validate_checking_carousel_preview(self) -> "Settings":
        from app.core.carousel_preview import checking_preview_database

        if self.checking_carousel_test_preview_enabled and checking_preview_database(self) is None:
            raise ValueError("Checking carousel preview requires the isolated Checking database, origins and secure authenticated runtime")
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
