"""Administrative support reads, without copying sensitive submission text into lists.

The union, filters, statistics and page execute as one PostgreSQL statement so
concurrent arrivals cannot produce a count/page mismatch. Search is restricted
to displayed summary fields; requester identity and message bodies are absent.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import String, case, cast, func, literal, or_, select, true, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import Content, ContentReport, ContentSource, Inquiry, InquiryAnswer, SourceSite, UserAccount
from app.schemas.admin_support import (
    AdminSupportContent, AdminSupportDetail, AdminSupportItem, AdminSupportPage,
    AdminSupportQuery, AdminSupportSource, AdminSupportStatistics,
)

PERIOD_DAYS = {"7d": 7, "30d": 30, "3m": 90, "6m": 180}
REASONS = {"wrong_info": "잘못된 정보", "spoiler": "스포일러", "rights": "저작권·권리 침해",
           "inappropriate": "부적절한 내용", "other": "기타"}


def _report_columns():
    return (
        ContentReport.report_id.label("record_id"), literal("report").label("record_type"),
        literal("content").label("kind"), Content.title.label("title"),
        ContentReport.reason_code.label("reason_code"),
        case(REASONS, value=ContentReport.reason_code).label("reason"),
        case({"received": "waiting", "reviewing": "reviewing", "resolved": "completed", "rejected": "completed"},
             value=ContentReport.status).label("status"),
        ContentReport.status.label("raw_status"), ContentReport.created_at.label("received_at"),
        ContentReport.updated_at.label("updated_at"),
    )


def _inquiry_columns():
    return (
        Inquiry.inquiry_id.label("record_id"), literal("inquiry").label("record_type"),
        Inquiry.inquiry_type.label("kind"), cast(literal(None), String).label("title"),
        cast(literal(None), String).label("reason_code"), cast(literal(None), String).label("reason"),
        case({"received": "waiting", "answered": "completed", "closed": "completed"},
             value=Inquiry.status).label("status"),
        Inquiry.status.label("raw_status"), Inquiry.created_at.label("received_at"), Inquiry.updated_at.label("updated_at"),
    )


def _retained_inquiry():
    # Grace-period withdrawal may leave the account physically present. Hide
    # its inquiry even if purge has not yet deleted the dependent row.
    return UserAccount.withdrawn_at.is_(None), UserAccount.account_status != "withdrawn"


def _report_select():
    return select(*_report_columns()).join(Content, Content.content_id == ContentReport.content_id)


def _inquiry_select():
    return select(*_inquiry_columns()).join(UserAccount, UserAccount.user_id == Inquiry.user_id).where(*_retained_inquiry())


def _item(row):
    fields = {name: row[name] for name in (
        "record_type", "kind", "title", "reason_code", "reason", "status", "raw_status", "received_at", "updated_at")}
    record_id = str(row["record_id"])
    return AdminSupportItem(id=("R-" if row["record_type"] == "report" else "I-") + record_id,
                            record_id=record_id, **fields)


async def list_support(db: AsyncSession, query: AdminSupportQuery, *, only_inquiries=False, now=None):
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("support clock requires a timezone")
    end = end.astimezone(timezone.utc)
    start = end - timedelta(days=PERIOD_DAYS[query.period])
    records = (_inquiry_select() if only_inquiries else union_all(_report_select(), _inquiry_select())).cte("support_records")
    conditions = [records.c.received_at >= start, records.c.received_at < end]
    if query.kind != "all":
        conditions.append(records.c.kind == query.kind)
    search = query.q.strip()
    if search:
        display_id = case((records.c.record_type == "report", "R-"), else_="I-") + cast(records.c.record_id, String)
        kind_name = case({"content": "콘텐츠 신고", "service": "서비스 문의", "bug": "버그/오류 문의"}, value=records.c.kind)
        # SQLAlchemy escapes %, _ and the escape character and still binds the
        # value. Searching a literal '%' must never become a match-all query.
        conditions.append(or_(*(column.icontains(search, autoescape=True)
                                for column in (display_id, records.c.title, records.c.reason, kind_name))))
    scope = select(records).where(*conditions).cte("support_scope")
    filtered = select(scope)
    if query.status != "all":
        filtered = filtered.where(scope.c.status == query.status)
    filtered = filtered.cte("support_filtered")
    statistics = select(
        func.count().label("all_count"),
        *(func.coalesce(func.sum(case((scope.c.status == status, 1), else_=0)), 0).label(status + "_count")
          for status in ("waiting", "reviewing", "completed")),
        select(func.count()).select_from(filtered).scalar_subquery().label("total"),
    ).select_from(scope).cte("support_statistics")
    page = select(filtered).order_by(filtered.c.received_at.desc(), filtered.c.record_type, filtered.c.record_id.desc())\
        .offset((query.page - 1) * query.page_size).limit(query.page_size).subquery("support_page")
    statement = select(statistics, page).select_from(statistics.outerjoin(page, true()))\
        .order_by(page.c.received_at.desc(), page.c.record_type, page.c.record_id.desc())
    rows = (await db.execute(statement)).mappings().all()
    totals = rows[0]  # Aggregate LEFT JOIN yields a row even for an empty page.
    return AdminSupportPage(
        items=[_item(row) for row in rows if row["record_id"] is not None], page=query.page, page_size=query.page_size,
        total=totals["total"], period_start=start, period_end=end,
        statistics=AdminSupportStatistics(**{name: totals[name + "_count"] for name in ("all", "waiting", "reviewing", "completed")}),
    )


async def get_report(db: AsyncSession, report_id: int):
    # Scalar subqueries avoid multiplying reports when a content has several
    # citations. Everything, including the body, is read in one DB snapshot.
    source_order = (ContentSource.citation_order.asc().nulls_last(), ContentSource.content_source_id)
    source_url = select(ContentSource.source_url).where(ContentSource.content_id == Content.content_id)\
        .order_by(*source_order).limit(1).correlate(Content).scalar_subquery()
    source_title = select(func.coalesce(ContentSource.source_title, SourceSite.media_name, literal("출처")))\
        .outerjoin(SourceSite, SourceSite.source_site_id == ContentSource.source_site_id)\
        .where(ContentSource.content_id == Content.content_id).order_by(*source_order).limit(1).correlate(Content).scalar_subquery()
    statement = select(
        *_report_columns(), ContentReport.detail.label("message"), Content.content_id,
        Content.summary.label("subtitle"), Content.content_type.label("category"), Content.image_url.label("cover_url"),
        Content.body, Content.production_type, source_url.label("source_url"), source_title.label("source_title"),
    ).join(Content, Content.content_id == ContentReport.content_id).where(ContentReport.report_id == report_id)
    row = (await db.execute(statement)).mappings().one_or_none()
    if row is None:
        raise ApiError(404, "ADMIN_REPORT_NOT_FOUND", "신고 내역을 찾을 수 없습니다.")
    content = AdminSupportContent(
        content_id=str(row["content_id"]), title=row["title"], subtitle=row["subtitle"], category=row["category"],
        cover_url=row["cover_url"], body=row["body"], production_type=row["production_type"],
        ai_generated=row["production_type"] == "ai",
        source=AdminSupportSource(title=row["source_title"], url=row["source_url"]) if row["source_url"] is not None else None,
    )
    return AdminSupportDetail(**_item(row).model_dump(), message=row["message"], target=row["title"], content=content)


async def get_inquiry(db: AsyncSession, inquiry_id: int):
    row = (await db.execute(select(*_inquiry_columns(), Inquiry.detail.label("message"),
        InquiryAnswer.body.label("answer"), InquiryAnswer.answered_at)
        .join(UserAccount, UserAccount.user_id == Inquiry.user_id)
        .outerjoin(InquiryAnswer, InquiryAnswer.inquiry_id == Inquiry.inquiry_id)
        .where(Inquiry.inquiry_id == inquiry_id, *_retained_inquiry()))).mappings().one_or_none()
    if row is None:
        raise ApiError(404, "ADMIN_INQUIRY_NOT_FOUND", "문의 내역을 찾을 수 없습니다.")
    availability = "available" if row["answer"] is not None else "none" if row["raw_status"] == "received" else "unavailable"
    return AdminSupportDetail(**_item(row).model_dump(), message=row["message"], target=None, content=None,
        answer=row["answer"], answered_at=row["answered_at"], answer_availability=availability)
