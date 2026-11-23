"""Real support SQL against isolated typed tables; no configured DB is contacted."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, Text, create_engine

from app.core.exceptions import ApiError
from app.schemas.admin_support import AdminSupportQuery
from app.services import admin_support_service as service

NOW = datetime(2026, 11, 23, 3, 4, 5, 123456, tzinfo=timezone.utc)
AT = NOW - timedelta(days=1)
BIG_ID = 9007199254740993


def support_tables():
    meta = MetaData()
    Table("user_account", meta, Column("user_id", BigInteger, primary_key=True),
          Column("account_status", String), Column("withdrawn_at", DateTime(timezone=True)))
    Table("content", meta, Column("content_id", BigInteger, primary_key=True), Column("title", String),
          Column("summary", Text), Column("body", Text), Column("image_url", String),
          Column("content_type", String), Column("production_type", String))
    Table("content_report", meta, Column("report_id", BigInteger, primary_key=True), Column("user_id", BigInteger),
          Column("content_id", BigInteger), Column("reason_code", String), Column("detail", Text),
          Column("status", String), Column("created_at", DateTime(timezone=True)), Column("updated_at", DateTime(timezone=True)))
    Table("inquiry", meta, Column("inquiry_id", BigInteger, primary_key=True), Column("user_id", BigInteger),
          Column("inquiry_type", String), Column("detail", Text), Column("status", String),
          Column("created_at", DateTime(timezone=True)), Column("updated_at", DateTime(timezone=True)))
    Table("source_site", meta, Column("source_site_id", BigInteger, primary_key=True), Column("media_name", String))
    Table("content_source", meta, Column("content_source_id", BigInteger, primary_key=True), Column("content_id", BigInteger),
          Column("source_site_id", BigInteger), Column("source_url", String), Column("source_title", String),
          Column("citation_order", BigInteger))
    return meta


class MappingRows:
    def __init__(self, rows):
        self.rows = rows
    def mappings(self):
        return self
    def all(self):
        return self.rows
    def one_or_none(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None


class ReadSession:
    def __init__(self, connection, meta):
        self.connection, self.meta, self.statements = connection, meta, []

    async def execute(self, statement):
        self.statements.append(statement)
        rows = [dict(row) for row in self.connection.execute(statement).mappings().all()]
        if self.connection.dialect.name == "sqlite":
            # SQLite loses offsets. All fixture times are explicitly UTC;
            # production code must receive actual PostgreSQL timestamptz.
            rows = [{k: v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v
                     for k, v in row.items()} for row in rows]
        return MappingRows(rows)

    def put(self, table, **values):
        self.connection.execute(self.meta.tables[table].insert().values(**values))

    def user(self, user_id=1, **changes):
        self.put("user_account", **dict(user_id=user_id, account_status="active", withdrawn_at=None) | changes)

    def content(self, content_id=101, **changes):
        self.put("content", **dict(content_id=content_id, title="달빛 OST", summary="요약", body="실제 콘텐츠 본문",
                 image_url="/covers/101.png", content_type="news", production_type="human") | changes)

    def report(self, report_id=1, **changes):
        self.put("content_report", **dict(report_id=report_id, user_id=1, content_id=101, reason_code="wrong_info",
                 detail="PRIVATE_REPORT_BODY", status="received", created_at=AT, updated_at=AT) | changes)

    def inquiry(self, inquiry_id=1, **changes):
        self.put("inquiry", **dict(inquiry_id=inquiry_id, user_id=1, inquiry_type="service", detail="PRIVATE_INQUIRY_BODY",
                 status="received", created_at=AT, updated_at=AT) | changes)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    meta = support_tables()
    meta.create_all(engine)
    with engine.connect() as connection:
        yield ReadSession(connection, meta)
    engine.dispose()


@pytest.mark.asyncio
async def test_union_is_stably_paged_and_empty_page_keeps_real_counts(db):
    db.user(); db.content()
    db.report(1); db.report(2, status="reviewing"); db.inquiry(1); db.inquiry(2, status="answered", created_at=AT-timedelta(hours=1))
    first = await service.list_support(db, AdminSupportQuery(page_size=2), now=NOW)
    second = await service.list_support(db, AdminSupportQuery(page=2, page_size=2), now=NOW)
    empty = await service.list_support(db, AdminSupportQuery(page=999, page_size=2), now=NOW)
    assert [row.id for row in first.items] == ["I-1", "R-2"]
    assert [row.id for row in second.items] == ["R-1", "I-2"]
    assert first.total == second.total == empty.total == 4 and empty.items == []
    assert first.statistics.model_dump() == {"all": 4, "waiting": 2, "reviewing": 1, "completed": 1}
    assert first.period_start == NOW-timedelta(days=7) and first.period_end == NOW
    assert len(db.statements) == 3
    assert all("support_statistics" in str(statement) for statement in db.statements)


@pytest.mark.asyncio
async def test_counts_use_period_kind_search_before_status_and_never_list_private_body(db):
    db.user(); db.content()
    db.report(1); db.report(2, status="rejected"); db.inquiry(1); db.inquiry(2, inquiry_type="bug")
    result = await service.list_support(db, AdminSupportQuery(kind="content", status="completed", q="달빛"), now=NOW)
    assert result.total == 1 and result.statistics.all == 2
    assert result.statistics.waiting == result.statistics.completed == 1
    assert result.items[0].raw_status == "rejected"
    serialized = result.model_dump_json(by_alias=True)
    assert "PRIVATE" not in serialized and "userId" not in serialized and "message" not in serialized
    assert "detail" not in str(db.statements[-1]) and "user_account.nickname" not in str(db.statements[-1])
    private_search = await service.list_support(db, AdminSupportQuery(q="PRIVATE"), now=NOW)
    assert private_search.total == private_search.statistics.all == 0


@pytest.mark.asyncio
async def test_period_is_half_open_with_all_supported_windows(db):
    db.user(); db.content()
    db.report(1, created_at=NOW-timedelta(days=7))
    db.report(2, created_at=NOW-timedelta(days=7, microseconds=1))
    db.report(3, created_at=NOW)
    db.report(4, created_at=NOW+timedelta(days=1))
    db.report(5, created_at=NOW-timedelta(days=100))
    for period, expected in (("7d", 1), ("30d", 2), ("3m", 2), ("6m", 3)):
        result = await service.list_support(db, AdminSupportQuery(period=period), now=NOW)
        assert result.total == expected


@pytest.mark.asyncio
async def test_search_wildcards_are_literal_and_large_ids_are_strings(db):
    db.user(); db.content(title="100%_달빛/OST")
    db.content(102, title="100aa달빛OST")
    db.report(BIG_ID); db.report(2, content_id=102)
    for q in ("%_", "%", "_", "/", "R-" + str(BIG_ID)):
        result = await service.list_support(db, AdminSupportQuery(q=q), now=NOW)
        assert result.total == 1 and result.items[0].id == "R-" + str(BIG_ID)
    assert result.items[0].record_id == str(BIG_ID)
    assert isinstance(result.model_dump(mode="json", by_alias=True)["items"][0]["recordId"], str)


@pytest.mark.asyncio
async def test_withdrawn_inquiries_are_hidden_while_anonymous_reports_remain(db):
    db.user(1); db.user(2, account_status="withdrawn"); db.user(3, withdrawn_at=AT)
    db.content(); db.report(1, user_id=None); db.report(2, user_id=2)
    db.inquiry(1); db.inquiry(2, user_id=2); db.inquiry(3, user_id=3)
    result = await service.list_support(db, AdminSupportQuery(page_size=10), now=NOW)
    assert {row.id for row in result.items} == {"R-1", "R-2", "I-1"}
    only = await service.list_support(db, AdminSupportQuery(), only_inquiries=True, now=NOW)
    assert [row.id for row in only.items] == ["I-1"] and only.statistics.all == 1
    for inquiry_id in (2, 3):
        with pytest.raises(ApiError) as error:
            await service.get_inquiry(db, inquiry_id)
        assert (error.value.status_code, error.value.code) == (404, "ADMIN_INQUIRY_NOT_FOUND")
    detail = await service.get_report(db, 2)
    assert "user" not in detail.model_dump_json().lower()


@pytest.mark.asyncio
async def test_all_raw_statuses_and_inquiry_answer_unavailability_are_explicit(db):
    db.user(); db.content()
    expected = {"received": "waiting", "reviewing": "reviewing", "resolved": "completed", "rejected": "completed"}
    for number, (raw, status) in enumerate(expected.items(), 1):
        db.report(number, status=raw)
        detail = await service.get_report(db, number)
        assert (detail.raw_status, detail.status) == (raw, status)
    for number, raw in enumerate(("received", "answered", "closed"), 1):
        db.inquiry(number, status=raw)
        detail = await service.get_inquiry(db, number)
        assert detail.status == ("waiting" if raw == "received" else "completed")
        assert detail.raw_status == raw and detail.answer is None and detail.answer_availability == "unavailable"
        assert detail.title is None and detail.reason is None and detail.content is None and detail.target is None
        assert detail.message == "PRIVATE_INQUIRY_BODY"


@pytest.mark.asyncio
@pytest.mark.parametrize("production_type", ["human", "ai", "hybrid"])
async def test_report_detail_reads_db_content_and_first_citation_without_faking_ai(db, production_type):
    db.user(); db.content(content_id=BIG_ID, production_type=production_type, body="<script>literal plain text</script>")
    db.report(content_id=BIG_ID)
    db.put("source_site", source_site_id=1, media_name="원문 매체")
    db.put("content_source", content_source_id=1, content_id=BIG_ID, source_url="https://example.com/other", citation_order=None)
    db.put("content_source", content_source_id=2, content_id=BIG_ID, source_site_id=1,
           source_title=None, source_url="https://example.com/primary", citation_order=1)
    result = await service.get_report(db, 1)
    assert len(db.statements) == 1
    assert result.message == "PRIVATE_REPORT_BODY" and result.target == "달빛 OST"
    assert result.content.content_id == str(BIG_ID)
    assert result.content.body == "<script>literal plain text</script>"
    assert result.content.source.model_dump() == {"title": "원문 매체", "url": "https://example.com/primary"}
    assert result.content.production_type == production_type and result.content.ai_generated == (production_type == "ai")


@pytest.mark.asyncio
async def test_missing_detail_and_empty_list_are_distinct(db):
    empty = await service.list_support(db, AdminSupportQuery(), now=NOW)
    assert empty.items == [] and empty.total == 0 and empty.statistics.model_dump() == dict(all=0, waiting=0, reviewing=0, completed=0)
    for lookup in (service.get_report, service.get_inquiry):
        with pytest.raises(ApiError) as error:
            await lookup(db, 999)
        assert error.value.status_code == 404
