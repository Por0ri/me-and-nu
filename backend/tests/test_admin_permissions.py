"""Real isolated SQL for role policy and audited manual grant management.

PostgreSQL contention and migration coverage live in the PG integration suite.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, insert, select, text, update
from sqlalchemy.orm import Session

from app.core.exceptions import ApiError
from app.models import AdminAccount, AdminActionLog, UserAccount
from app.services import admin_grant_service as grants
from app.services.admin_permission_provider import DatabaseAdminPermissionProvider
from app.services.admin_review_permission_provider import DatabaseAdminReviewPermissionProvider


class SQLSession:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    def get_bind(self):
        return self.session.get_bind()

    def in_transaction(self):
        return self.session.in_transaction()

    @asynccontextmanager
    async def begin(self):
        with self.session.begin():
            yield


@pytest.fixture
def permission_db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "permissions.sqlite"))
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys = ON")
    with engine.begin() as db:
        UserAccount.__table__.create(db)
        AdminAccount.__table__.create(db)
        AdminActionLog.__table__.create(db)
        db.execute(insert(UserAccount), [{"user_id": number, "nickname": f"user{number}",
            "auth_provider": "kakao", "provider_user_id": str(number)} for number in range(1, 9)])
    yield engine
    engine.dispose()


def call(engine, operation, **kwargs):
    with Session(engine) as session:
        return asyncio.run(operation(SQLSession(session), **kwargs))


def bootstrap(engine):
    return call(engine, grants.bootstrap_admin, target_user_id=1, rationale="Initial operator authorization")


def rows(engine, model):
    with engine.connect() as db:
        return list(db.execute(select(model.__table__)).mappings())


def seed_grant(engine, role, user_id=2, revoked=False):
    with engine.begin() as db:
        return db.scalar(insert(AdminAccount).values(user_id=user_id, role=role,
            revoked_at=datetime.now(timezone.utc) if revoked else None).returning(AdminAccount.admin_account_id))


@pytest.mark.parametrize("role,read,review", [
    ("viewer", True, False), ("operator", True, True), ("admin", True, True),
    ("reviewer", False, False), ("Admin", False, False), ("", False, False),
])
def test_fresh_database_role_policy(permission_db, role, read, review):
    seed_grant(permission_db, role)
    assert call(permission_db, DatabaseAdminPermissionProvider().can_read_contents, user_id=2) is read
    provider = DatabaseAdminReviewPermissionProvider()
    assert call(permission_db, provider.can_review_content, user_id=2, content_id=10) is review
    grant = call(permission_db, provider.lock_grant, user_id=2, content_id=10)
    assert (grant is not None) is review
    if grant is not None:
        assert grant.user_id == 2


@pytest.mark.parametrize("status", ["warned", "suspended", "banned", "withdrawn"])
def test_inactive_user_cannot_read_or_review(permission_db, status):
    seed_grant(permission_db, "admin")
    with permission_db.begin() as db:
        db.execute(update(UserAccount).where(UserAccount.user_id == 2).values(account_status=status))
    assert not call(permission_db, DatabaseAdminPermissionProvider().can_read_contents, user_id=2)
    assert not call(permission_db, DatabaseAdminReviewPermissionProvider().can_review_content, user_id=2, content_id=10)
    assert call(permission_db, DatabaseAdminReviewPermissionProvider().lock_grant, user_id=2, content_id=10) is None


def test_revocation_is_not_cached(permission_db):
    grant_id = seed_grant(permission_db, "operator")
    provider = DatabaseAdminReviewPermissionProvider()
    assert call(permission_db, provider.can_review_content, user_id=2, content_id=10)
    with permission_db.begin() as db:
        db.execute(update(AdminAccount).where(AdminAccount.admin_account_id == grant_id).values(revoked_at=datetime.now(timezone.utc)))
    assert not call(permission_db, provider.can_review_content, user_id=2, content_id=10)
    assert call(permission_db, provider.lock_grant, user_id=2, content_id=10) is None
    assert not call(permission_db, DatabaseAdminPermissionProvider().can_read_contents, user_id=2)


@pytest.mark.parametrize("user_id", [True, 0, -1, "2", 9223372036854775808])
def test_invalid_identity_fails_closed(permission_db, user_id):
    seed_grant(permission_db, "admin")
    assert not call(permission_db, DatabaseAdminPermissionProvider().can_read_contents, user_id=user_id)
    assert call(permission_db, DatabaseAdminReviewPermissionProvider().lock_grant, user_id=user_id, content_id=10) is None


def test_bootstrap_self_audits_and_cannot_repeat(permission_db):
    result = bootstrap(permission_db)
    account = rows(permission_db, AdminAccount)[0]
    audit = rows(permission_db, AdminActionLog)[0]
    assert result["role"] == "admin"
    assert audit["admin_account_id"] == account["admin_account_id"] == audit["target_id"]
    assert audit["action_type"] == "admin.bootstrap"
    assert audit["after_value"]["user_id"] == 1
    with pytest.raises(ApiError) as error:
        call(permission_db, grants.bootstrap_admin, target_user_id=2, rationale="retry")
    assert error.value.code == "ADMIN_BOOTSTRAP_CLOSED"
    assert len(rows(permission_db, AdminAccount)) == len(rows(permission_db, AdminActionLog)) == 1


def test_revoked_history_keeps_bootstrap_closed(permission_db):
    seed_grant(permission_db, "admin", revoked=True)
    with pytest.raises(ApiError) as error:
        bootstrap(permission_db)
    assert error.value.code == "ADMIN_BOOTSTRAP_CLOSED"


@pytest.mark.parametrize("role", ["viewer", "operator", "unknown"])
def test_only_admin_can_grant(permission_db, role):
    seed_grant(permission_db, role, user_id=1)
    with pytest.raises(ApiError) as error:
        call(permission_db, grants.grant_admin, actor_user_id=1, target_user_id=2,
             role="operator", rationale="Requested grant")
    assert error.value.code == "ADMIN_MANAGE_REQUIRED"
    assert len(rows(permission_db, AdminAccount)) == 1
    assert rows(permission_db, AdminActionLog) == []


def test_grant_revoke_regrant_retains_history_and_audit(permission_db):
    owner = bootstrap(permission_db)
    first = call(permission_db, grants.grant_admin, actor_user_id=1, target_user_id=2,
                 role="operator", rationale="Content reviews")
    with pytest.raises(ApiError) as error:
        call(permission_db, grants.grant_admin, actor_user_id=1, target_user_id=2,
             role="viewer", rationale="Role change")
    assert error.value.code == "ADMIN_GRANT_EXISTS"
    result = call(permission_db, grants.revoke_admin, actor_user_id=1,
                  admin_account_id=first["adminAccountId"], rationale="Rotation")
    assert result["revokedAt"]
    second = call(permission_db, grants.grant_admin, actor_user_id=1, target_user_id=2,
                  role="viewer", rationale="Read only")
    assert second["adminAccountId"] != first["adminAccountId"]
    history = call(permission_db, grants.list_grants, actor_user_id=1, target_user_id=2)
    assert len(history) == 2 and history[0]["revoked_at"] is not None and history[1]["revoked_at"] is None
    audit = rows(permission_db, AdminActionLog)
    assert [row["action_type"] for row in audit] == ["admin.bootstrap", "admin.grant", "admin.revoke", "admin.grant"]
    assert all(row["admin_account_id"] == owner["adminAccountId"] for row in audit)


def test_last_manager_cannot_be_revoked(permission_db):
    owner = bootstrap(permission_db)
    with pytest.raises(ApiError) as error:
        call(permission_db, grants.revoke_admin, actor_user_id=1,
             admin_account_id=owner["adminAccountId"], rationale="Leave")
    assert error.value.code == "ADMIN_LAST_MANAGER"
    second = call(permission_db, grants.grant_admin, actor_user_id=1, target_user_id=2,
                  role="admin", rationale="Replacement")
    call(permission_db, grants.revoke_admin, actor_user_id=2,
         admin_account_id=owner["adminAccountId"], rationale="Handover")
    with pytest.raises(ApiError) as error:
        call(permission_db, grants.grant_admin, actor_user_id=1, target_user_id=3,
             role="viewer", rationale="Revoked actor")
    assert error.value.code == "ADMIN_MANAGE_REQUIRED"
    assert second["adminAccountId"] > 0


def test_audit_failure_rolls_back_grant(permission_db):
    bootstrap(permission_db)
    with permission_db.begin() as db:
        AdminActionLog.__table__.drop(db)
    with pytest.raises(ApiError) as error:
        call(permission_db, grants.grant_admin, actor_user_id=1, target_user_id=2,
             role="operator", rationale="Should rollback")
    assert error.value.code == "ADMIN_GRANT_UNAVAILABLE"
    assert len(rows(permission_db, AdminAccount)) == 1


def test_inactive_target_and_unknown_role_never_gain_permission(permission_db):
    bootstrap(permission_db)
    with permission_db.begin() as db:
        db.execute(update(UserAccount).where(UserAccount.user_id == 2).values(account_status="withdrawn"))
    with pytest.raises(ApiError) as error:
        call(permission_db, grants.grant_admin, actor_user_id=1, target_user_id=2,
             role="viewer", rationale="Inactive")
    assert error.value.code == "ADMIN_TARGET_INACTIVE"
    with pytest.raises(ValueError):
        call(permission_db, grants.grant_admin, actor_user_id=1, target_user_id=3,
             role="superuser", rationale="Unknown role")
    assert len(rows(permission_db, AdminAccount)) == 1


def test_cli_preview_does_not_need_a_database(monkeypatch, capsys):
    path = Path(__file__).parents[1] / "scripts" / "manage_admin_grants.py"
    spec = importlib.util.spec_from_file_location("manage_admin_grants", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("ADMIN_DATABASE_URL", raising=False)
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    assert module.main(["bootstrap", "--database-name", "isolated_admin_test", "--target-user-id", "1",
                        "--rationale", "preview", "--confirm-first-admin"]) == 0
    assert '"applied": false' in capsys.readouterr().out
    assert module.main(["bootstrap", "--database-name", "isolated_admin_test", "--target-user-id", "1",
                        "--rationale", "preview", "--confirm-first-admin", "--apply"]) == 1
    assert "INVALID_ADMIN_COMMAND" in capsys.readouterr().err


@pytest.mark.parametrize("query", ["dbname=elsewhere", "service=other", "host=example.invalid", "options=-csearch_path=other"])
def test_cli_rejects_target_override_before_connecting(monkeypatch, query):
    path = Path(__file__).parents[1] / "scripts" / "manage_admin_grants.py"
    spec = importlib.util.spec_from_file_location("manage_admin_grants_guard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_DATABASE_URL", "postgresql+psycopg://test:test@127.0.0.1:1/intended?" + query)
    args = module.parser().parse_args(["list", "--actor-user-id", "1", "--database-name", "intended"])
    with pytest.raises(ValueError, match="overridden"):
        asyncio.run(module.execute(args))


@pytest.mark.parametrize("variable", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_cli_rejects_inherited_libpq_routing_without_printing_values(monkeypatch, capsys, variable):
    path = Path(__file__).parents[1] / "scripts" / "manage_admin_grants.py"
    spec = importlib.util.spec_from_file_location("manage_admin_grants_environment_guard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_DATABASE_URL", "postgresql+psycopg://test:test@127.0.0.1:1/intended")
    secret_value = "routing-value-must-not-be-printed"
    monkeypatch.setenv(variable, secret_value)
    assert module.main(["list", "--actor-user-id", "1", "--database-name", "intended"]) == 1
    captured = capsys.readouterr()
    assert "AMBIENT_DATABASE_ROUTING" in captured.err
    assert variable in captured.err
    assert secret_value not in captured.out + captured.err
