"""Opt-in synthetic labs fixtures; never creates users, consents, or sessions."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PREFIX = "menu-labs-fixture-v1:"
SUBTOPIC = "Labs 통합 테스트 (가상 콘텐츠)"
PUBLISHED_AT = datetime(2026, 10, 27, tzinfo=timezone.utc)
POLICIES = tuple({
    "policy_type": kind, "policy_version": "labs-v1", "title": f"{title} (Labs 테스트 전용)",
    "content": "비공개 Labs 환경의 동의 API 검증용 임시 문안입니다. 실제 서비스 약관이나 개인정보 처리방침이 아닙니다. 운영 서비스에 사용하지 않습니다.",
    "is_required": kind in ("terms", "privacy"), "is_active": True,
} for kind, title in (("terms", "서비스 이용약관"), ("privacy", "개인정보 안내"),
                     ("advertising", "광고성 정보 수신 동의"), ("marketing", "마케팅 정보 수신 동의")))
FIXTURES = (
    {"key": "anime-body", "title": "[Labs 가상 자료] 별빛 우체국의 첫 번째 편지",
     "summary": "본문 표시와 북마크 저장을 확인하는 고정 테스트 자료입니다. 실제 작품이나 기사가 아닙니다.",
     "body": "이 글은 Labs 통합 검증을 위해 직접 작성한 고정 테스트 자료입니다. 실제 작품이나 기사가 아닙니다.\n\n가상의 별빛 우체국에서 주인공이 첫 번째 편지를 받는 장면을 상상합니다. 본문 표시, 뒤로 가기, 저장 목록의 일관성을 확인하세요.",
     "production_type": "ai", "content_type": "anime_review"},
    {"key": "anime-link", "title": "[Labs 가상 자료] 구름 극장의 상영 안내",
     "summary": "원문 링크형 상세와 이미지 없는 카드를 확인하는 고정 테스트 자료입니다. 실제 상영 정보가 아닙니다.",
     "body": None, "production_type": "human", "content_type": "article"},
)


class FixtureError(ValueError):
    pass


def source_url(fixture):
    return f"https://example.com/menu-labs-fixtures/{fixture['key']}"


def provenance(fixture):
    digest = hashlib.sha256(json.dumps(fixture, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"fixture_key": PREFIX + fixture["key"], "fixture_sha256": digest,
            "test_environment": "labs", "test_fixture": True, "preview": True, "agent_invoked": False}


def validate_target(settings, environment):
    checks = {
        "oauth_public_api_origin": "https://api.labs.me-nu.kr", "oauth_frontend_origin": "https://labs.me-nu.kr",
        "frontend_origins": "https://labs.me-nu.kr,https://api.labs.me-nu.kr",
        "redis_key_prefix": "me_nu_labs", "session_backend": "redis", "session_cookie_name": "menu_labs_session",
        "enable_dev_auth_bypass": False, "enable_dev_api": False, "session_cookie_secure": True,
    }
    if environment.get("MENU_DEPLOYMENT_ENV") != "labs" or any(getattr(settings, key, None) != expected for key, expected in checks.items()):
        raise FixtureError("Seed is restricted to the isolated labs origins, secure cookie, and Redis namespace.")
    try:
        database = make_url(settings.database_url)
        redis = make_url(settings.redis_url)
        valid = (database.drivername == "postgresql+psycopg" and database.host == "postgres"
                 and database.port == 5432 and database.database == "me_nu_labs" and database.username == "me_nu_labs"
                 and bool(database.password) and not database.query
                 and redis.drivername == "redis" and redis.host == "redis" and redis.port == 6379
                 and redis.database == "0" and not redis.username and bool(redis.password) and not redis.query)
    except (ArgumentError, TypeError, ValueError):
        valid = False
    if not valid:
        raise FixtureError("Seed requires the private labs PostgreSQL and Redis endpoints.")


async def seed_labs(session, *, apply=False):
    """Caller owns one transaction. Refuse changed fixtures; never overwrite rows."""
    # app.db package imports runtime Settings; defer model import until the target
    # guard has succeeded in run(). Tests provide their own in-memory transaction.
    from app.models import AgentRun, Content, ContentSource, ContentTag, Draft, Policy, Subtopic, Topic

    existing_policies = list(await session.scalars(select(Policy)))
    missing_policies = []
    for values in POLICIES:
        matches = [row for row in existing_policies if row.policy_type == values["policy_type"]]
        if matches and (len(matches) != 1 or any(getattr(matches[0], key) != value for key, value in values.items()) or matches[0].content_url is not None):
            raise FixtureError("An existing policy differs from the labs fixture; no changes applied.")
        if not matches:
            missing_policies.append(values)
    topic = await session.scalar(select(Topic).where(Topic.topic_code == "anime"))
    if topic and (not topic.is_active or topic.topic_name != "애니메이션"):
        raise FixtureError("Existing anime Topic differs; no changes applied.")
    subtopics = list(await session.scalars(select(Subtopic).where(Subtopic.topic_id == topic.topic_id, Subtopic.subtopic_name == SUBTOPIC))) if topic else []
    if len(subtopics) > 1 or (subtopics and (subtopics[0].parent_subtopic_id is not None or subtopics[0].depth_level != 1)):
        raise FixtureError("Existing labs Subtopic differs; no changes applied.")
    subtopic = subtopics[0] if subtopics else None
    # Labs data is intentionally small. A Python marker check also finds fixtures
    # whose source URL was removed, without relying on dialect-specific JSON SQL.
    contents = list(await session.scalars(select(Content)))
    missing = []
    content_ids = {}
    for fixture in FIXTURES:
        sources = list(await session.scalars(select(ContentSource).where(ContentSource.source_url == source_url(fixture))))
        source_ids = {row.content_id for row in sources}
        candidates = [row for row in contents if row.content_id in source_ids or row.title == fixture["title"]
                      or (isinstance(row.ai_judgment_basis, dict) and row.ai_judgment_basis.get("fixture_key") == PREFIX + fixture["key"])]
        run = await session.scalar(select(AgentRun).where(AgentRun.queue_task_id == PREFIX + fixture["key"]))
        if not candidates:
            if sources or run:
                raise FixtureError("A labs source or run marker already exists without its matching content.")
            missing.append(fixture)
            continue
        if len(candidates) != 1 or len(sources) != 1 or not topic or not subtopic:
            raise FixtureError("A labs fixture marker collides with existing data.")
        content = candidates[0]
        expected = {key: fixture[key] for key in ("title", "summary", "body", "production_type", "content_type")}
        expected.update(topic_id=topic.topic_id, status="active", judgment_status="needs_review",
                        ai_judgment_basis=provenance(fixture), image_url=None, is_sponsored=False, deleted_at=None)
        if any(getattr(content, key) != value for key, value in expected.items()):
            raise FixtureError("An existing labs content was edited; no changes applied.")
        tags = list(await session.scalars(select(ContentTag).where(ContentTag.content_id == content.content_id)))
        if len(tags) != 1 or tags[0].topic_id != topic.topic_id or tags[0].subtopic_id != subtopic.subtopic_id or sources[0].content_id != content.content_id or sources[0].source_role != "primary":
            raise FixtureError("A labs source or tag was edited; no changes applied.")
        if fixture["production_type"] == "ai":
            draft = await session.get(Draft, content.source_draft_id) if content.source_draft_id else None
            if not run or not draft or draft.agent_run_id != run.agent_run_id or draft.body != fixture["body"] or run.input_payload != provenance(fixture) or run.node_count != 0:
                raise FixtureError("The synthetic full-body provenance was edited; no changes applied.")
        elif run or content.source_draft_id is not None:
            raise FixtureError("The link fixture has unexpected draft provenance.")
        content_ids[fixture["key"]] = content.content_id
    report = {"mode": "apply" if apply else "dry_run", "new_policies": len(missing_policies),
              "new_contents": len(missing), "content_ids": content_ids, "topic_id": topic.topic_id if topic else None}
    if not apply:
        return report
    for values in missing_policies:
        session.add(Policy(**values))
    if not topic:
        topic = Topic(topic_code="anime", topic_name="애니메이션", is_active=True, sort_order=3)
        session.add(topic)
        await session.flush()
    if not subtopic:
        subtopic = Subtopic(topic_id=topic.topic_id, subtopic_name=SUBTOPIC, depth_level=1)
        session.add(subtopic)
        await session.flush()
    for fixture in missing:
        draft_id = None
        if fixture["production_type"] == "ai":
            # Synthetic provenance is necessary for the full_body API contract.
            # The payload explicitly records that no agent/LLM was executed.
            run = AgentRun(topic_id=topic.topic_id, agent_code="labs-synthetic-fixture", agent_version="1",
                           queue_task_id=PREFIX + fixture["key"], status="succeeded", node_count=0,
                           input_payload=provenance(fixture), output_payload={"agent_invoked": False, "test_fixture": True})
            session.add(run)
            await session.flush()
            draft = Draft(agent_run_id=run.agent_run_id, topic_id=topic.topic_id, production_type="ai",
                          content_type=fixture["content_type"], title=fixture["title"], body=fixture["body"],
                          status="published", factcheck_result={"test_fixture": True, "real_world_claims": False})
            session.add(draft)
            await session.flush()
            draft_id = draft.draft_id
        content = Content(source_draft_id=draft_id, topic_id=topic.topic_id,
                          **{key: fixture[key] for key in ("title", "summary", "body", "production_type", "content_type")},
                          published_at=PUBLISHED_AT, status="active", judgment_status="needs_review",
                          ai_judgment_basis=provenance(fixture), is_sponsored=False)
        session.add(content)
        await session.flush()
        session.add(ContentSource(content_id=content.content_id, source_url=source_url(fixture),
                                  source_title="Labs 가상 테스트 자료 · 실제 기사 아님", source_role="primary", citation_order=1))
        session.add(ContentTag(content_id=content.content_id, topic_id=topic.topic_id, subtopic_id=subtopic.subtopic_id))
        await session.flush()
        content_ids[fixture["key"]] = content.content_id
    report["topic_id"] = topic.topic_id
    return report


async def run(*, apply, settings, environment):
    validate_target(settings, environment)
    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            if apply:
                await session.execute(text("SELECT pg_advisory_xact_lock(72161027)"))
            else:
                await session.execute(text("SET TRANSACTION READ ONLY"))
            return await seed_labs(session, apply=apply)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the reviewed fixture plan to the isolated labs DB")
    args = parser.parse_args()
    try:
        # Settings and DB engine are deliberately not imported at module load.
        from app.core.config import settings
        print(json.dumps(asyncio.run(run(apply=args.apply, settings=settings, environment=os.environ)), sort_keys=True))
    except FixtureError as error:
        parser.exit(1, f"Labs seed refused: {error}\n")
    except Exception:
        parser.exit(1, "Labs seed failed; transaction rolled back. Database/configuration details suppressed.\n")


if __name__ == "__main__":
    main()
