import pytest

from app.core.config import settings
from app.services import feed_service


@pytest.fixture(autouse=True)
def _debate_off_unless_enabled(monkeypatch):
    """토론방 스위치는 개발자 .env(로컬은 켜 둘 수 있음)와 상관없이 꺼진 상태로 시작한다. 토론 테스트만 직접 켠다."""
    monkeypatch.setattr(settings, "debate_enabled", False)


@pytest.fixture(autouse=True)
def _no_first_round_delay(monkeypatch):
    """The home feed waits up to about 3 s for a new account's first round; tests skip the real delay."""
    async def no_delay(seconds):
        return None

    monkeypatch.setattr(feed_service, "_sleep", no_delay)
