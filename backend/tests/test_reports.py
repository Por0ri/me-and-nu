"""콘텐츠 신고(POST /reports)와 마이페이지 문의(POST /inquiries), 그리고 탈퇴했을 때 둘의 처리.

test_oauth_postgres_flow 의 일회용 SQLite 흐름(외래 키 켬)에 글 · 신고 · 문의 표를 더해 확인합니다.
글이 공개 중인지 보는 조건(카드 수 · 승인 · 기간)은 피드 테스트가 따로 보므로, 여기선 정해 둔 글 번호만 공개로 둡니다.
"""

import asyncio
from typing import get_args

import pytest
from sqlalchemy import Integer, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import AgentRun, Content, ContentReport, CreatorChannel, Draft, Inquiry, TopicCluster, UserAccount
from app.models.report import INQUIRY_TYPES, REPORT_REASONS
from app.schemas.report import InquiryType, ReportReason
from app.services import report_service
from app.services.withdrawal_service import purge_withdrawn_accounts
from test_oauth_postgres_flow import (  # noqa: F401
    AsyncSqliteSession, onboarding_flow, pending_onboarding, postgres_flow, signup_request,
)

LEGACY_ANSWERS = {"q1": "world", "q2": "releases"}
VISIBLE = {301, 302}   # 303 은 표에는 있지만 공개 중이 아닌 글


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture
def report_flow(onboarding_flow, monkeypatch):
    flow = onboarding_flow
    with monkeypatch.context() as patch:
        # SQLite 는 INTEGER PRIMARY KEY 만 번호를 자동으로 매겨서, 표를 만들 때만 바꿈
        for column in (ContentReport.__table__.c.report_id, Inquiry.__table__.c.inquiry_id):
            patch.setattr(column, "type", Integer())
        # 외래 키를 켠 SQLite 는 글 표가 가리키는 표(초안 · 채널 · 작품 묶음 · 에이전트 실행)도 있어야 글을 넣을 수 있음
        Base.metadata.create_all(flow.engine, tables=[
            model.__table__
            for model in (AgentRun, CreatorChannel, TopicCluster, Draft, Content, ContentReport, Inquiry)
        ])
    with Session(flow.engine) as session:
        session.add_all([
            Content(content_id=content_id, topic_id=1, title=f"글 {content_id}", status="active")
            for content_id in (301, 302, 303)
        ])
        session.commit()

    async def visible(_db, content_id):
        return object() if content_id in VISIBLE else None

    monkeypatch.setattr(report_service, "get_visible_content", visible)
    return flow


def member(flow):
    """카카오로 가입하고 온보딩까지 마친 회원 → (지금 세션 CSRF 토큰, 회원 번호)."""
    csrf, _ = pending_onboarding(flow)
    assert signup_request(flow, csrf, LEGACY_ANSWERS).status_code == 201
    current = flow.client.get("/api/v1/auth/session").json()
    return current["csrfToken"], current["user"]["id"]


def report(flow, csrf, **changes):
    body = {"targetType": "content", "targetId": 301, "reasonCode": "wrong_info", **changes}
    return flow.client.post("/api/v1/reports", json=body, headers={"X-CSRF-Token": csrf})


def inquire(flow, csrf, **body):
    return flow.client.post("/api/v1/inquiries", json=body, headers={"X-CSRF-Token": csrf})


def test_schema_choices_match_the_database_checks():
    assert get_args(ReportReason) == REPORT_REASONS
    assert get_args(InquiryType) == INQUIRY_TYPES


def test_report_is_saved_once_per_member_and_post(report_flow):
    flow = report_flow
    csrf, user_id = member(flow)
    first = report(flow, csrf, detail="   ")   # 공백뿐인 내용은 저장하지 않음
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "received" and first.json()["reportId"] > 0

    again = report(flow, csrf, reasonCode="spoiler")   # 사유가 달라도 같은 글은 한 번만
    assert again.status_code == 409 and again.json()["code"] == "REPORT_DUPLICATE"
    other = report(flow, csrf, targetId=302, reasonCode="other", detail="  같은 소식이 두 번 올라왔어요  ")
    assert other.status_code == 201, other.text

    with Session(flow.engine) as session:
        rows = session.execute(select(
            ContentReport.user_id, ContentReport.content_id, ContentReport.reason_code,
            ContentReport.detail, ContentReport.status,
        ).order_by(ContentReport.content_id)).all()
    assert [tuple(row) for row in rows] == [
        (user_id, 301, "wrong_info", None, "received"),
        (user_id, 302, "other", "같은 소식이 두 번 올라왔어요", "received"),
    ]


def test_report_needs_onboarding_csrf_a_public_post_and_known_choices(report_flow):
    flow = report_flow
    assert report(flow, "").json()["code"] == "AUTH_REQUIRED"
    csrf, _ = pending_onboarding(flow)
    assert report(flow, csrf).json()["code"] == "ONBOARDING_REQUIRED"
    assert signup_request(flow, csrf, LEGACY_ANSWERS).status_code == 201
    csrf = flow.client.get("/api/v1/auth/session").json()["csrfToken"]

    assert report(flow, "wrong-token").json()["code"] == "CSRF_INVALID"
    hidden = report(flow, csrf, targetId=303)
    assert hidden.status_code == 404 and hidden.json()["code"] == "CONTENT_NOT_FOUND"
    for changes in (
        {"reasonCode": "sexual"}, {"targetType": "dm"}, {"targetId": 0},
        {"detail": "가" * 501}, {"birthDate": "2000-01-01"},
    ):
        response = report(flow, csrf, **changes)
        assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR"), changes
    with Session(flow.engine) as session:
        assert session.scalar(select(func.count()).select_from(ContentReport)) == 0


def test_inquiry_saves_trimmed_detail_and_checks_type_and_length(report_flow):
    flow = report_flow
    csrf, user_id = member(flow)
    sent = inquire(flow, csrf, inquiryType="service", detail="  애니 말고 다른 관심분야는 언제 열리나요?  ")
    assert sent.status_code == 201, sent.text
    assert sent.json()["status"] == "received" and sent.json()["inquiryId"] > 0
    assert inquire(flow, csrf, inquiryType="bug", detail="가" * 500).status_code == 201
    for body in (
        {"inquiryType": "other", "detail": "문의"}, {"inquiryType": "service", "detail": "   "},
        {"inquiryType": "service", "detail": "가" * 501}, {"inquiryType": "service"},
        {"inquiryType": "bug", "detail": "문의", "contentId": 301},
    ):
        response = inquire(flow, csrf, **body)
        assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR"), body
    assert inquire(flow, "wrong-token", inquiryType="bug", detail="문의").json()["code"] == "CSRF_INVALID"

    with Session(flow.engine) as session:
        rows = session.execute(select(
            Inquiry.user_id, Inquiry.inquiry_type, Inquiry.detail, Inquiry.status,
        ).order_by(Inquiry.inquiry_id)).all()
    assert [tuple(row) for row in rows] == [
        (user_id, "service", "애니 말고 다른 관심분야는 언제 열리나요?", "received"),
        (user_id, "bug", "가" * 500, "received"),
    ]


def test_withdrawal_keeps_reports_without_the_reporter_and_deletes_inquiries(report_flow):
    flow = report_flow
    csrf, user_id = member(flow)
    assert report(flow, csrf).status_code == 201
    assert inquire(flow, csrf, inquiryType="bug", detail="카드가 안 넘어가요").status_code == 201

    response = flow.client.delete("/api/v1/users/me", params={"mode": "grace"}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 202, response.text
    with Session(flow.engine) as session:   # 유예 동안엔 신고자 · 문의가 그대로
        assert session.scalar(select(ContentReport.user_id)) == user_id
        assert session.scalar(select(func.count()).select_from(Inquiry)) == 1

    async def delete_account():
        with Session(flow.engine) as session:
            db = AsyncSqliteSession(session, flow.failure)
            assert (await purge_withdrawn_accounts(db, apply=True, user_id=user_id))["deleted"] == 1
            await db.commit()
    asyncio.run(delete_account())
    with Session(flow.engine) as session:
        assert session.get(UserAccount, user_id) is None
        assert [tuple(row) for row in session.execute(select(ContentReport.user_id, ContentReport.content_id))] == [(None, 301)]
        assert session.scalar(select(func.count()).select_from(Inquiry)) == 0
