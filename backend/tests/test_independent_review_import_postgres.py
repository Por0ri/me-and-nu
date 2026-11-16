"""Independent producer review is evidence, never an operator publication approval.

The default bundle is synthetic and needs no network or model. Set
INDEPENDENT_REVIEW_TEST_BUNDLE to replay an existing export without changing its
package, dates, or PNGs. The PostgreSQL scenario reuses the loopback-only,
random-schema carousel_test fixture; the SQLite scenario also runs in normal CI.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from test_carousel_import import AsyncAdapter, engine, make_raw_bundle  # noqa: F401 -- shared isolated fixture
from test_carousel_postgres import (
    _authenticated_feed_cards,
    _run_async,
    carousel_pg,  # noqa: F401 -- guarded disposable PostgreSQL fixture
)


@pytest.fixture
def independently_reviewed_bundle(tmp_path):
    from app.seeds.carousel_contents import load_bundle

    archive = os.environ.get("INDEPENDENT_REVIEW_TEST_BUNDLE")
    if archive:
        bundle_file = Path(archive).resolve(strict=True)
    else:
        raw = make_raw_bundle(tmp_path, pages=4)
        now = datetime.now(timezone.utc)
        raw["items"][0]["package"].update(
            request_id="independent-review-fixture",
            content_type="goods",
            menu_work_ids=[8],
            feed_valid_until=(now + timedelta(days=2)).isoformat(),
            purge_at=(now + timedelta(days=32)).isoformat(),
        )
        raw["items"][0]["reviewEvidence"] = {
            "review_mode": "llm",
            "decision": "review_passed",
            "independentReview": {"decision": "approved_for_feed", "hard_blockers": []},
            "originalGeneration": {"outcome": "rejected", "historyPreserved": True},
            "operatorReviewCreated": False,
            "fixtureOnly": True,
        }
        bundle_file = tmp_path / "bundle.json"
        bundle_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    # Validate actual input at the real current time; do not extend a deadline
    # or rewrite rejected history merely to make a replay pass.
    original_hash = hashlib.sha256(bundle_file.read_bytes()).hexdigest()
    pending = load_bundle(bundle_file)
    item, = pending.items
    assert item.raw.reviewEvidence
    assert pending.reviews == {}
    asset_hashes = {
        (bundle_file.parent / asset.path).resolve(): asset.sha256
        for asset in item.raw.assets
    }
    review_file = tmp_path / "operator-review.json"
    record = {
        "digest": item.digest,
        "decision": "approved",
        "reviewedBy": "isolated-import-regression",
        "reviewedAt": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "comment": "Isolated regression approval only; no service publication.",
    }
    review_file.write_text(
        json.dumps({"schemaVersion": 1, "records": [record]}), encoding="utf-8"
    )
    approved = load_bundle(bundle_file, review_record_file=review_file)
    assert approved.items[0].digest == item.digest
    assert approved.items[0].raw.package == item.raw.package
    assert approved.items[0].raw.reviewEvidence == item.raw.reviewEvidence
    yield pending, approved, bool(archive)
    assert hashlib.sha256(bundle_file.read_bytes()).hexdigest() == original_hash
    for path, expected in asset_hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_independent_review_does_not_publish_without_operator_approval(
    engine, independently_reviewed_bundle
):
    from app.models import AgentRun, Content, Draft
    from app.seeds.carousel_contents import import_bundle

    pending, approved, _ = independently_reviewed_bundle

    async def scenario():
        with Session(engine) as db, db.begin():
            first = await import_bundle(AsyncAdapter(db), pending, apply=True)
            duplicate = await import_bundle(AsyncAdapter(db), pending, apply=True)
            assert (first["created"], duplicate["created"], duplicate["unchanged"]) == (1, 0, 1)
            content = db.scalars(select(Content)).one()
            draft = db.scalars(select(Draft)).one()
            run = db.scalars(select(AgentRun)).one()
            assert (content.status, content.judgment_status, draft.status) == (
                "pending_review", "needs_review", "checking"
            )
            assert content.published_at is None
            assert content.ai_judgment_basis["operator_review"] is None
            assert content.ai_judgment_basis["producer_review_is_approval"] is False
            evidence = pending.items[0].raw.reviewEvidence
            assert run.output_payload["producer_review_evidence"] == evidence
            promoted = await import_bundle(AsyncAdapter(db), approved, apply=True)
            replayed = await import_bundle(AsyncAdapter(db), approved, apply=True)
            assert (promoted["review_updated"], replayed["unchanged"]) == (1, 1)
            assert (content.status, content.judgment_status, draft.status) == (
                "active", "confirmed", "published"
            )
            assert run.output_payload["producer_review_evidence"] == evidence
            for model in (Content, Draft, AgentRun):
                assert db.scalar(select(func.count()).select_from(model)) == 1

    _run_async(scenario())


def test_independent_review_import_and_operator_approval_in_authenticated_feed(
    carousel_pg, independently_reviewed_bundle, monkeypatch
):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.deps import require_onboarding
    from app.core.config import settings
    from app.core.security import csrf_token_for_session, hash_token
    from app.db.session import get_db
    from app.main import create_app
    from app.models import (
        AgentRun, AuthSession, Content, ContentSlide, Draft, Entity, EntityExternalId,
        RecommendationItem, RecommendationRound, Tap, Topic, UserAccount,
    )
    from app.seeds.carousel_contents import import_bundle, run

    pending, approved, actual_bundle = independently_reviewed_bundle
    prepared, = pending.items
    expected_pages = sorted(prepared.images)
    expected_source = next(source.url for source in prepared.package.sources if source.role == "primary")
    engine, url, migrate = carousel_pg
    migrate("head")
    for key, value in {
        "session_backend": "postgres", "enable_dev_auth_bypass": False,
        "session_cookie_secure": False, "redis_url": None,
        "checking_carousel_test_preview_enabled": False,
    }.items():
        monkeypatch.setattr(settings, key, value)
    now = datetime.now(timezone.utc)
    token = "independent-review-test-" + uuid4().hex
    with Session(engine) as db, db.begin():
        db.add_all([
            Topic(topic_id=1, topic_code="anime", topic_name="애니", is_active=True),
            Topic(topic_id=2, topic_code="other", topic_name="다른 토픽", is_active=True),
        ])
        user = UserAccount(
            nickname="격리 검수 테스트", auth_provider="kakao", provider_user_id=uuid4().hex,
            onboarding_completed_at=now - timedelta(days=2),
        )
        db.add(user)
        db.flush()
        for work_id in prepared.package.menu_work_ids:
            work = Entity(name=f"격리 작품 {work_id}", topic_id=1)
            db.add(work)
            db.flush()
            db.add(EntityExternalId(provider="menu_work", external_id=str(work_id), entity_id=work.entity_id))
        db.add(Tap(user_id=user.user_id, topic_id=1))
        db.add(AuthSession(
            user_id=user.user_id, session_state="active", session_token_hash=hash_token(token),
            csrf_token_hash=hash_token(csrf_token_for_session(token)), created_at=now,
            last_accessed_at=now, expires_at=now + timedelta(hours=1),
        ))

    async def apply(bundle):
        return await run(
            bundle=bundle, apply=True, expected_database="carousel_test",
            database_url=url.render_as_string(hide_password=False),
        )

    async def scenario():
        async_engine = create_async_engine(url, poolclass=NullPool)
        factory = async_sessionmaker(async_engine, expire_on_commit=False)

        async def test_db():
            async with factory() as db:
                yield db

        async def stored_counts():
            async with factory() as db:
                return {
                    model.__tablename__: await db.scalar(select(func.count()).select_from(model))
                    for model in (Content, Draft, AgentRun, ContentSlide, RecommendationRound, RecommendationItem)
                }

        app = create_app(enable_dev_api=False)
        app.dependency_overrides[get_db] = test_db
        assert require_onboarding not in app.dependency_overrides
        try:
            before = await stored_counts()
            assert all(count == 0 for count in before.values())
            async with factory() as db, db.begin():
                await db.execute(text("SET TRANSACTION READ ONLY"))
                dry_run = await import_bundle(db, pending)
                assert dry_run["mode"] == "dry_run" and dry_run["created"] == 1
                for model in (Content, Draft, AgentRun):
                    assert await db.scalar(select(func.count()).select_from(model)) == 0
            assert await stored_counts() == before
            first = await apply(pending)
            duplicate = await apply(pending)
            item, = first["items"]
            content_id = item["content_id"]
            assert first["created"] == 1
            assert first["work_links"]["links_added"] == len(prepared.package.menu_work_ids)
            assert item["request_id"] == prepared.package.request_id
            assert (duplicate["created"], duplicate["unchanged"], duplicate["work_links"]["links_added"]) == (0, 1, 0)
            assert duplicate["items"] == [item]
            expected_counts = {**before, "content": 1, "draft": 1, "agent_run": 1, "content_slide": len(expected_pages)}
            assert await stored_counts() == expected_counts
            async with factory() as db:
                content = (await db.scalars(select(Content))).one()
                draft = (await db.scalars(select(Draft))).one()
                agent_run = (await db.scalars(select(AgentRun))).one()
                assert (content.status, content.judgment_status, draft.status) == (
                    "pending_review", "needs_review", "checking"
                )
                assert content.published_at is None
                assert content.ai_judgment_basis["operator_review"] is None
                assert content.ai_judgment_basis["producer_review_is_approval"] is False
                assert agent_run.output_payload["producer_review_evidence"] == prepared.raw.reviewEvidence
            detail_url = f"/api/v1/contents/{content_id}?topicId=1"
            image_url = f"/api/v1/contents/{content_id}/slides/1/image?topicId=1"
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                assert (await client.get(image_url)).status_code == 401
                client.cookies.set(settings.session_cookie_name, token)
                assert await _authenticated_feed_cards(client) == []
                assert (await client.get(detail_url)).status_code == 404
                assert (await client.get(image_url)).status_code == 404

                promoted = await apply(approved)
                assert (promoted["created"], promoted["review_updated"]) == (0, 1)
                assert promoted["items"] == [{**item, "status": "active"}]
                card, = await _authenticated_feed_cards(client)
                assert card["id"] == content_id and card["sourceUrl"] == expected_source
                assert card["recommendationId"] is None and card["publishedAt"]
                assert [slide["page"] for slide in card["slides"]] == expected_pages
                detail = await client.get(detail_url)
                assert detail.status_code == 200, detail.text
                assert detail.json()["body"] == prepared.package.detail_body
                assert "operator_review" not in detail.text and "reviewEvidence" not in detail.text
                for page in expected_pages:
                    image = await client.get(f"/api/v1/contents/{content_id}/slides/{page}/image?topicId=1")
                    assert image.status_code == 200 and image.content == prepared.images[page]
                    assert image.headers["content-type"] == "image/png"
                    assert image.headers["cache-control"] == "private, no-store"
                assert (await client.get(image_url.replace("topicId=1", "topicId=2"))).status_code == 404
                replayed = await apply(approved)
                assert (replayed["created"], replayed["review_updated"], replayed["unchanged"]) == (0, 0, 1)
                replay_card, = await _authenticated_feed_cards(client)
                assert (replay_card["id"], replay_card["publishedAt"]) == (content_id, card["publishedAt"])
                after = await stored_counts()
                assert after == expected_counts
                async with factory() as db:
                    content = (await db.scalars(select(Content))).one()
                    draft = (await db.scalars(select(Draft))).one()
                    agent_run = (await db.scalars(select(AgentRun))).one()
                    assert (content.status, content.judgment_status, draft.status) == ("active", "confirmed", "published")
                    assert content.ai_judgment_basis["operator_review"]["decision"] == "approved"
                    assert agent_run.output_payload["producer_review_evidence"] == prepared.raw.reviewEvidence
                report_path = os.environ.get("INDEPENDENT_REVIEW_TEST_REPORT")
                if report_path:
                    path = Path(report_path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({
                        "actual_bundle": actual_bundle,
                        "request_id": prepared.package.request_id,
                        "source_url": expected_source,
                        "digest": prepared.digest,
                        "cards": len(expected_pages),
                        "dry_run": dry_run,
                        "first_import": first,
                        "duplicate_import": duplicate,
                        "operator_approval": promoted,
                        "approved_reimport": replayed,
                        "pending_feed_count": 0,
                        "pending_detail_status": 404,
                        "pending_image_status": 404,
                        "approved_feed_count": 1,
                        "counts": after,
                        "producer_review_evidence_preserved": True,
                        "all_image_bytes_verified": True,
                        "recommendation_tables_unchanged": True,
                        "service_db_access": False,
                        "llm_calls": 0,
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            app.dependency_overrides.clear()
            await async_engine.dispose()

    _run_async(scenario())
