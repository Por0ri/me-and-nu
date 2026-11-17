"""Synthetic fixed-publication rehearsal; never accepts a service database.

Run inside the backend validation image with candidate /app/app mounted read-only.
MAIN_PUBLICATION_TEST_DATABASE_URL must target the internal disposable PostgreSQL
container main-publication-test-db and database test_main_publication_20261117.
The script creates and drops only its random test schema. No LLM or HTTP calls.
"""
import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import Base
from app.models import (
    AgentRun, Content, ContentSlide, ContentSource, Draft, JudgmentLog,
    RecommendationItem, RecommendationRound, Tap, Topic, UserAccount,
)
from app.repositories import recommendation_repository
from app.repositories.carousel_repository import public_content_condition
from app.services import main_publication_transition as transition


DB_NAME = "test_main_publication_20261117"
OPERATOR = "synthetic-main-publication-test"
PREVIEW_COUNT = 3526
FIXED_COUNT = 3530
EXCLUDED_COUNT = 517
INITIAL_COUNT = FIXED_COUNT + EXCLUDED_COUNT
LATE_ID = INITIAL_COUNT + 1
FIXED_IDS = list(range(1, FIXED_COUNT + 1))
APPROVED_IDS = list(range(PREVIEW_COUNT + 1, FIXED_COUNT + 1))
EXCLUDED_IDS = list(range(FIXED_COUNT + 1, LATE_ID + 1))
NEGATIVE_KINDS = ("expired", "future", "hidden", "pending", "deleted", "invalid_slides", "no_source")
PREVIEW_KEYS = {"preview", "local_test_preview", "checking_test_preview"}
PROTECTED_TABLES = (
    "user_account", "auth_session", "tap", "recommendation_round", "recommendation_item",
    "agent_run", "judgment_log", "content_source", "content_slide",
)


def guarded_url():
    url = make_url(os.environ["MAIN_PUBLICATION_TEST_DATABASE_URL"])
    if (url.drivername != "postgresql+psycopg" or url.host != "main-publication-test-db"
            or url.database != DB_NAME or url.port not in (None, 5432) or url.query):
        raise RuntimeError("Only the explicitly named internal synthetic PostgreSQL database is accepted")
    return url


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, default=str).encode()).hexdigest()


@contextmanager
def visibility_mode(checking):
    # These values only select the real SQL visibility predicate. Database reads
    # always use the independently guarded test engine, never this synthetic URL.
    values = dict(
        checking_carousel_test_preview_enabled=checking,
        menu_deployment_env="checking" if checking else "production",
        event_data_source="automated_test" if checking else "production",
        database_url="postgresql+psycopg://me_nu_checking:synthetic@postgres:5432/me_nu_checking",
        oauth_frontend_origin="https://checking.me-nu.kr" if checking else "https://www.me-nu.kr",
        oauth_public_api_origin="https://api.checking.me-nu.kr" if checking else "https://api.me-nu.kr",
        frontend_origins="https://checking.me-nu.kr,https://api.checking.me-nu.kr" if checking else "https://www.me-nu.kr",
        enable_dev_api=False, enable_dev_auth_bypass=False, session_cookie_secure=True,
    )
    with patch.multiple(settings, **values):
        yield


def fixture_rows(content_id, now, kind="preview"):
    evidence = {"fixture_only": True, "content_id": content_id, "decision": "not_reviewed",
                "original_rejections": [{"decision": "rejected", "reason": "synthetic historical record"}]}
    preview = dict(preview=True, checking_test_preview={
        "database": "me_nu_checking", "environment": "checking", "quality_approval": "not_reviewed",
    })
    basis = {"method": "carousel_import_v1", "producer_review_evidence": evidence}
    review = {"producer_review_evidence": evidence}
    if kind != "approved":
        basis.update(preview)
        review.update(preview)
    draft = dict(draft_id=content_id, agent_run_id=content_id, topic_id=1,
                 production_type="ai", content_type="goods", title=f"Synthetic {content_id}",
                 body=f"Preserved draft body {content_id}", status="published" if kind == "approved" else "checking",
                 factcheck_result=review, structure_template={"pages": 2},
                 structure_template_history={"original": True}, created_at=now, updated_at=now)
    content = dict(content_id=content_id, source_draft_id=content_id, topic_id=1,
                   production_type="ai", content_type="goods", title=f"Synthetic {content_id}",
                   body=f"Preserved content body {content_id}", summary="Synthetic isolated fixture",
                   status="active", judgment_status="confirmed" if kind == "approved" else "needs_review",
                   ai_judgment_basis=basis, published_at=now - timedelta(hours=1),
                   feed_valid_until=now + timedelta(days=5), created_at=now, updated_at=now,
                   deleted_at=None)
    if kind == "expired":
        content["feed_valid_until"] = now - timedelta(days=1)
    elif kind == "future":
        content["published_at"] = now + timedelta(days=1)
    elif kind == "hidden":
        content["status"] = "hidden"
    elif kind == "pending":
        content.update(status="pending_review", published_at=None)
    elif kind == "deleted":
        content.update(status="deleted", deleted_at=now)
    run = dict(agent_run_id=content_id, topic_id=1, agent_code="synthetic", agent_version="test",
               status="succeeded", outcome="publish_candidate", input_payload={"synthetic": content_id},
               output_payload={"producer_review_evidence": evidence}, created_at=now)
    return run, draft, content


async def insert_content_batch(db, ids_and_kinds, now, png):
    runs, drafts, contents, slides, sources, judgments = [], [], [], [], [], []
    for content_id, kind in ids_and_kinds:
        run, draft, content = fixture_rows(content_id, now, kind)
        runs.append(run)
        drafts.append(draft)
        contents.append(content)
        judgments.append(dict(judgment_log_id=content_id, agent_run_id=content_id, draft_id=content_id,
                              content_id=content_id, stage_name="judge", agent_code="synthetic",
                              prompt_version="test", judgment_result={"decision": "rejected", "fixture_only": True},
                              is_adopted=False, judged_at=now))
        for page in range(1, 2 if kind == "invalid_slides" else 3):
            slides.append(dict(content_id=content_id, page=page, role="cover" if page == 1 else "detail",
                               eyebrow="Synthetic only", title_lines=[f"Synthetic page {page}"],
                               body_sentences=["No generation or external source request"], layout_json={},
                               image_data=png, image_sha256=hashlib.sha256(png).hexdigest(),
                               image_width=1308, image_height=1632))
        if kind != "no_source":
            sources.append(dict(content_source_id=content_id, content_id=content_id,
                                source_url=f"https://example.invalid/synthetic/{content_id}",
                                source_title="Synthetic fixture", source_role="primary", created_at=now))
    for model, rows in [(AgentRun, runs), (Draft, drafts), (Content, contents),
                        (ContentSlide, slides), (ContentSource, sources), (JudgmentLog, judgments)]:
        if rows:
            await db.execute(insert(model), rows)


async def seed(factory, now, png):
    kinds = {}
    async with factory() as db, db.begin():
        await db.execute(insert(Topic), [dict(topic_id=1, topic_code="anime", topic_name="Synthetic anime", is_active=True)])
        await db.execute(insert(UserAccount), [dict(user_id=i, nickname=f"Synthetic member {i}",
                            auth_provider="kakao", provider_user_id=f"synthetic-{i}",
                            onboarding_completed_at=now) for i in range(1, 4)])
        await db.execute(insert(Tap), [dict(tap_id=i, user_id=i, topic_id=1) for i in range(1, 4)])
        for content_id in range(1, INITIAL_COUNT + 1):
            kinds[content_id] = ("preview" if content_id <= PREVIEW_COUNT else
                                  "approved" if content_id <= FIXED_COUNT else
                                  NEGATIVE_KINDS[(content_id - FIXED_COUNT - 1) % len(NEGATIVE_KINDS)])
        for start in range(1, INITIAL_COUNT + 1, 250):
            await insert_content_batch(db, [(i, kinds[i]) for i in range(start, min(start + 250, INITIAL_COUNT + 1))], now, png)
        await db.execute(insert(RecommendationRound), [dict(recommendation_round_id=i, tap_id=1,
                         service_date=now.date(), algorithm_version=f"synthetic-{i}") for i in range(1, 64)])
        await db.execute(insert(RecommendationItem), [dict(recommendation_item_id=i,
                         recommendation_round_id=(i - 1) // 100 + 1, content_id=(i - 1) % INITIAL_COUNT + 1,
                         rank=(i - 1) % 100 + 1, score=1 / i, recommend_reason={"fixture": i}) for i in range(1, 6301)])
    return Counter(kinds.values())


async def query_visible(factory, *, checking, now):
    with visibility_mode(checking):
        async with factory() as db:
            public_ids = list((await db.scalars(select(Content.content_id).where(public_content_condition())
                                               .order_by(Content.content_id))).all())
            latest_ids = await recommendation_repository.latest_content_ids(db, 1, now, 10000)
            projected = await recommendation_repository.contents_by_id(db, list(range(1, LATE_ID + 1)), 1)
            ranked = [await recommendation_repository.ranked_items(db, i, 1) for i in range(1, 64)]
            assert set(latest_ids) == set(public_ids) == set(projected), "Public and recommendation predicates diverged"
            return {"public_ids": public_ids, "latest_ids": latest_ids, "ranked": ranked}


async def table_hashes(factory):
    # Names are code constants; rows include all persisted columns, not ORM projections.
    async with factory() as db:
        return {name: digest(list((await db.scalars(text(
            f'SELECT row_to_json(t)::text FROM "{name}" AS t ORDER BY row_to_json(t)::text'
        ))).all())) for name in PROTECTED_TABLES}


async def mutable_hash(factory):
    async with factory() as db:
        return digest({name: list((await db.scalars(text(
            f'SELECT row_to_json(t)::text FROM "{name}" AS t ORDER BY row_to_json(t)::text'
        ))).all()) for name in ("content", "draft")})


async def review_evidence_hash(factory):
    async with factory() as db:
        rows = (await db.execute(select(
            Content.content_id, Content.ai_judgment_basis["producer_review_evidence"],
            Draft.factcheck_result["producer_review_evidence"],
        ).join(Draft, Draft.draft_id == Content.source_draft_id).order_by(Content.content_id))).all()
        return digest([list(row) for row in rows])


async def snapshot(factory, ids):
    async with factory() as db:
        return await transition.snapshot_rows(db, ids)


async def expect_conflict(factory, plan):
    async with factory() as db:
        try:
            await transition.apply_isolated_plan(db, plan, operator_id=OPERATOR)
        except transition.TransitionConflict:
            return
    raise AssertionError("A stale or failed plan unexpectedly committed")


async def scenario(factory):
    now = datetime.now(timezone.utc)
    out = io.BytesIO()
    Image.new("1", (1308, 1632), 1).save(out, format="PNG")
    png = out.getvalue()
    distribution = await seed(factory, now, png)
    before_checking = await query_visible(factory, checking=True, now=now)
    before_main = await query_visible(factory, checking=False, now=now)
    assert before_checking["public_ids"] == FIXED_IDS
    assert before_main["public_ids"] == APPROVED_IDS
    plan = transition.build_plan(await snapshot(factory, FIXED_IDS), FIXED_IDS, as_of=now, operator_id=OPERATOR)
    assert Counter(entry["classification"] for entry in plan["entries"]) == {
        "existing_checking_preview": PREVIEW_COUNT, "already_approved": 4,
    }
    # Newly queued content after approval is deliberately outside the fixed manifest.
    async with factory() as db, db.begin():
        await insert_content_batch(db, [(LATE_ID, "pending")], now, png)
    protected_before = await table_hashes(factory)
    evidence_before = await review_evidence_hash(factory)
    excluded_before = transition.fingerprint(await snapshot(factory, EXCLUDED_IDS))
    approved_before = transition.fingerprint(await snapshot(factory, APPROVED_IDS))

    # A different DB session commits a material edit after export. The whole
    # manifest must fail before any publication; preserve that writer's edit.
    changed_id = PREVIEW_COUNT
    original = plan["entries"][changed_id - 1]["snapshot"]["content"]
    async with factory() as other_writer, other_writer.begin():
        await other_writer.execute(update(Content).where(Content.content_id == changed_id)
                                   .values(title="Concurrent synthetic edit", updated_at=now + timedelta(seconds=1)))
    changed_hash = await mutable_hash(factory)
    await expect_conflict(factory, plan)
    assert await mutable_hash(factory) == changed_hash
    async with factory() as other_writer, other_writer.begin():
        await other_writer.execute(update(Content).where(Content.content_id == changed_id)
            .values(title=original["title"], updated_at=datetime.fromisoformat(original["updated_at"])))

    # Also prove real PostgreSQL rollback after writes have already started.
    original_update_draft = transition.repo.update_draft
    async def fail_second_draft(db, original_row, values):
        if original_row["draft_id"] == 2:
            return False
        return await original_update_draft(db, original_row, values)
    before_failure = await mutable_hash(factory)
    with patch.object(transition.repo, "update_draft", fail_second_draft):
        await expect_conflict(factory, plan)
    assert await mutable_hash(factory) == before_failure
    assert await table_hashes(factory) == protected_before

    async with factory() as db:
        applied = await transition.apply_isolated_plan(db, plan, operator_id=OPERATOR)
    assert (applied["transitioned"], applied["replayed"], applied["already_approved"]) == (PREVIEW_COUNT, 0, 4)
    after_main = await query_visible(factory, checking=False, now=now)
    after_checking = await query_visible(factory, checking=True, now=now)
    assert after_main == before_checking == after_checking
    assert await table_hashes(factory) == protected_before
    assert await review_evidence_hash(factory) == evidence_before
    assert transition.fingerprint(await snapshot(factory, EXCLUDED_IDS)) == excluded_before
    assert transition.fingerprint(await snapshot(factory, APPROVED_IDS)) == approved_before
    rows = await snapshot(factory, FIXED_IDS)
    for row in rows:
        content, draft = row["content"], row["draft"]
        assert (content["status"], content["judgment_status"], draft["status"]) == ("active", "confirmed", "published")
        assert not PREVIEW_KEYS.intersection(content["ai_judgment_basis"])
        assert not PREVIEW_KEYS.intersection(draft["factcheck_result"])
        for basis in (content["ai_judgment_basis"], draft["factcheck_result"]):
            history = basis.get(transition.HISTORY_KEY, [])
            assert len(history) == (1 if content["content_id"] <= PREVIEW_COUNT else 0)
            if history:
                assert history[0]["approval_kind"] == "operator_bulk_publication"
                assert history[0]["llm_quality_review_performed"] is False
                assert history[0]["operator_id"] == OPERATOR
                assert history[0]["manifest_sha256"] == plan["manifest_sha256"]
                assert history[0]["before_value"]["content"]["judgment_status"] == "needs_review"
    after_hash = await mutable_hash(factory)
    async with factory() as db:
        replayed = await transition.apply_isolated_plan(db, plan, operator_id=OPERATOR)
    assert (replayed["transitioned"], replayed["replayed"], replayed["already_approved"]) == (0, PREVIEW_COUNT, 4)
    assert await mutable_hash(factory) == after_hash
    assert await table_hashes(factory) == protected_before
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Content)) == LATE_ID
        assert await db.scalar(select(func.count()).select_from(RecommendationItem)) == 6300
        assert await db.scalar(text("SELECT to_regclass('admin_action_log')")) is None
    return dict(
        fixture_only=True, fixture_distribution=dict(distribution), fixed_target_count=FIXED_COUNT,
        checking_visible_before=FIXED_COUNT, main_visible_before=4, main_visible_after=FIXED_COUNT,
        newly_pending_preserved=1, excluded_preserved=EXCLUDED_COUNT, transitioned=PREVIEW_COUNT,
        existing_approved_unchanged=4, recommendation_items_preserved=6300,
        members_and_recommendations_hashes=protected_before, review_evidence_sha256=evidence_before,
        public_and_recommendation_sets_identical=True, stale_concurrent_edit_rejected=True,
        mid_apply_cas_failure_rolled_back=True, replay_no_writes=True, new_audit_table_created=False,
        manifest_sha256=plan["manifest_sha256"], llm_calls=0, service_database_writes=0,
    )


async def main():
    url = guarded_url()
    schema = "main_publication_" + uuid4().hex
    admin = create_async_engine(url, poolclass=NullPool)
    scoped = None
    try:
        async with admin.begin() as db:
            assert await db.scalar(text("SELECT current_database()")) == DB_NAME
            await db.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
            await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = create_async_engine(url, connect_args={"options": f"-csearch_path={schema},public"}, poolclass=NullPool)
        async with scoped.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        result = await scenario(async_sessionmaker(scoped, expire_on_commit=False))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        if scoped is not None:
            await scoped.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


if __name__ == "__main__":
    asyncio.run(main())
