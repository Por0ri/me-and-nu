"""Opt-in, random-schema carousel_test regression; never use the service DB.

PENDING_IMPORT_TEST_INBOX / PENDING_IMPORT_TEST_BUNDLE_ROOT optionally consume
the immutable real-Redis/Celery offline handoff. Its synthetic future policy date
is used only as this test's explicit clock; the production CLI has no clock flag.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models import (AgentRun, AuthSession, Content, ContentEntityLink, ContentSlide, ContentSource, Draft,
                        Entity, EntityExternalId, RecommendationItem, RecommendationRound, Topic, UserAccount)
from app.repositories.carousel_repository import public_content_condition
from app.seeds import carousel_contents as carousel
from app.services import pending_content_import as pending
from test_carousel_import import AsyncAdapter
from test_carousel_postgres import carousel_pg, _run_async  # noqa: F401 -- guarded disposable fixture
from test_pending_content_import import make_delivery, policy_for, write_delivery


SNAPSHOT_MODELS = (AgentRun, Content, Draft, ContentSlide, ContentSource, ContentEntityLink,
                   RecommendationRound, RecommendationItem, AuthSession, UserAccount)


def snapshot(engine):
    with Session(engine) as db:
        return {model.__tablename__: [dict(row) for row in db.execute(
            select(model.__table__).order_by(*model.__table__.primary_key.columns)).mappings()]
            for model in SNAPSHOT_MODELS}


def target_for(url):
    schema = url.query["options"].removeprefix("-csearch_path=").split(",")[0]
    return {"host":url.host, "port":url.port or 5432, "database":url.database,
            "username":url.username, "schema":schema, "environment":"test"}


@pytest.fixture
def pending_pg(carousel_pg, tmp_path):
    engine, url, migrate = carousel_pg
    migrate("head")
    with Session(engine) as db, db.begin():
        db.add(Topic(topic_id=1, topic_code="anime", topic_name="격리 애니", is_active=True))
        db.flush()
        entity = Entity(topic_id=1, name="격리 원피스")
        db.add(entity)
        db.flush()
        db.add(EntityExternalId(provider="menu_work", external_id="8", entity_id=entity.entity_id))
    root = tmp_path/"bundles"
    root.mkdir()
    inbox = tmp_path/"inbox"
    inbox.mkdir()
    raw, envelope = make_delivery(root, independent=True)
    write_delivery(root, raw, envelope, inbox=inbox)
    policy = policy_for(root, envelope, target=target_for(url))
    # Preserve a pre-existing unlinked carousel. Calling the global legacy linker
    # would wrongly add its work link during an unrelated new import.
    legacy_root = tmp_path/"legacy"
    legacy_root.mkdir()
    _, old_envelope = make_delivery(legacy_root, number=99)
    old_bundle = carousel.load_bundle(legacy_root/old_envelope["bundle_path"])
    async def seed_legacy():
        with Session(engine) as db, db.begin():
            return await carousel.import_bundle(AsyncAdapter(db), old_bundle, apply=True)
    legacy = _run_async(seed_legacy())["items"][0]["content_id"]
    return engine, url, root, inbox, raw, envelope, policy, legacy


async def execute(url, policy, *, inbox=None, manifest=None, apply=False, now=None):
    return await pending.run(policy=policy, database_url=url.render_as_string(hide_password=False),
        deployment_env="test", reviewed_sha256=pending.fingerprint(policy) if apply else None,
        inbox=inbox, manifest=manifest, apply=apply, now=now)


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_pending_duplicate_terminal_replay_preserves_evidence_and_unrelated_links(pending_pg, tmp_path, decision):
    engine, url, root, inbox, raw, envelope, policy, legacy = pending_pg
    before = snapshot(engine)
    async def scenario():
        preview = await execute(url, policy, inbox=inbox)
        assert preview["mode"] == "dry_run" and preview["created"] == 1
        assert snapshot(engine) == before
        first = await execute(url, policy, inbox=inbox, apply=True)
        duplicate = await execute(url, policy, inbox=inbox, apply=True)
        assert (first["created"], first["links_added"], duplicate["created"], duplicate["unchanged"]) == (1,1,0,1)
        content_id = first["items"][0]["content_id"]
        with Session(engine) as db:
            content = db.get(Content, content_id)
            draft = db.get(Draft, content.source_draft_id)
            run = db.get(AgentRun, draft.agent_run_id)
            assert (content.status,content.judgment_status,draft.status) == ("pending_review","needs_review","checking")
            assert content.published_at is None and content.ai_judgment_basis["operator_review"] is None
            assert run.output_payload["producer_review_evidence"] == raw["items"][0]["reviewEvidence"]
            assert run.output_payload["producer_review_evidence"]["originalGeneration"]["outcome"] == "rejected"
            assert run.output_payload[pending.RECEIPT]["envelope"] == envelope
            assert db.scalar(select(func.count()).select_from(ContentEntityLink).where(ContentEntityLink.content_id==legacy)) == 0
            assert db.scalar(select(func.count()).select_from(Content).where(Content.content_id==content_id, public_content_condition())) == 0
        bundle_path = root/envelope["bundle_path"]
        prepared = carousel.load_bundle(bundle_path)
        record_path = tmp_path/"explicit-isolated-operator-review.json"
        record_path.write_text(json.dumps({"schemaVersion":1,"records":[{
            "digest":prepared.items[0].digest,"decision":decision,"reviewedBy":"isolated-test-operator",
            "reviewedAt":(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),
            "comment":"Explicit isolated regression action, never an automatic approval."}]}))
        terminal = carousel.load_bundle(bundle_path, review_record_file=record_path)
        async_engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with async_sessionmaker(async_engine)() as db, db.begin():
                approved = await carousel.import_bundle(db, terminal, apply=True)
                assert approved["review_updated"] == 1
        finally:
            await async_engine.dispose()
        terminal_snapshot = snapshot(engine)
        replay = await execute(url, policy, inbox=inbox, apply=True)
        assert (replay["created"],replay["unchanged"],replay["review_updated"]) == (0,1,0)
        assert snapshot(engine) == terminal_snapshot
        with Session(engine) as db:
            row = db.get(Content, content_id)
            draft = db.get(Draft, row.source_draft_id)
            expected = ("active","confirmed","published") if decision=="approved" else ("hidden","failed","discarded")
            assert (row.status,row.judgment_status,draft.status) == expected
            assert db.scalar(select(func.count()).select_from(Content).where(Content.content_id==content_id, public_content_condition())) == int(decision=="approved")
        after = snapshot(engine)
        for name in ("recommendation_round","recommendation_item","auth_session","user_account"):
            assert after[name] == before[name]
    _run_async(scenario())


def test_concurrent_delivery_commits_one_content_and_one_receipt(pending_pg):
    engine, url, _, inbox, _, envelope, policy, _ = pending_pg
    async def scenario():
        results = await asyncio.gather(*(execute(url,policy,inbox=inbox,apply=True) for _ in range(2)))
        assert sorted(result["created"] for result in results) == [0,1]
        with Session(engine) as db:
            runs = list(db.scalars(select(AgentRun).where(
                AgentRun.output_payload["producer_review_evidence"]["jobId"].as_string()==envelope["job_id"])))
            assert len(runs)==1 and runs[0].output_payload[pending.RECEIPT]["envelope"]==envelope
    _run_async(scenario())


def test_daily_limit_survives_inbox_replacement_and_process_restarts(pending_pg, tmp_path):
    engine, url, root, inbox, _, _, policy, _ = pending_pg
    policy["max_items"] = 1
    second_raw, second = make_delivery(root, number=2)
    second_inbox = tmp_path/"second-inbox"
    write_delivery(root,second_raw,second,inbox=second_inbox)
    async def scenario():
        await execute(url,policy,inbox=inbox,apply=True)
        before = snapshot(engine)
        with pytest.raises(pending.PendingImportError, match="durable_daily_import_limit_exceeded"):
            await execute(url,policy,inbox=second_inbox,apply=True)
        assert snapshot(engine)==before
    _run_async(scenario())


def test_missing_work_in_later_delivery_rolls_back_the_complete_batch(pending_pg, tmp_path):
    engine, url, root, inbox, _, _, policy, _ = pending_pg
    raw, envelope = make_delivery(root, number=2, work_id=999999)
    write_delivery(root,raw,envelope,inbox=inbox)
    before = snapshot(engine)
    with pytest.raises(pending.PendingImportError, match="existing_work_mapping_required"):
        _run_async(execute(url,policy,inbox=inbox,apply=True))
    assert snapshot(engine)==before


def test_changed_delivery_or_review_evidence_cannot_rebind_existing_import(pending_pg):
    engine, url, root, inbox, raw, envelope, policy, _ = pending_pg
    _run_async(execute(url,policy,inbox=inbox,apply=True))
    before = snapshot(engine)
    raw["items"][0]["reviewEvidence"]["language_review"]["content_type_fit"] = 4
    write_delivery(root,raw,envelope,inbox=inbox)
    with pytest.raises(pending.PendingImportError, match="existing_review_evidence_changed"):
        _run_async(execute(url,policy,inbox=inbox,apply=True))
    assert snapshot(engine)==before


def test_external_celery_export_handoff_is_pending_and_idempotent(carousel_pg, tmp_path):
    inbox_value = os.environ.get("PENDING_IMPORT_TEST_INBOX")
    root_value = os.environ.get("PENDING_IMPORT_TEST_BUNDLE_ROOT")
    if not inbox_value and not root_value:
        pytest.skip("optional real-Celery offline handoff was not selected")
    assert inbox_value and root_value
    inbox, root = Path(inbox_value), Path(root_value)
    envelopes = [pending._json(pending.read_file(path)) for path in sorted(inbox.glob("*.json"))]
    assert envelopes
    engine, url, migrate = carousel_pg
    migrate("head")
    policy = policy_for(root,envelopes[0],target=target_for(url))
    now = datetime.fromisoformat(policy["policy_date"]+"T12:00:00+09:00")
    deliveries = pending.load_deliveries(policy,inbox=inbox,now=now)
    work_ids = sorted({work for d in deliveries for work in d.bundle.items[0].package.menu_work_ids})
    with Session(engine) as db, db.begin():
        db.add(Topic(topic_id=1,topic_code="anime",topic_name="격리 handoff",is_active=True))
        db.flush()
        for work in work_ids:
            entity = Entity(topic_id=1,name=f"격리 작품 {work}")
            db.add(entity)
            db.flush()
            db.add(EntityExternalId(provider="menu_work",external_id=str(work),entity_id=entity.entity_id))
    before = snapshot(engine)
    async def scenario():
        dry = await execute(url,policy,inbox=inbox,now=now)
        assert snapshot(engine)==before
        first = await execute(url,policy,inbox=inbox,apply=True,now=now)
        persisted = snapshot(engine)
        duplicate = await execute(url,policy,inbox=inbox,apply=True,now=now)
        assert snapshot(engine)==persisted
        assert first["created"]==len(deliveries) and duplicate["unchanged"]==len(deliveries)
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Content).where(public_content_condition()))==0
            assert all((r.status,r.judgment_status)==("pending_review","needs_review") for r in db.scalars(select(Content)))
            assert all(r.status=="checking" for r in db.scalars(select(Draft)))
        for name in ("recommendation_round","recommendation_item","auth_session","user_account"):
            assert persisted[name]==before[name]
        report = {"dry_run":dry,"first":first,"replay":duplicate,"pending_feed_count":0,
                  "producer_evidence_preserved":True,"service_database_access":False,"actual_llm_calls":0}
        if os.environ.get("PENDING_IMPORT_TEST_REPORT"):
            Path(os.environ["PENDING_IMPORT_TEST_REPORT"]).write_text(json.dumps(report,indent=2),encoding="utf-8")
    _run_async(scenario())
