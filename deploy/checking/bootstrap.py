"""Minimal Checking-only Topic/Policy seed. Dry-run by default; no user/session writes.

Run with the Checking container's DATABASE_URL and MENU_DEPLOYMENT_ENV, not a
development .env file. This standalone script needs SQLAlchemy and psycopg only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

DATABASE = "me_nu_checking"
REVISION = "f4b8c1d9a620"
POLICY_VERSION = "checking-20261028-v1"
POLICIES = (
    {"policy_type": "terms", "title": "[Checking 테스트] 서비스 이용약관", "is_required": True,
     "content": "팀 내부 Checking 환경의 가입·피드·저장 기능 검증을 위한 테스트 안내입니다. 이 환경은 정식 서비스가 아니며, 게시된 합성 콘텐츠는 실제 소식이나 에이전트 생성 결과가 아닙니다."},
    {"policy_type": "privacy", "title": "[Checking 테스트] 개인정보 수집 및 이용 안내", "is_required": True,
     "content": "Checking 가입 흐름 검증을 위해 소셜 로그인 식별 정보, 닉네임, 동의 기록 및 선택한 취향을 별도 테스트 DB에 저장합니다. 기존 개발·운영 계정이나 세션을 복사하지 않습니다. 생년월일은 이 흐름에서 수집하지 않습니다. 정식 서비스용 개인정보 안내가 아닙니다."},
    {"policy_type": "marketing", "title": "[Checking 테스트] 선택 동의 화면 검증", "is_required": False,
     "content": "선택 동의 저장 동작을 확인하기 위한 테스트 항목입니다. 실제 광고·마케팅 발송에 사용하지 않으며, 동의하지 않아도 Checking 화면 검증을 진행할 수 있습니다."},
)
PRIVATE_TABLES = ("user_account", "auth_session", "oauth_attempt", "consent_history", "tap", "saved_item")


class BootstrapError(ValueError):
    """Safe operational error, without connection credentials or personal data."""


def validate_target(database_url: str, expected_database: str, deployment: str) -> None:
    try:
        url = make_url(database_url)
    except (ValueError, TypeError, SQLAlchemyError):
        raise BootstrapError("A valid Checking DATABASE_URL is required.") from None
    if (expected_database != DATABASE or deployment != "checking"
            or url.drivername != "postgresql+psycopg" or url.database != DATABASE
            or url.username != DATABASE or not url.host or not url.password or url.query):
        raise BootstrapError("Only the dedicated me_nu_checking PostgreSQL user/database in Checking is allowed.")


def build_plan(connection, now: datetime) -> dict[str, Any]:
    topic = connection.execute(text(
        "SELECT topic_id, is_active FROM topic WHERE topic_code='anime'"
    )).mappings().one_or_none()
    if topic is not None and not topic["is_active"]:
        raise BootstrapError("The existing anime Topic is inactive; it will not be changed.")
    missing = []
    preserved = []
    for policy in POLICIES:
        own = connection.execute(text(
            "SELECT title, content, is_required, is_active, effective_at<=:now AS effective FROM policy "
            "WHERE policy_type=:kind AND policy_version=:version"
        ), {"kind": policy["policy_type"], "version": POLICY_VERSION, "now": now}).mappings().one_or_none()
        if own is not None:
            if (not own["is_active"] or not own["effective"]
                    or any(own[key] != policy[key] for key in ("title", "content", "is_required"))):
                raise BootstrapError("The Checking policy version already exists with different or inactive content.")
            preserved.append(policy["policy_type"])
            continue
        active = connection.execute(text(
            "SELECT policy_id, effective_at FROM policy WHERE policy_type=:kind AND is_active=true "
            "ORDER BY effective_at DESC, policy_id DESC"
        ), {"kind": policy["policy_type"]}).mappings().all()
        if active:
            # Preserve existing active policy versions; never create a newer test policy over them.
            effective = connection.scalar(text(
                "SELECT count(*) FROM policy WHERE policy_type=:kind AND is_active=true AND effective_at<=:now"
            ), {"kind": policy["policy_type"], "now": now})
            if not effective:
                raise BootstrapError("A policy is scheduled for the future; resolve it explicitly before bootstrap.")
            preserved.append(policy["policy_type"])
        else:
            missing.append(policy)
    plan = {"new_topic": topic is None, "new_policies": missing, "preserved_policies": preserved}
    if plan["new_topic"] or missing:
        # These fixed table names are never supplied by a caller. Read existence only, not identities.
        if any(connection.scalar(text(f"SELECT EXISTS (SELECT 1 FROM {table})")) for table in PRIVATE_TABLES):
            raise BootstrapError("Incomplete bootstrap in an already-used database requires explicit reconciliation.")
    return plan


def apply_plan(connection, plan: dict[str, Any], now: datetime) -> None:
    if plan["new_topic"]:
        connection.execute(text(
            "INSERT INTO topic (topic_code, topic_name, description, sort_order, is_active) "
            "VALUES ('anime', '애니메이션', 'Checking 온보딩과 피드 검증용 관심 분야', 0, true)"
        ))
    for policy in plan["new_policies"]:
        connection.execute(text(
            "INSERT INTO policy (policy_type, policy_version, title, content, is_required, is_active, effective_at) "
            "VALUES (:policy_type, :policy_version, :title, :content, :is_required, true, :effective_at)"
        ), {**policy, "policy_version": POLICY_VERSION, "effective_at": now})


def run(*, database_url: str, expected_database: str, deployment: str, apply: bool = False) -> dict:
    validate_target(database_url, expected_database, deployment)
    engine = create_engine(database_url, echo=False, connect_args={"connect_timeout": 10})
    try:
        with engine.connect() as connection, connection.begin():
            if apply:
                connection.execute(text("SELECT pg_advisory_xact_lock(20261028, 3)"))
            else:
                connection.execute(text("SET TRANSACTION READ ONLY"))
            if (connection.scalar(text("SELECT current_database()")) != DATABASE
                    or connection.scalar(text("SELECT current_user")) != DATABASE):
                raise BootstrapError("Connected database/user differs from the dedicated Checking target.")
            revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            if revisions != [REVISION]:
                raise BootstrapError("Checking bootstrap requires migration f4b8c1d9a620 exactly.")
            now = datetime.now(timezone.utc)
            plan = build_plan(connection, now)
            if apply:
                apply_plan(connection, plan, now)
            return {"database": DATABASE, "mode": "apply" if apply else "dry_run",
                    "createdTopics": int(plan["new_topic"]), "createdPolicies": len(plan["new_policies"]),
                    "preservedPolicyTypes": plan["preserved_policies"], "userOrSessionWrites": 0}
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True, choices=[DATABASE])
    parser.add_argument("--apply", action="store_true", help="Commit the complete seed transaction; default is read-only.")
    args = parser.parse_args()
    try:
        report = run(database_url=os.environ.get("DATABASE_URL", ""), expected_database=args.expected_database,
                     deployment=os.environ.get("MENU_DEPLOYMENT_ENV", ""), apply=args.apply)
    except BootstrapError as exc:
        parser.exit(1, f"Checking bootstrap stopped: {exc}\n")
    except SQLAlchemyError:
        parser.exit(1, "Checking bootstrap failed; no changes committed. Check the dedicated DB and migration.\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
