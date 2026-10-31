import pytest

from app.services import feed_service


@pytest.fixture(autouse=True)
def _no_first_round_delay(monkeypatch):
    """The home feed waits up to about 3 s for a new account's first round; tests skip the real delay."""
    async def no_delay(seconds):
        return None

    monkeypatch.setattr(feed_service, "_sleep", no_delay)
