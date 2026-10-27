"""Two synthetic anime articles for api.test.me-nu.kr only.

The default is a read-only preview. No users, policies, subscriptions, or
reactions are created. Run this module only inside the verified test deployment:
python -m app.seeds.fe_integration --confirm-test-environment api.test.me-nu.kr \
    --expected-database me_nu [--apply]
"""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AgentRun, Content, ContentSource, ContentTag, Draft, Subtopic, Topic


TEST_HOST = "api.test.me-nu.kr"
TEST_ORIGIN = f"https://{TEST_HOST}"
TEST_REDIS_PREFIX = "me_nu_staging"
FIXTURE_PREFIX = "fe-integration:v1:"
SUBTOPIC_NAME = "FE 연동 테스트 (가상 애니메이션)"
SOURCE_NAME = "me;nu FE 연동 테스트 — 실제 기사 아님"
PUBLISHED_AT = datetime(2026, 10, 27, tzinfo=timezone.utc)
FIXTURES = (
    {
        "key": "anime-01",
        "title": "[FE 연동 테스트 1] 별빛 우체국의 첫 번째 편지",
        "summary": "가상의 애니메이션을 소재로 피드, 상세 본문, 북마크를 확인하는 고정 테스트 콘텐츠입니다.",
        "body": (
            "이 글은 FE API 연동 검증을 위해 AI가 작성한 고정 테스트 자료입니다. "
            "작품과 인물은 모두 가상이며 실제 작품의 기사나 리뷰가 아닙니다.\n\n"
            "가상의 애니메이션 ‘별빛 우체국’에서 주인공은 밤하늘의 편지를 배달합니다. "
            "첫 번째 장면은 조용한 우체국, 두 번째 장면은 작은 별의 집을 배경으로 합니다.\n\n"
            "이 콘텐츠를 저장한 뒤 저장함과 상세 화면을 다시 열어 같은 콘텐츠 ID와 저장 상태가 유지되는지 확인하세요."
        ),
    },
    {
        "key": "anime-02",
        "title": "[FE 연동 테스트 2] 구름 정원에서 만난 작은 로봇",
        "summary": "첫 번째 콘텐츠와 구분되는 제목과 본문으로 목록 이동과 북마크 취소를 확인하는 가상 자료입니다.",
        "body": (
            "이 글은 FE API 연동 검증을 위해 AI가 작성한 고정 테스트 자료입니다. "
            "외부 자료 수집이나 실제 Agent 실행 결과가 아니며, 등장하는 작품은 존재하지 않습니다.\n\n"
            "가상의 애니메이션 ‘구름 정원’에서는 작은 로봇이 정원에 떨어진 색을 모읍니다. "
            "서로 다른 색을 발견하는 장면을 통해 두 번째 콘텐츠의 본문과 문단 표시를 확인할 수 있습니다.\n\n"
            "이 콘텐츠를 저장하고 취소한 뒤 새로고침하여 저장함에서 사라지고 상세 화면의 저장 표시도 해제되는지 확인하세요."
        ),
    },
)


class FixtureError(ValueError):
    """Unsafe target or conflicting fixture; do not modify existing records."""


def validate_target(settings, *, confirmation: str, expected_database: str) -> None:
    """Check explicit deployment configuration before creating any DB engine.

    These checks identify the configured target, not the physical server. The
    operator must run inside the independently verified test deployment; a DB
    name alone is not evidence that a database is non-production.
    """
    if confirmation != TEST_HOST or settings.oauth_public_api_origin != TEST_ORIGIN:
        raise FixtureError("Only the explicitly confirmed api.test.me-nu.kr deployment is allowed.")
    if settings.redis_key_prefix != TEST_REDIS_PREFIX or settings.session_backend != "redis" or not settings.redis_url:
        raise FixtureError("The test Redis session configuration is required.")
    if (settings.enable_dev_auth_bypass or settings.enable_dev_api or not settings.session_cookie_secure
            or settings.session_cookie_name != "menu_staging_session"):
        raise FixtureError("Dev authentication and dev APIs must be disabled; secure cookies are required.")
    try:
        target = make_url(settings.database_url)
    except (TypeError, ValueError, SQLAlchemyError):
        raise FixtureError("Invalid database configuration.") from None
    if not expected_database or target.database != expected_database or target.get_backend_name() != "postgresql":
        raise FixtureError("The PostgreSQL database must exactly match --expected-database.")


def source_url(fixture: dict) -> str:
    # A reserved example.com URL is an identifier, not a claimed news source.
    return f"https://example.com/me-nu-fe-integration/{fixture['key']}"


def provenance(fixture: dict) -> dict:
    fingerprint = hashlib.sha256(json.dumps(fixture, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {
        "method": "synthetic_fe_integration_fixture", "fixture_key": fixture["key"],
        "fixture_sha256": fingerprint, "test_environment": TEST_HOST,
        "test_fixture": True, "preview": True, "agent_invoked": False,
    }


async def _existing_content(db, fixture, topic, subtopic):
    key = FIXTURE_PREFIX + fixture["key"]
    run = await db.scalar(select(AgentRun).where(AgentRun.queue_task_id == key))
    sources = list((await db.scalars(select(ContentSource).where(ContentSource.source_url == source_url(fixture)))).all())
    if run is None:
        if sources:
            raise FixtureError(f"Reserved source marker already belongs to another record: {fixture['key']}")
        return None
    draft = await db.scalar(select(Draft).where(Draft.agent_run_id == run.agent_run_id))
    content = None if draft is None else await db.scalar(select(Content).where(Content.source_draft_id == draft.draft_id))
    expected = provenance(fixture)
    if (
        topic is None or subtopic is None or content is None
        or run.topic_id != topic.topic_id or run.agent_code != "fe-integration-fixture"
        or run.input_payload != expected or run.node_count != 0 or run.status != "succeeded"
        or draft.topic_id != topic.topic_id or draft.status != "published"
        or draft.title != fixture["title"] or draft.body != fixture["body"]
        or draft.production_type != "ai" or content.topic_id != topic.topic_id
        or content.title != fixture["title"] or content.body != fixture["body"]
        or content.summary != fixture["summary"] or content.production_type != "ai"
        or content.content_type != "anime_review" or content.status != "active"
        or content.deleted_at is not None or content.ai_judgment_basis != expected
        or len(sources) != 1 or sources[0].content_id != content.content_id
        or sources[0].source_title != SOURCE_NAME
    ):
        raise FixtureError(f"Existing fixture was changed or is incomplete; refusing to overwrite: {fixture['key']}")
    tags = list((await db.scalars(select(ContentTag).where(ContentTag.content_id == content.content_id))).all())
    if len(tags) != 1 or tags[0].topic_id != topic.topic_id or tags[0].subtopic_id != subtopic.subtopic_id:
        raise FixtureError(f"Existing fixture tags differ: {fixture['key']}")
    return content


async def seed_fe_integration(db: AsyncSession, *, apply: bool = False) -> dict:
    """Plan or add exactly two fixtures. Caller owns the entire transaction."""
    topic = await db.scalar(select(Topic).where(Topic.topic_code == "anime"))
    if topic is not None and not topic.is_active:
        raise FixtureError("The anime Topic is inactive; refusing to change it.")
    subjects = [] if topic is None else list((await db.scalars(select(Subtopic).where(
        Subtopic.topic_id == topic.topic_id, Subtopic.subtopic_name == SUBTOPIC_NAME,
    ))).all())
    if len(subjects) > 1 or (subjects and subjects[0].parent_subtopic_id is not None):
        raise FixtureError("The test Subtopic is ambiguous; refusing to change it.")
    subtopic = subjects[0] if subjects else None
    keys = set((await db.scalars(select(AgentRun.queue_task_id).where(
        AgentRun.queue_task_id.startswith(FIXTURE_PREFIX),
    ))).all())
    if keys - {FIXTURE_PREFIX + fixture["key"] for fixture in FIXTURES}:
        raise FixtureError("Unexpected records use the reserved fixture namespace.")
    existing = {}
    for fixture in FIXTURES:
        content = await _existing_content(db, fixture, topic, subtopic)
        if content is not None:
            existing[fixture["key"]] = content.content_id
    report = {
        "mode": "apply" if apply else "dry_run", "fixture_count": len(FIXTURES),
        "new_contents": len(FIXTURES) - len(existing), "existing_contents": len(existing),
        "topic_id": topic.topic_id if topic else None,
        "subtopic_id": subtopic.subtopic_id if subtopic else None,
        "content_ids": existing,
    }
    if not apply:
        return report
    if topic is None:
        topic = Topic(topic_code="anime", topic_name="애니메이션", is_active=True, sort_order=3)
        db.add(topic)
        await db.flush()
    if subtopic is None:
        subtopic = Subtopic(topic_id=topic.topic_id, subtopic_name=SUBTOPIC_NAME, depth_level=1)
        db.add(subtopic)
        await db.flush()
    for fixture in FIXTURES:
        if fixture["key"] in existing:
            continue
        basis = provenance(fixture)
        # This records fixture ingestion, not a fabricated execution of an Agent.
        run = AgentRun(
            topic_id=topic.topic_id, agent_code="fe-integration-fixture", agent_version="1",
            queue_task_id=FIXTURE_PREFIX + fixture["key"], status="succeeded", node_count=0,
            input_payload=basis, output_payload={"agent_invoked": False, "test_fixture": True},
        )
        db.add(run)
        await db.flush()
        draft = Draft(
            agent_run_id=run.agent_run_id, topic_id=topic.topic_id, production_type="ai",
            content_type="anime_review", title=fixture["title"], body=fixture["body"],
            status="published", factcheck_result={"test_fixture": True, "real_world_claims": False},
        )
        db.add(draft)
        await db.flush()
        content = Content(
            source_draft_id=draft.draft_id, topic_id=topic.topic_id, production_type="ai",
            content_type="anime_review", title=fixture["title"], body=fixture["body"],
            summary=fixture["summary"], published_at=PUBLISHED_AT,
            status="active", judgment_status="needs_review", ai_judgment_basis=basis,
            is_sponsored=False,
        )
        db.add(content)
        await db.flush()
        db.add(ContentSource(content_id=content.content_id, source_url=source_url(fixture),
                             source_title=SOURCE_NAME, source_role="primary", citation_order=1))
        db.add(ContentTag(content_id=content.content_id, topic_id=topic.topic_id, subtopic_id=subtopic.subtopic_id))
        existing[fixture["key"]] = content.content_id
    await db.flush()
    report.update(topic_id=topic.topic_id, subtopic_id=subtopic.subtopic_id)
    return report


async def run(*, confirmation: str, expected_database: str, apply: bool, settings=None) -> dict:
    if settings is None:
        from app.core.config import settings
    validate_target(settings, confirmation=confirmation, expected_database=expected_database)
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db, db.begin():
            if apply:
                # Serialize this one seed namespace, including the empty-table case.
                await db.execute(text("SELECT pg_advisory_xact_lock(20261027, 2)"))
            else:
                await db.execute(text("SET TRANSACTION READ ONLY"))
            report = await seed_fe_integration(db, apply=apply)
        return report
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-test-environment", required=True, choices=[TEST_HOST])
    parser.add_argument("--expected-database", required=True, help="Verified test deployment database name; never a URL.")
    parser.add_argument("--apply", action="store_true", help="Commit both fixtures atomically; otherwise preview only.")
    args = parser.parse_args()
    try:
        task = run(confirmation=args.confirm_test_environment, expected_database=args.expected_database, apply=args.apply)
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                report = runner.run(task)
        else:
            report = asyncio.run(task)
    except FixtureError as exc:
        parser.exit(1, f"Fixture seed stopped: {exc}\n")
    except (ValueError, SQLAlchemyError):
        # Configuration/DB errors can contain connection details; do not print them.
        parser.exit(1, "Fixture seed stopped: configuration or database error; no credentials printed.\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
