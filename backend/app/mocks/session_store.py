from datetime import datetime, timedelta, timezone

from app.core.security import (
    SESSION_EXPIRE_MINUTES,
    create_session_id,
)

mock_sessions: dict[str, dict] = {}


def create_session(user_id: int) -> str:
    session_id = create_session_id()

    mock_sessions[session_id] = {
        "user_id": user_id,
        "expires_at": (
            datetime.now(timezone.utc)
            + timedelta(minutes=SESSION_EXPIRE_MINUTES)
        ),
    }

    return session_id


def get_session(
    session_id: str,
) -> dict | None:
    session = mock_sessions.get(session_id)

    if session is None:
        return None

    if session["expires_at"] <= datetime.now(timezone.utc):
        mock_sessions.pop(session_id, None)
        return None

    return session


def delete_session(session_id: str) -> None:
    mock_sessions.pop(session_id, None)