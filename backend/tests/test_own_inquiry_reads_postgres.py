"""Own inquiry visibility and answer timestamps through real PostgreSQL types."""

from datetime import datetime, timezone

import pytest

from app.core.exceptions import ApiError
from app.models import AdminAccount, Inquiry, InquiryAnswer, UserAccount
from app.services import inquiry_read_service as service
from test_admin_postgres_integration import admin_pg, run_async, sessions  # noqa: F401


def test_migrated_own_inquiry_and_answer_visibility(admin_pg):
    admin_pg.migrate()
    at = datetime(2026, 11, 23, 4, 5, 6, 123456, tzinfo=timezone.utc)
    big_id = 9007199254740993
    async def verify():
        async with sessions(admin_pg) as factory, factory() as db:
            db.add_all([UserAccount(user_id=uid, nickname="합성", auth_provider="kakao", provider_user_id=f"own-inquiry-{uid}",
                onboarding_completed_at=at) for uid in (1, 2, 7)])
            await db.flush()
            db.add(AdminAccount(admin_account_id=7, user_id=7, role="operator"))
            db.add_all([Inquiry(inquiry_id=id_, user_id=owner, inquiry_type="bug", detail="합성 문의", status="answered",
                created_at=at, updated_at=at) for id_, owner in [(big_id, 1), (2, 2)]])
            await db.flush()
            db.add(InquiryAnswer(inquiry_id=big_id, admin_account_id=7, body="합성 답변", answered_at=at))
            await db.commit()
            page = await service.list_own_inquiries(db, user_id=1, page=1, page_size=10)
            assert page.total == 1 and page.items[0].inquiry_id == str(big_id)
            detail = await service.get_own_inquiry(db, user_id=1, inquiry_id=big_id)
            assert detail.answer == "합성 답변" and detail.answer_availability == "available"
            assert detail.answered_at.tzinfo is not None and detail.answered_at.microsecond == 123456
            with pytest.raises(ApiError) as missing:
                await service.get_own_inquiry(db, user_id=2, inquiry_id=big_id)
            assert missing.value.status_code == 404
            owner = await db.get(UserAccount, 1)
            owner.withdrawn_at = at
            await db.commit()
            assert (await service.list_own_inquiries(db, user_id=1, page=1, page_size=10)).total == 0
            with pytest.raises(ApiError):
                await service.get_own_inquiry(db, user_id=1, inquiry_id=big_id)
    run_async(verify())
