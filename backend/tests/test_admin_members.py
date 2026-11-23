"""Real query execution for masking, snapshot pagination and member detail."""

from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine, event
from sqlalchemy.exc import OperationalError

from app.core.exceptions import ApiError
from app.schemas.admin_members import AdminMemberQuery
from app.services import admin_member_service as service

NOW = datetime(2026, 11, 23, 1, 2, 3, 123456, tzinfo=timezone.utc)
BIG_ID = 9007199254740993


def member_tables():
    meta, timestamp = MetaData(), DateTime(timezone=True)
    Table("user_account", meta, Column("user_id", BigInteger, primary_key=True), Column("nickname", String),
          Column("email", String), Column("auth_provider", String), Column("account_status", String),
          Column("joined_at", timestamp), Column("last_login_at", timestamp), Column("withdrawn_at", timestamp))
    Table("topic", meta, Column("topic_id", BigInteger, primary_key=True), Column("topic_name", String), Column("is_active", Boolean))
    Table("tap", meta, Column("tap_id", BigInteger, primary_key=True), Column("user_id", BigInteger),
          Column("topic_id", BigInteger), Column("deleted_at", timestamp))
    Table("content", meta, Column("content_id", BigInteger, primary_key=True), Column("topic_id", BigInteger),
          Column("status", String), Column("deleted_at", timestamp))
    Table("saved_item", meta, Column("saved_item_id", BigInteger, primary_key=True), Column("user_id", BigInteger),
          Column("tap_id", BigInteger), Column("content_id", BigInteger), Column("status", String))
    Table("content_reaction", meta, Column("content_reaction_id", BigInteger, primary_key=True), Column("user_id", BigInteger),
          Column("tap_id", BigInteger), Column("content_id", BigInteger), Column("is_liked", Boolean))
    Table("onboarding_answers", meta, Column("tap_id", BigInteger, primary_key=True), Column("schema_version", Integer), Column("consumption_type", String))
    Table("entity", meta, Column("entity_id", BigInteger, primary_key=True), Column("topic_id", BigInteger), Column("name", String))
    Table("tap_entity", meta, Column("tap_id", BigInteger), Column("entity_id", BigInteger), Column("topic_id", BigInteger))
    Table("preference_tag", meta, Column("tag_id", BigInteger, primary_key=True), Column("topic_id", BigInteger),
          Column("display_name", String), Column("kind", String))
    Table("tap_preference_tag", meta, Column("tap_id", BigInteger), Column("tag_id", BigInteger), Column("topic_id", BigInteger))
    return meta


class ReadSession:
    def __init__(self, connection, meta):
        self.connection, self.meta, self.statements = connection, meta, []

    async def execute(self, statement):
        self.statements.append(statement)
        rows = [dict(row) for row in self.connection.execute(statement).mappings()]
        if self.connection.dialect.name == "sqlite":
            # SQLite drops DateTime offsets. Fixtures use UTC; production schema
            # and PostgreSQL tests enforce actual timestamptz-aware results.
            rows = [{key: value.replace(tzinfo=timezone.utc) if isinstance(value, datetime) and value.tzinfo is None else value
                     for key, value in row.items()} for row in rows]
        class Result:
            def mappings(self): return self
            def all(self): return rows
        return Result()

    def insert(self, table, **values):
        self.connection.execute(self.meta.tables[table].insert().values(**values))


@pytest.fixture
def db():
    class JsonArray:
        def __init__(self): self.values = []
        def step(self, value): self.values.append(value)
        def finalize(self): return json.dumps(self.values)
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda connection, _: connection.create_aggregate("json_agg", 1, JsonArray))
    meta = member_tables()
    meta.create_all(engine)
    with engine.connect() as connection:
        yield ReadSession(connection, meta)
    engine.dispose()


def member(db, member_id=1, **changes):
    fields = dict(user_id=member_id, nickname="실제 회원", email="person@example.test", auth_provider="kakao",
                  account_status="active", joined_at=NOW, last_login_at=NOW, withdrawn_at=None)
    fields.update(changes)
    db.insert("user_account", **fields)


@pytest.mark.parametrize("email,expected", [(None, None), ("", None), ("bad", None),
    ("p@x.test", "****@x.test"), ("ab@x.test", "a***@x.test"), ("person@x.test", "pe***@x.test"),
    ("person@localhost", None), ("person@example.test\n", None), ("two@@x.test", None)])
def test_email_masking(email, expected):
    assert service.mask_email(email) == expected


@pytest.mark.asyncio
async def test_pagination_ties_bigint_ids_and_empty_page_share_single_snapshot(db):
    for member_id in (1, 2, BIG_ID):
        member(db, member_id)
    result = await service.list_members(db, AdminMemberQuery(page_size=2))
    assert result.total == 3 and [item.id for item in result.items] == [str(BIG_ID), "2"]
    assert len(db.statements) == 1
    result = await service.list_members(db, AdminMemberQuery(page=99, page_size=2))
    assert result.total == 3 and result.items == []
    result = await service.list_members(db, AdminMemberQuery(page_size=2, sort="oldest"))
    assert [item.id for item in result.items] == ["1", "2"]
    payload = result.model_dump(mode="json", by_alias=True)
    assert payload["items"][0]["lastLoginAt"].endswith("Z")
    assert "lastSeenAt" not in payload["items"][0]
    assert "person@example.test" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_search_is_literal_case_insensitive_and_filters_actual_enums(db):
    member(db, 1, nickname="회원 100%_\\", email="Exact@example.test", auth_provider="apple", account_status="warned")
    member(db, 2, nickname="회원 100ABC", email="other@example.test", auth_provider="local")
    result = await service.list_members(db, AdminMemberQuery(), "%_\\")
    assert [item.id for item in result.items] == ["1"]
    result = await service.list_members(db, AdminMemberQuery(status="warned", provider="apple"), "EXACT@example")
    assert result.total == 1
    result = await service.list_members(db, AdminMemberQuery(provider="naver"))
    assert result.total == 0 and result.items == []
    result = await service.list_members(db, AdminMemberQuery(), "%' OR 1=1 --")
    assert result.total == 0


@pytest.mark.asyncio
async def test_withdrawal_redacts_every_personal_field_and_cannot_be_probed_by_search(db):
    member(db, 1, nickname="secret nickname", email="private@example.test", withdrawn_at=NOW)
    member(db, 2, nickname="second secret", account_status="withdrawn")
    for search in ("secret", "private@example", "person@example"):
        assert (await service.list_members(db, AdminMemberQuery(), search)).total == 0
    assert (await service.list_members(db, AdminMemberQuery(provider="kakao"))).total == 0
    result = await service.list_members(db, AdminMemberQuery(status="withdrawn"))
    assert result.total == 2
    for item in result.items:
        assert item.nickname == "탈퇴 회원" and item.status == "withdrawn"
        assert item.masked_email is item.provider is item.domain is item.joined_at is item.last_login_at is None
    detail = await service.get_member(db, 1)
    assert detail.onboarding is detail.saved_count is detail.liked_count is detail.notes is detail.history is None


def seed_detail(db):
    member(db)
    db.insert("topic", topic_id=1, topic_name="애니메이션", is_active=True)
    db.insert("topic", topic_id=2, topic_name="비활성 분야", is_active=False)
    db.insert("tap", tap_id=1, user_id=1, topic_id=1)
    db.insert("tap", tap_id=2, user_id=1, topic_id=1, deleted_at=NOW)
    db.insert("tap", tap_id=3, user_id=1, topic_id=2)
    for cid, status, deleted in ((1, "active", None), (2, "active", None), (3, "hidden", None), (4, "active", NOW), (5, "active", None)):
        db.insert("content", content_id=cid, topic_id=1, status=status, deleted_at=deleted)
    for number, cid, tap, status in ((1, 1, 1, "kept"), (2, 2, 1, "resurface_off"), (3, 3, 1, "kept"),
                                     (4, 4, 1, "kept"), (5, 5, 1, "deleted"), (6, 5, 2, "kept"), (7, 5, 3, "kept")):
        db.insert("saved_item", saved_item_id=number, user_id=1, tap_id=tap, content_id=cid, status=status)
        db.insert("content_reaction", content_reaction_id=number, user_id=1, tap_id=tap, content_id=cid, is_liked=number != 2)
    db.insert("onboarding_answers", tap_id=1, schema_version=2, consumption_type="world")
    db.insert("onboarding_answers", tap_id=2, schema_version=2, consumption_type="news")
    db.insert("entity", entity_id=1, topic_id=1, name="선택한 실제 작품")
    db.insert("entity", entity_id=2, topic_id=1, name="삭제된 Tap의 작품")
    db.insert("tap_entity", tap_id=1, entity_id=1, topic_id=1)
    db.insert("tap_entity", tap_id=2, entity_id=2, topic_id=1)
    for tag_id, kind, label in ((1, "genre", "액션"), (2, "theme", "성장")):
        db.insert("preference_tag", tag_id=tag_id, topic_id=1, display_name=label, kind=kind)
        db.insert("tap_preference_tag", tap_id=1, tag_id=tag_id, topic_id=1)


@pytest.mark.asyncio
async def test_detail_uses_current_saved_liked_state_and_actual_onboarding_labels(db):
    seed_detail(db)
    result = await service.get_member(db, 1)
    assert len(db.statements) == 1
    assert result.domain == "애니메이션" and result.saved_count == 2 and result.liked_count == 2
    assert result.onboarding.model_dump() == dict(works=["선택한 실제 작품"], genres=["액션"], themes=["성장"],
                                                 content_types=["설정과 세계관을 파고든다"])
    assert result.notes is None and result.history is None


@pytest.mark.asyncio
async def test_missing_member_is_404_and_uncollected_onboarding_is_null(db):
    member(db)
    result = await service.get_member(db, 1)
    assert result.saved_count == result.liked_count == 0
    assert result.onboarding is None
    with pytest.raises(ApiError) as error:
        await service.get_member(db, 999)
    assert error.value.status_code == 404 and error.value.code == "MEMBER_NOT_FOUND"


@pytest.mark.asyncio
async def test_query_failures_do_not_surface_sensitive_parameters():
    class Broken:
        async def execute(self, _):
            raise OperationalError("private sql", {"email": "private@example.test"}, RuntimeError("credential"))
    with pytest.raises(ApiError) as error:
        await service.list_members(Broken(), AdminMemberQuery(), "private@example.test")
    assert error.value.status_code == 503 and "private" not in str(error.value)


def test_queries_select_only_allowed_columns_and_never_interpolate_search():
    from sqlalchemy.dialects.postgresql import dialect
    query = service.member_list_statement(AdminMemberQuery(), "private@example.test")
    compiled = query.compile(dialect=dialect())
    sql = str(compiled)
    assert "private@example.test" not in sql
    for field in ("hashed_password", "provider_user_id", "profile_image_url", "withdrawal_reason"):
        assert field not in sql
        assert field not in str(service.member_detail_statement(1).compile(dialect=dialect()))


@pytest.mark.parametrize("fields", [{"status": "dormant"}, {"status": "restricted"}, {"provider": "google"},
    {"page": 0}, {"page_size": 101}, {"sort": "last_seen"}, {"q": "private@example.test"}])
def test_invalid_filter_and_url_search_contract_rejected(fields):
    with pytest.raises(ValueError):
        AdminMemberQuery(**fields)
