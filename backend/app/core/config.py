from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    database_url: str
    frontend_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    enable_dev_api: bool = True
    enable_dev_auth_bypass: bool = False
    session_cookie_name: str = "menu_session"
    session_expire_minutes: int = 30
    session_cookie_secure: bool = False

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
