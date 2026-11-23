"""Real migrated PostgreSQL: atomic support writes, races, replay and erasure."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.exceptions import ApiError
from app.models import AdminAccount, AdminActionLog, Content, ContentReport, Inquiry, InquiryAnswer, Topic, UserAccount
from app.schemas.admin_support_action import AdminInquiryAnswerRequest, AdminReportActionRequest, AdminSupportScope
from app.services import admin_support_action_service as service
from app.services.admin_support_permission_provider import DatabaseAdminSupportPermissionProvider
from test_admin_postgres_integration import STAMP, admin_pg, run_async, sessions  # noqa: F401


def seed(pg):
    pg.migrate()
    with Session(pg.engine) as db, db.begin():
        for uid, role in ((7, "admin"), (8, "operator"), (9, "viewer"), (10, "operator")):
            db.add(UserAccount(user_id=uid, nickname=f"synthetic-{uid}", auth_provider="kakao",
                               provider_user_id=f"support-action-{uid}", onboarding_completed_at=STAMP))
        db.add(UserAccount(user_id=101, nickname="synthetic-owner", auth_provider="kakao", provider_user_id="support-owner"))
        db.add(Topic(topic_id=1, topic_code="anime", topic_name="애니메이션", is_active=True))
        db.flush()
        db.add_all(AdminAccount(admin_account_id=uid + 100, user_id=uid, role=role)
                   for uid, role in ((7, "admin"), (8, "operator"), (9, "viewer"), (10, "operator")))
        db.add(Content(content_id=1, topic_id=1, title="Synthetic unchanged content", status="active", production_type="human"))
        db.flush()
        db.add(ContentReport(report_id=1, user_id=101, content_id=1, reason_code="wrong_info", detail="Synthetic report",
                             status="received", created_at=STAMP, updated_at=STAMP))
        db.add(Inquiry(inquiry_id=1, user_id=101, inquiry_type="service", detail="Synthetic inquiry",
                       status="received", created_at=STAMP, updated_at=STAMP))


def report_request(**changes):
    return AdminReportActionRequest(**{"requestId": str(uuid4()), "action": "start_review", "rationale": "확인 시작",
                                       "expectedStatus": "received", "expectedUpdatedAt": STAMP.isoformat(), **changes})


def answer_request(**changes):
    return AdminInquiryAnswerRequest(**{"requestId": str(uuid4()), "answer": "비공개 합성 답변 <script>alert(1)</script>",
                                        "expectedStatus": "received", "expectedUpdatedAt": STAMP.isoformat(), **changes})


async def apply(factory, payload, *, uid=8, scope=None):
    async with factory() as db:
        operation = service.answer_inquiry if isinstance(payload, AdminInquiryAnswerRequest) else service.apply_report_action
        return await operation(db, 1, payload, user_id=uid, permissions=DatabaseAdminSupportPermissionProvider(),
                               expected_scope=scope or AdminSupportScope(actor_user_id=str(uid), admin_account_id=str(uid + 100)))


def snapshot(pg):
    with pg.engine.connect() as db:
        return (db.scalar(text("SELECT status FROM content_report WHERE report_id=1")),
                db.scalar(text("SELECT status FROM inquiry WHERE inquiry_id=1")),
                db.scalar(text("SELECT count(*) FROM inquiry_answer")),
                db.scalar(text("SELECT count(*) FROM admin_action_log")))


def test_report_lifecycle_audit_and_content_are_separate(admin_pg):
    seed(admin_pg)
    async def scenario():
        async with sessions(admin_pg) as factory:
            started = await apply(factory, report_request())
            assert started.raw_status == "reviewing" and started.updated_at.tzinfo
            completed = await apply(factory, report_request(action="resolve", expectedStatus="reviewing",
                                                            expectedUpdatedAt=started.updated_at.isoformat()))
            assert completed.raw_status == "resolved" and completed.updated_at > started.updated_at
            with pytest.raises(ApiError) as stale:
                await apply(factory, report_request(action="reject"))
            assert stale.value.status_code == 409
    run_async(scenario())
    assert snapshot(admin_pg) == ("resolved", "received", 0, 2)
    with admin_pg.engine.connect() as db:
        assert db.scalar(text("SELECT status FROM content WHERE content_id=1")) == "active"


@pytest.mark.parametrize("kind", ["report", "answer"])
def test_same_request_concurrent_calls_commit_exactly_once(admin_pg, kind):
    seed(admin_pg)
    payload = report_request(action="reject") if kind == "report" else answer_request()
    async def scenario():
        async with sessions(admin_pg) as factory:
            first, second = await asyncio.gather(apply(factory, payload), apply(factory, payload))
            assert first.audit_log_id == second.audit_log_id
            assert {first.replayed, second.replayed} == {True, False}
            assert first.updated_at == second.updated_at and first.updated_at.tzinfo
            replay = await apply(factory, payload)
            assert replay.replayed and replay.audit_log_id == first.audit_log_id
            different = payload.model_copy(update={"answer": "다른 답변"} if kind == "answer" else {"rationale": "다른 사유"})
            with pytest.raises(ApiError) as reused:
                await apply(factory, different)
            assert reused.value.status_code == 409
    run_async(scenario())
    assert snapshot(admin_pg) == (("rejected", "received", 0, 1) if kind == "report" else ("received", "answered", 1, 1))
    if kind == "answer":
        with Session(admin_pg.engine) as db:
            assert db.get(InquiryAnswer, 1).body == payload.answer
            audit = db.scalars(select(AdminActionLog)).one()
            assert payload.answer not in str(audit.before_value) + str(audit.after_value) + audit.rationale


@pytest.mark.parametrize("kind", ["report", "answer"])
def test_two_operators_cannot_overwrite_each_other(admin_pg, kind):
    seed(admin_pg)
    first = report_request(action="resolve") if kind == "report" else answer_request(answer="첫 번째 답변")
    second = report_request(action="reject") if kind == "report" else answer_request(answer="두 번째 답변")
    async def scenario():
        async with sessions(admin_pg) as factory:
            results = await asyncio.gather(apply(factory, first), apply(factory, second, uid=10), return_exceptions=True)
            successes = [r for r in results if not isinstance(r, Exception)]
            conflicts = [r for r in results if isinstance(r, ApiError)]
            assert len(successes) == len(conflicts) == 1
            assert conflicts[0].status_code == 409
    run_async(scenario())
    state = snapshot(admin_pg)
    assert state[3] == 1
    assert state[2] == (1 if kind == "answer" else 0)


@pytest.mark.parametrize("kind", ["report", "answer"])
def test_audit_failure_rolls_back_both_status_and_answer(admin_pg, kind):
    seed(admin_pg)
    with admin_pg.engine.begin() as db:
        db.execute(text("CREATE FUNCTION fail_support_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$"))
        db.execute(text("CREATE TRIGGER fail_support_audit BEFORE INSERT ON admin_action_log FOR EACH ROW EXECUTE FUNCTION fail_support_audit()"))
    payload = report_request() if kind == "report" else answer_request()
    async def scenario():
        async with sessions(admin_pg) as factory:
            with pytest.raises(ApiError) as failed:
                await apply(factory, payload)
            assert failed.value.status_code == 503
            assert snapshot(admin_pg) == ("received", "received", 0, 0)
            with admin_pg.engine.begin() as db:
                db.execute(text("DROP TRIGGER fail_support_audit ON admin_action_log"))
            success = await apply(factory, payload)
            assert not success.replayed
    run_async(scenario())


@pytest.mark.parametrize("kind", ["report", "answer"])
def test_microsecond_compare_and_viewer_authorization(admin_pg, kind):
    seed(admin_pg)
    request = report_request if kind == "report" else answer_request
    async def scenario():
        async with sessions(admin_pg) as factory:
            with pytest.raises(ApiError) as stale:
                await apply(factory, request(expectedUpdatedAt=(STAMP - timedelta(microseconds=1)).isoformat()))
            assert stale.value.status_code == 409
            with pytest.raises(ApiError) as viewer:
                await apply(factory, request(), uid=9)
            assert viewer.value.status_code == 403
    run_async(scenario())
    assert snapshot(admin_pg) == ("received", "received", 0, 0)


def test_replay_requires_current_grant_and_original_actor_scope(admin_pg):
    seed(admin_pg)
    payload = answer_request()
    async def scenario():
        async with sessions(admin_pg) as factory:
            await apply(factory, payload)
            with admin_pg.engine.begin() as db:
                db.execute(text("UPDATE admin_account SET revoked_at=now() WHERE admin_account_id=108"))
            with pytest.raises(ApiError) as revoked:
                await apply(factory, payload)
            assert revoked.value.status_code == 403
            with admin_pg.engine.begin() as db:
                db.execute(text("INSERT INTO admin_account(admin_account_id,user_id,role) VALUES(208,8,'operator')"))
            with pytest.raises(ApiError) as changed:
                await apply(factory, payload)
            assert changed.value.status_code == 409
    run_async(scenario())
    assert snapshot(admin_pg) == ("received", "answered", 1, 1)


def test_inquiry_erasure_cascades_answer_but_keeps_body_free_audit(admin_pg):
    seed(admin_pg)
    payload = answer_request(answer="ERASURE_PRIVATE_BODY_테스트")
    async def scenario():
        async with sessions(admin_pg) as factory:
            await apply(factory, payload)
    run_async(scenario())
    with admin_pg.engine.begin() as db:
        db.execute(text("DELETE FROM user_account WHERE user_id=101"))
        assert db.scalar(text("SELECT count(*) FROM inquiry")) == 0
        assert db.scalar(text("SELECT count(*) FROM inquiry_answer")) == 0
        assert db.scalar(text("SELECT count(*) FROM admin_action_log")) == 1
        row = db.execute(text("SELECT before_value,after_value,rationale FROM admin_action_log")).one()
        assert payload.answer not in str(row)
        assert db.scalar(text("SELECT user_id FROM content_report WHERE report_id=1")) is None
