import pytest

from ani_menu_agent.config import get_settings


@pytest.fixture(autouse=True)
def isolate_agent_database(monkeypatch, tmp_path):
    """Use isolated SQLite only as a fast test adapter; never touch PostgreSQL data."""
    monkeypatch.setenv("ANI_MENU_DATABASE_URL", "")
    monkeypatch.setenv("ANI_MENU_DATABASE_PATH", str(tmp_path / "agent-test.sqlite3"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
