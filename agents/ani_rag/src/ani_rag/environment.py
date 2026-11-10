"""Explicit loading of this project's local runtime environment settings."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ENV_FILE = PROJECT_ROOT / ".env"


def load_project_environment() -> bool:
    """Load project-root .env without overriding values supplied by the process."""
    return load_dotenv(dotenv_path=PROJECT_ENV_FILE, override=False, encoding="utf-8")
