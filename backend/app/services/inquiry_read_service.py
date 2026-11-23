"""A member's own inquiry history, without internal admin identities or notes."""

from sqlalchemy import case, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import Inquiry, InquiryAnswer, UserAccount
from app.schemas.report import OwnInquiryDetail, OwnInquiryItem, OwnInquiryPage


def _scope(user_id: int):
    return (
        Inquiry.user_id == user_id, UserAccount.user_id == user_id,
        UserAccount.account_status == "active", UserAccount.withdrawn_at.is_(None),
    )


def _columns():
    return (
        Inquiry.inquiry_id, Inquiry.inquiry_type, Inquiry.status, Inquiry.created_at, Inquiry.updated_at,
        func.substr(Inquiry.detail, 1, 100).label("preview"),
        case((InquiryAnswer.inquiry_id.is_not(None), "available"),
             (Inquiry.status == "answered", "unavailable"), else_="none").label("answer_availability"),
    )


def _select(user_id: int):
    return select(*_columns()).select_from(Inquiry)\
        .join(UserAccount, UserAccount.user_id == Inquiry.user_id)\
        .outerjoin(InquiryAnswer, InquiryAnswer.inquiry_id == Inquiry.inquiry_id).where(*_scope(user_id))


def _item(row):
    return OwnInquiryItem(**{key: str(value) if key == "inquiry_id" else value for key, value in row.items()
        if key in OwnInquiryItem.model_fields})


async def list_own_inquiries(db: AsyncSession, *, user_id: int, page: int, page_size: int):
    scoped = _select(user_id).cte("own_inquiries")
    count = select(func.count().label("total")).select_from(scoped).cte("own_inquiry_count")
    selected = select(scoped).order_by(scoped.c.created_at.desc(), scoped.c.inquiry_id.desc())\
        .offset((page - 1) * page_size).limit(page_size).subquery("own_inquiry_page")
    rows = (await db.execute(select(count, selected).select_from(count.outerjoin(selected, true()))
        .order_by(selected.c.created_at.desc(), selected.c.inquiry_id.desc()))).mappings().all()
    return OwnInquiryPage(items=[_item(row) for row in rows if row["inquiry_id"] is not None],
                          total=rows[0]["total"], page=page, page_size=page_size)


async def get_own_inquiry(db: AsyncSession, *, user_id: int, inquiry_id: int):
    row = (await db.execute(_select(user_id).add_columns(
        Inquiry.detail, InquiryAnswer.body.label("answer"), InquiryAnswer.answered_at,
    ).where(Inquiry.inquiry_id == inquiry_id))).mappings().one_or_none()
    if row is None:
        # The same response covers missing records and another user's records.
        raise ApiError(404, "INQUIRY_NOT_FOUND", "문의 내역을 찾을 수 없습니다.")
    return OwnInquiryDetail(**_item(row).model_dump(), detail=row["detail"],
                            answer=row["answer"], answered_at=row["answered_at"])
