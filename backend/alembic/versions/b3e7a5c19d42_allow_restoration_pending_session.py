"""allow the restoration_pending login session (API-010)

Revision ID: b3e7a5c19d42
Revises: 6d2f8b4a1c37
Create Date: 2026-11-09

A member inside the withdrawal grace period signs in with this restricted
session state. No table or column changes. Downgrade removes those short-lived
sessions first because the previous rule rejects them.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "b3e7a5c19d42"
down_revision: str | Sequence[str] | None = "6d2f8b4a1c37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_auth_session_state", "auth_session", type_="check")
    op.create_check_constraint(
        "ck_auth_session_state",
        "auth_session",
        "session_state IN ('anonymous', 'onboarding_pending', 'restoration_pending', 'active')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM auth_session WHERE session_state = 'restoration_pending'")
    op.drop_constraint("ck_auth_session_state", "auth_session", type_="check")
    op.create_check_constraint(
        "ck_auth_session_state",
        "auth_session",
        "session_state IN ('anonymous', 'onboarding_pending', 'active')",
    )
