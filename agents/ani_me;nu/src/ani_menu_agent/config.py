"""환경변수와 실행 설정."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """로컬과 배포 환경에서 공통으로 사용하는 설정."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="local", validation_alias="ANI_MENU_ENV")
    log_level: str = Field(default="INFO", validation_alias="ANI_MENU_LOG_LEVEL")
    model_name: str = Field(default="gpt-6-luna", validation_alias="ANI_MENU_MODEL")
    max_revision: int = Field(default=3, ge=1, le=5, validation_alias="ANI_MENU_MAX_REVISION")
    region_policy: str = Field(default="kr_first", validation_alias="ANI_MENU_REGION_POLICY")
    database_backend: Literal["auto", "sqlite", "postgresql"] = Field(
        default="auto", validation_alias="ANI_MENU_DATABASE_BACKEND"
    )
    database_url: str | None = Field(default=None, validation_alias="ANI_MENU_DATABASE_URL")
    # Explicitly test-only/legacy adapter; production should use PostgreSQL.
    database_path: str | None = Field(default=None, validation_alias="ANI_MENU_DATABASE_PATH")
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "LLM_KEY"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
