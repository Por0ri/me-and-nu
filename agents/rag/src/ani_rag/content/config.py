"""콘텐츠 생성 설정 — agents/rag/.env 를 읽는다 (OPENAI_API_KEY)."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ani_rag.config import PACKAGE_ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PACKAGE_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model_name: str = Field(default="gpt-6-luna", validation_alias="ANI_MENU_MODEL")
    visual_layout_model: str = Field(
        default="gpt-6-luna", validation_alias="ANI_MENU_VISUAL_LAYOUT_MODEL"
    )
    visual_layout_enabled: bool = Field(
        default=True, validation_alias="ANI_MENU_VISUAL_LAYOUT_ENABLED"
    )
    max_revision: int = Field(default=3, ge=1, le=5, validation_alias="ANI_MENU_MAX_REVISION")
    database_backend: Literal["auto", "sqlite", "postgresql"] = Field(
        default="sqlite", validation_alias="ANI_MENU_DATABASE_BACKEND"
    )
    database_url: str | None = Field(default=None, validation_alias="ANI_MENU_DATABASE_URL")
    database_path: str | None = Field(
        default=str(PACKAGE_ROOT / ".cache" / "content" / "agent.sqlite3"),
        validation_alias="ANI_MENU_DATABASE_PATH",
    )
    duplicate_baseline_path: str | None = Field(
        default=None, validation_alias="ANI_MENU_DUPLICATE_BASELINE_PATH"
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "LLM_KEY"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
