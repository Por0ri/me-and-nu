from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


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
    # 외부 서비스 키 (없으면 해당 기능만 꺼진다)
    tmdb_key: str | None = Field(default=None, repr=False)
    llm_key: str | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_session_backend(self) -> "Settings":
        if self.session_backend == "redis" and not self.redis_url:
            raise ValueError("SESSION_BACKEND=redis requires REDIS_URL")
        return self

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
    )


settings = Settings()
