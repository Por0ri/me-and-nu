"""Opt-in raw RAG consumer regression in the guarded ephemeral carousel_test DB."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentRun, Content, Draft, Entity, EntityExternalId, Topic, UserAccount
from app.repositories.carousel_repository import public_content_condition
from app.seeds import carousel_contents as carousel
from app.services import pending_content_import as pending
from app.services import rag_pending_import as rag
from test_carousel_import import AsyncAdapter
from test_carousel_postgres import carousel_pg, _run_async  # noqa: F401
from test_pending_content_import_postgres import snapshot, target_for
from test_rag_pending_import import fixture, reseal
from test_pending_content_import import make_delivery, policy_for, write_delivery


@pytest.fixture
def rag_pg(carousel_pg, tmp_path):
    engine, url, migrate = carousel_pg
    migrate("head")
    now = datetime.now(timezone.utc)
    with Session(engine) as db, db.begin():
        db.add(Topic(topic_id=1, topic_code="anime", topic_name="Isolated RAG", is_active=True))
        db.flush()
        work = Entity(topic_id=1, name="Isolated existing work")
        db.add(work)
        db.flush()
        db.add(EntityExternalId(provider="menu_work", external_id="8", entity_id=work.entity_id))
        db.add(UserAccount(nickname="preserved-user", auth_provider="kakao", provider_user_id=uuid4().hex,
                           onboarding_completed_at=now))
        db.add(Content(topic_id=1, production_type="human", title="Existing public content", body="Preserve me",
                       status="active", judgment_status="confirmed", published_at=now - timedelta(days=1)))
    f = fixture(tmp_path, target=target_for(url))
    return engine, url, f


async def apply(url, f):
    return await rag.run(policy=f["policy"], feed_valid_until=f["policy"]["feed_valid_until"], envelope=f["path"],
        apply=True, reviewed_sha256=rag.fingerprint(f["policy"]), deployment_env="test",
        database_url=url.render_as_string(hide_password=False))


def use_expiry_mode(f, mode):
    if mode != "override":
        f["native"]["draft"]["valid_until"] = f["policy"]["feed_valid_until"] if mode == "native_deadline" else None
        reseal(f)
        f["policy"]["feed_valid_until"] = None


@pytest.mark.parametrize("expiry_mode", ["override", "native_deadline", "native_none"])
def test_raw_consumer_duplicate_pending_hidden_then_approved_replay_preserves_every_row(rag_pg, expiry_mode):
    engine, url, f = rag_pg
    use_expiry_mode(f, expiry_mode)
    before = snapshot(engine)

    async def scenario():
        dry = await rag.run(policy=f["policy"], feed_valid_until=f["policy"]["feed_valid_until"], envelope=f["path"])
        assert dry["database_accessed"] is False and snapshot(engine) == before
        first = await apply(url, f)
        pending_snapshot = snapshot(engine)
        duplicate = await apply(url, f)
        assert (first["created"], duplicate["unchanged"]) == (1, 1)
        assert snapshot(engine) == pending_snapshot
        content_id = first["items"][0]["content_id"]
        with Session(engine) as db:
            content = db.get(Content, content_id)
            draft = db.get(Draft, content.source_draft_id)
            run = db.get(AgentRun, draft.agent_run_id)
            assert (content.status, content.judgment_status, draft.status) == ("pending_review", "needs_review", "checking")
            assert content.published_at is None and not content.ai_judgment_basis.get("preview")
            assert (content.feed_valid_until is None) is (expiry_mode == "native_none")
            if expiry_mode == "native_none":
                assert content.ai_judgment_basis["feed_expiry_policy"] == "no_deadline_v1"
                assert draft.factcheck_result["feed_expiry_policy"] == "no_deadline_v1"
            assert db.scalar(select(func.count()).select_from(Content).where(
                Content.content_id == content_id, public_content_condition())) == 0
            assert run.output_payload[pending.RECEIPT]["envelope"] == f["raw"]
            assert run.output_payload["producer_review_evidence"]["review"]["review_id"] == "review-original"
        for table in ("user_account", "auth_session", "recommendation_round", "recommendation_item"):
            assert before[table] == pending_snapshot[table]
        assert before["content"][0] == pending_snapshot["content"][0]
        delivery = rag.load_delivery(f["path"], f["policy"], feed_valid_until=f["policy"]["feed_valid_until"])
        item, = delivery.bundle.items
        approval = carousel.ReviewRecord(digest=item.digest, decision="approved", reviewedBy="isolated-operator",
            reviewedAt=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), comment="Isolated regression only")
        approved = replace(delivery.bundle, reviews={item.digest: approval})
        with Session(engine) as db, db.begin():
            report = await carousel.import_bundle(AsyncAdapter(db), approved, apply=True)
            assert report["review_updated"] == 1
        approved_snapshot = snapshot(engine)
        replay = await apply(url, f)
        assert replay["unchanged"] == 1 and snapshot(engine) == approved_snapshot
        with Session(engine) as db:
            content = db.get(Content, content_id)
            draft = db.get(Draft, content.source_draft_id)
            assert (content.status, content.judgment_status, draft.status) == ("active", "confirmed", "published")
            assert not content.ai_judgment_basis.get("preview")
            assert db.scalar(select(func.count()).select_from(Content).where(
                Content.content_id == content_id, public_content_condition())) == 1
            assert db.scalar(select(func.count()).select_from(AgentRun)) == 1
        # Changing an attempt or job cannot make a second service content ID for
        # the same namespace/work; the original native history remains intact.
        f["native"]["generation_attempt_id"] = str(uuid4())
        reseal(f)
        with pytest.raises(rag.RagPendingImportError, match="existing_rag_delivery_changed"):
            await apply(url, f)
        assert snapshot(engine) == approved_snapshot
    _run_async(scenario())


def test_concurrent_same_delivery_imports_one_content(rag_pg):
    engine, url, f = rag_pg
    async def scenario():
        results = await asyncio.gather(apply(url, f), apply(url, f))
        assert sum(row["created"] for row in results) == sum(row["unchanged"] for row in results) == 1
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(AgentRun)) == 1
            assert db.scalar(select(func.count()).select_from(Draft)) == 1
    _run_async(scenario())


@pytest.mark.parametrize("expiry_mode", ["override", "native_deadline", "native_none"])
def test_expired_day_reconciliation_keeps_original_receipt_pending_visibility_and_replay(rag_pg, expiry_mode):
    engine, url, f = rag_pg
    use_expiry_mode(f, expiry_mode)
    before = snapshot(engine)
    original_policy = deepcopy(f["policy"])
    original_envelope = f["path"].read_bytes()
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)

    async def scenario():
        async def reconcile():
            return await rag.run(policy=f["policy"], feed_valid_until=f["policy"]["feed_valid_until"],
                envelope=f["path"], apply=True, reviewed_sha256=rag.fingerprint(f["policy"]),
                deployment_env="test", database_url=url.render_as_string(hide_password=False), now=tomorrow,
                reconcile_policy_date=f["policy"]["policy_date"])
        first = await reconcile()
        saved = snapshot(engine)
        replay = await reconcile()
        assert first["created"] == replay["unchanged"] == 1
        assert snapshot(engine) == saved and f["policy"] == original_policy
        assert f["path"].read_bytes() == original_envelope
        with Session(engine) as db:
            content = db.get(Content, first["items"][0]["content_id"])
            draft = db.get(Draft, content.source_draft_id)
            run = db.get(AgentRun, draft.agent_run_id)
            receipt = run.output_payload[pending.RECEIPT]
            assert receipt["envelope"] == f["raw"]
            assert receipt["day_policy_sha256"] == original_policy["day_policy_sha256"]
            assert receipt["policy_date_reconciliation"] == {"policy_date": original_policy["policy_date"],
                "reconciled_at": tomorrow.isoformat(), "mode": "explicit_pending_only"}
            assert (content.status, content.judgment_status, draft.status) == ("pending_review", "needs_review", "checking")
            assert content.published_at is None and not content.ai_judgment_basis.get("preview")
            assert db.scalar(select(func.count()).select_from(Content).where(
                Content.content_id == content.content_id, public_content_condition())) == 0
        for table in ("user_account", "auth_session", "recommendation_round", "recommendation_item"):
            assert saved[table] == before[table]
        assert saved["content"][0] == before["content"][0]
    _run_async(scenario())


def test_period_receipts_and_concurrent_rag_imports_share_twenty_cap(rag_pg):
    engine, url, f = rag_pg
    with Session(engine) as db, db.begin():
        for index in range(19):
            db.add(AgentRun(topic_id=1, agent_code="isolated_period_receipt", agent_version="1",
                queue_task_id=f"isolated-period-{index}", status="succeeded", outcome="on_hold",
                input_payload={"producer": "ani_menu_agent"}, output_payload={pending.RECEIPT: {
                    "day_policy_sha256": f["policy"]["day_policy_sha256"], "original_receipt": index}}))
    original = snapshot(engine)
    second = deepcopy(f)
    second["native"]["job_id"] = str(uuid4())
    second["native"]["generation_attempt_id"] = str(uuid4())
    second["native"]["work_id"] = second["native"]["request"]["work_id"] = second["request"]["work_id"] = "another-work"
    second["path"] = f["inbox"] / (second["native"]["job_id"] + ".json")
    reseal(second)
    async def scenario():
        results = await asyncio.gather(apply(url, f), apply(url, second), return_exceptions=True)
        assert sum(isinstance(row, dict) and row["created"] == 1 for row in results) == 1
        errors = [row for row in results if isinstance(row, Exception)]
        assert len(errors) == 1 and str(errors[0]) == "durable_daily_import_limit_exceeded"
        after = snapshot(engine)
        assert after["agent_run"][:19] == original["agent_run"]
        for table in ("user_account", "auth_session", "recommendation_round", "recommendation_item"):
            assert after[table] == original[table]
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(AgentRun).where(
                AgentRun.output_payload[pending.RECEIPT]["day_policy_sha256"].as_string()
                == f["policy"]["day_policy_sha256"])) == 20
    _run_async(scenario())


def test_nineteen_real_rag_imports_leave_only_one_slot_for_unchanged_period_consumer(rag_pg, tmp_path):
    engine, url, f = rag_pg
    before = snapshot(engine)

    async def scenario():
        # Use the actual new consumer to create all 19 RAG receipts; this tests
        # their persisted JSON shape against the unchanged period SQL query.
        for index in range(19):
            item = deepcopy(f)
            item["native"]["job_id"] = str(uuid4())
            item["native"]["generation_attempt_id"] = str(uuid4())
            work = f"rag-before-period-{index}"
            item["native"]["work_id"] = item["native"]["request"]["work_id"] = item["request"]["work_id"] = work
            item["path"] = f["inbox"] / (item["native"]["job_id"] + ".json")
            reseal(item)
            assert (await apply(url, item))["created"] == 1
        period_root = tmp_path / "period-bundles"
        period_root.mkdir()
        inputs = []
        for number in (1, 2):
            raw, envelope = make_delivery(period_root, number=number, version=2)
            envelope["day_policy_sha256"] = f["policy"]["day_policy_sha256"]
            inbox = tmp_path / f"period-inbox-{number}"
            write_delivery(period_root, raw, envelope, inbox=inbox)
            policy = policy_for(period_root, envelope, target=target_for(url))
            inputs.append((inbox, policy))
        async def period(inbox, policy):
            return await pending.run(policy=policy, database_url=url.render_as_string(hide_password=False),
                deployment_env="test", reviewed_sha256=pending.fingerprint(policy), inbox=inbox, apply=True)
        results = await asyncio.gather(*(period(*item) for item in inputs), return_exceptions=True)
        assert sum(isinstance(row, dict) and row["created"] == 1 for row in results) == 1
        errors = [row for row in results if isinstance(row, Exception)]
        assert len(errors) == 1 and str(errors[0]) == "durable_daily_import_limit_exceeded"
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(AgentRun)) == 20
            assert db.scalar(select(func.count()).select_from(Content).where(public_content_condition())) == 1
        after = snapshot(engine)
        for table in ("user_account", "auth_session", "recommendation_round", "recommendation_item"):
            assert before[table] == after[table]
        assert before["content"][0] == after["content"][0]
    _run_async(scenario())
