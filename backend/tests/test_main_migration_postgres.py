"""Synthetic PostgreSQL rehearsal of the observed b7 -> Main service upgrade.

Use only MAIN_WITHDRAWAL_TEST_URL on a disposable loopback database named
main_withdrawal_test. No application URL, environment file, live row or external
service is used. The shared rehearsal runner must supply PostgreSQL + pgvector;
collection alone never connects. This is not a backup or a live-data rehearsal.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DataError, IntegrityError
from sqlalchemy.pool import NullPool

from app.db.migration_policy import SERVICE_SCHEMA_REVISION


BASE_REVISION = "b7d4e2a9c601"
EXPECTED_TARGET = "7f843b19debf"
Q3_RANKING_REVISION = "4dba45de1ad1"
BACKEND = Path(__file__).resolve().parents[1]
DEBATE_TABLES = {
    "debate", "debate_work", "debate_vote", "debate_vote_ticket", "debate_side",
    "debate_member", "debate_message", "debate_notification", "debate_search_log",
}
# 4dba45de1ad1 (onboarding Q3 ranking) adds one more, empty table after the debate tables.
# 7f843b19debf (debate description 100) adds no table; it only narrows debate.description.
Q3_RANKING_TABLES = {"tap_consumption_type"}
NEW_TABLES = DEBATE_TABLES | Q3_RANKING_TABLES
SEEDED_TABLES = {
    "user_account", "auth_session", "admin_account", "admin_action_log", "topic",
    "content", "content_slide", "tap", "recommendation_round", "recommendation_item",
    "user_event", "notice", "notification",
}
STAMP = datetime(2026, 11, 23, 3, 4, 5, 123456, tzinfo=timezone.utc)
SLIDE_BYTES = bytes(range(256)) * 257  # Includes NUL and non-UTF8 byte values.


def disposable_url(value):
    """Reject production, hostname, Unix-socket and query-option URLs pre-connect."""
    try:
        url = make_url(value)
        allowed = (url.drivername == "postgresql+psycopg" and url.host == "127.0.0.1"
                   and url.database == "main_withdrawal_test" and not url.query
                   and url.port is not None and 1 <= url.port <= 65535
                   and bool(url.username) and bool(url.password))
    except (ArgumentError, ValueError, TypeError):
        allowed = False
    if not allowed:
        raise ValueError("Only an explicit disposable loopback main_withdrawal_test database is accepted") from None
    return url


def relation_name(value):
    # Names below come from the isolated schema catalog, never from a URL.
    assert re.fullmatch(r"[a-z][a-z0-9_]*", value), "Unexpected rehearsal relation name"
    return '"' + value + '"'


def table_names(db, schema):
    return set(db.scalars(text(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname=:schema"
    ), {"schema": schema}))


def snapshot(db, schema, names):
    """Compare exact canonical rows, including timestamps, JSON, vectors and bytea."""
    result = {}
    for name in sorted(names):
        qualified = relation_name(schema) + "." + relation_name(name)
        rows = tuple(db.scalars(text(
            f"SELECT to_jsonb(row_value)::text FROM {qualified} AS row_value "
            "ORDER BY to_jsonb(row_value)::text COLLATE \"C\""
        )))
        payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result[name] = (rows, hashlib.sha256(payload).hexdigest())
    return result


def sequence_snapshot(db, schema, names=None):
    if names is None:
        names = tuple(db.scalars(text(
            "SELECT sequencename FROM pg_catalog.pg_sequences WHERE schemaname=:schema"
        ), {"schema": schema}))
    return {name: tuple(db.execute(text(
        "SELECT last_value, is_called FROM " + relation_name(schema) + "." + relation_name(name)
    )).one()) for name in names}


def seed_existing_rows(db):
    # Raw SQL targets the actual b7 schema, including legacy birth_date; current
    # application models cannot silently omit columns or generate fixture DDL.
    db.execute(text("""
        INSERT INTO user_account(user_id,nickname,auth_provider,provider_user_id,
            birth_date,account_status,onboarding_completed_at,withdrawn_at,withdrawal_reason)
        VALUES (701,'합성 기존 회원','kakao','migration-active',DATE '1991-02-03','active',:at,NULL,NULL),
               (702,'합성 탈퇴 회원','naver','migration-withdrawn',DATE '1992-04-05','withdrawn',:at,:at,'synthetic history')
    """), {"at": STAMP})
    db.execute(text("""
        INSERT INTO auth_session(auth_session_id,user_id,session_state,session_token_hash,
            csrf_token_hash,created_at,expires_at,revoked_at)
        VALUES (801,701,'active',:token_a,:csrf,:at,:at + INTERVAL '1 hour',NULL),
               (802,702,'active',:token_b,:csrf,:at,:at + INTERVAL '1 hour',:at)
    """), {"token_a": "a" * 64, "token_b": "b" * 64, "csrf": "c" * 64, "at": STAMP})
    db.execute(text("""
        INSERT INTO admin_account(admin_account_id,user_id,role,granted_at,revoked_at)
        VALUES (901,701,'operator',:at,NULL),(902,702,'viewer',:at,:at)
    """), {"at": STAMP})
    db.execute(text("""
        INSERT INTO admin_action_log(admin_action_log_id,admin_account_id,action_type,
            target_type,target_id,before_value,after_value,rationale,acted_at)
        VALUES (911,901,'content_approve','content',601,CAST(:before AS jsonb),
                CAST(:after AS jsonb),'합성 검토 이력',:at)
    """), {"before": '{"status":"pending_review"}', "after": '{"status":"active"}', "at": STAMP})
    db.execute(text("""
        INSERT INTO topic(topic_id,topic_code,topic_name,is_active)
        VALUES (501,'migration_synthetic','합성 분야',true)
    """))
    db.execute(text("""
        INSERT INTO content(content_id,topic_id,title,body,summary,keyword,production_type,
            published_at,judgment_status,status,embedding,feed_valid_until)
        VALUES (601,501,'보존할 합성 콘텐츠','줄바꿈\n본문','synthetic summary',
                CAST(:keywords AS jsonb),'ai',:at,'confirmed','active',
                CAST(:vector AS vector),:at + INTERVAL '30 days')
    """), {"keywords": '["보존","synthetic"]', "vector": "[0.125,-0.5,1]", "at": STAMP})
    db.execute(text("""
        INSERT INTO content_slide(content_id,page,role,eyebrow,title_lines,body_sentences,
            layout_json,image_data,image_sha256,image_width,image_height)
        VALUES (601,1,'cover','합성 카드',CAST(:title AS jsonb),CAST(:body AS jsonb),
                CAST(:layout AS jsonb),:image,:digest,1308,1632)
    """), {"title": '["보존할 제목"]', "body": '["한 문장", "두 문장"]',
            "layout": '{"version":1,"positions":[0,1]}', "image": SLIDE_BYTES,
            "digest": hashlib.sha256(SLIDE_BYTES).hexdigest()})
    db.execute(text("INSERT INTO tap(tap_id,user_id,topic_id) VALUES (1001,701,501)"))
    db.execute(text("""
        INSERT INTO recommendation_round(recommendation_round_id,tap_id,service_date,algorithm_version)
        VALUES (1101,1001,DATE '2026-11-23','synthetic-existing-v1')
    """))
    db.execute(text("""
        INSERT INTO recommendation_item(recommendation_item_id,recommendation_round_id,
            content_id,rank,score,recommend_reason,impressed_at)
        VALUES (1201,1101,601,1,0.875,CAST(:reason AS jsonb),:at)
    """), {"reason": '{"reason":"synthetic","weights":[0.25,0.75]}', "at": STAMP})
    db.execute(text("""
        INSERT INTO user_event(user_event_id,client_event_id,user_id,tap_id,event_type,
            content_id,recommendation_item_id,event_value,occurred_at,received_at,data_source)
        VALUES (1301,:event,701,1001,'open',601,1201,CAST(:value AS jsonb),:at,:at,'local_demo')
    """), {"event": UUID("00000000-0000-4000-8000-000000000001"), "value": '{"surface":"home","page":1}', "at": STAMP})
    db.execute(text("""
        INSERT INTO notice(notice_id,title,body,status,published_at,created_by_admin_id,updated_by_admin_id)
        VALUES (1401,'합성 공지','기존 수신 이력 보존','published',:at,901,901)
    """), {"at": STAMP})
    db.execute(text("""
        INSERT INTO notification(notification_id,user_id,notification_type,title,body,
            content_id,read_at,created_at,notice_id)
        VALUES (1501,701,'system_notice','합성 공지','기존 수신 이력',NULL,:at,:at,1401),
               (1502,701,'recommended_post','합성 추천','아직 읽지 않은 추천',601,NULL,:at,NULL)
    """), {"at": STAMP})


@pytest.fixture
def migration_pg():
    value = os.environ.get("MAIN_WITHDRAWAL_TEST_URL")
    if not value:
        pytest.skip("requires an explicitly selected disposable main_withdrawal_test database")
    try:
        url = disposable_url(value)
    except ValueError:
        pytest.fail("Unsafe migration rehearsal database URL rejected before connection", pytrace=False)
    # Config() has no ini/env-file input. env.py receives our explicit connection;
    # the isolated runner must also omit developer .env files from its image.
    assert not (BACKEND / ".env").exists(), "Run in the isolated image without application environment files"
    assert not (Path.cwd() / ".env").exists(), "Run in the isolated image without application environment files"
    owner = create_engine(url, poolclass=NullPool)
    schema = "main_migration_" + uuid4().hex
    engine = None
    created = False
    try:
        with owner.begin() as db:
            assert db.scalar(text("SELECT current_database()")) == "main_withdrawal_test"
            assert db.scalar(text("SELECT host(inet_server_addr())")) == "127.0.0.1"
            # A service schema in public means this is not an empty disposable DB.
            assert db.scalar(text("SELECT to_regclass('public.alembic_version')")) is None
            assert db.scalar(text("SELECT to_regclass('public.user_account')")) is None
            db.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
            db.execute(text("CREATE SCHEMA " + relation_name(schema)))
        created = True
        scoped = url.update_query_dict({
            "options": f"-csearch_path={schema},public -clock_timeout=5000 -cstatement_timeout=10000"
        })
        engine = create_engine(scoped, poolclass=NullPool)
        config = Config()
        config.set_main_option("script_location", str(BACKEND / "alembic"))
        with engine.begin() as db:
            assert db.scalar(text("SELECT current_schema()")) == schema
            config.attributes["connection"] = db
            command.upgrade(config, BASE_REVISION)
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == BASE_REVISION
            seed_existing_rows(db)
        yield SimpleNamespace(engine=engine, schema=schema, config=config)
    finally:
        if engine is not None:
            engine.dispose()
        try:
            if created:
                assert re.fullmatch(r"main_migration_[0-9a-f]{32}", schema)
                with owner.begin() as db:
                    assert db.scalar(text("SELECT current_database()")) == "main_withdrawal_test"
                    db.execute(text("DROP SCHEMA " + relation_name(schema) + " CASCADE"))
        finally:
            owner.dispose()


def test_main_upgrade_preserves_existing_data_and_enables_debate_notification(migration_pg):
    assert SERVICE_SCHEMA_REVISION == EXPECTED_TARGET, "Review a changed migration target before rehearsing"
    pg = migration_pg
    notification_sql = text("""
        INSERT INTO notification(notification_id,user_id,notification_type,title,body)
        VALUES (:notification_id,701,:kind,'합성 토론 시작','합성 알림')
    """)
    with pg.engine.begin() as db:
        before_tables = table_names(db, pg.schema) - {"alembic_version"}
        assert not before_tables & NEW_TABLES
        before = snapshot(db, pg.schema, before_tables)
        assert SEEDED_TABLES <= before_tables
        assert all(before[name][0] for name in SEEDED_TABLES), "Preservation must cover nonempty existing tables"
        sequences = sequence_snapshot(db, pg.schema)
        # Demonstrate that this exact existing CHECK constraint is widened.
        with pytest.raises(IntegrityError) as previous_rejection:
            with db.begin_nested():
                db.execute(notification_sql, {"notification_id": 1601, "kind": "debate_opened"})
        assert previous_rejection.value.orig.diag.constraint_name == "ck_notification_type"
        pg.config.attributes["connection"] = db
        command.upgrade(pg.config, SERVICE_SCHEMA_REVISION)
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == SERVICE_SCHEMA_REVISION
        assert table_names(db, pg.schema) - {"alembic_version"} == before_tables | NEW_TABLES
        after = snapshot(db, pg.schema, before_tables)
        assert after == before, "Existing table rows or canonical hashes changed during the upgrade"
        assert sequence_snapshot(db, pg.schema, sequences) == sequences
        image, stored_hash = db.execute(text(
            "SELECT image_data,image_sha256 FROM content_slide WHERE content_id=601 AND page=1"
        )).one()
        assert bytes(image) == SLIDE_BYTES
        assert hashlib.sha256(bytes(image)).hexdigest() == stored_hash
        for name in sorted(NEW_TABLES):
            assert db.scalar(text("SELECT count(*) FROM " + relation_name(name))) == 0
        db.execute(notification_sql, {"notification_id": 1601, "kind": "debate_opened"})
        assert db.scalar(text("SELECT notification_type FROM notification WHERE notification_id=1601")) == "debate_opened"
        # The new constraint still rejects unrecognized values.
        with pytest.raises(IntegrityError) as invalid_rejection:
            with db.begin_nested():
                db.execute(notification_sql, {"notification_id": 1602, "kind": "not_an_allowed_notification"})
        assert invalid_rejection.value.orig.diag.constraint_name == "ck_notification_type"


def test_debate_description_upgrade_cuts_old_long_descriptions_and_downgrade_only_widens(migration_pg):
    """2026-11-25: cards dropped their more button, so a description takes 100 characters. Rows saved under the
    old 200 rule keep their first 99 characters (trailing whitespace dropped) plus '…'."""
    pg = migration_pg
    width = text("SELECT character_maximum_length FROM information_schema.columns "
                 "WHERE table_schema=:schema AND table_name='debate' AND column_name='description'")
    rows = text("SELECT debate_id, description FROM debate ORDER BY debate_id")
    saved = {
        2001: None,
        2002: "가" * 100,                        # exactly 100: kept
        2003: "가" * 101,
        2004: "나" * 98 + " \n" + "다" * 50,     # whitespace at the cut goes before '…'
        2005: "줄바꿈\n" + "라" * 196,           # the old 200 maximum
    }
    with pg.engine.begin() as db:
        pg.config.attributes["connection"] = db
        command.upgrade(pg.config, Q3_RANKING_REVISION)
        assert db.scalar(width, {"schema": pg.schema}) == 200
        for debate_id, description in saved.items():
            db.execute(text("""
                INSERT INTO debate(debate_id,topic_id,author_user_id,title,description,side_a_label,side_b_label,voting_ends_at)
                VALUES (:debate_id,501,701,:title,:description,'합성 A','합성 B',:ends)
            """), {"debate_id": debate_id, "title": f"합성 토론 {debate_id}", "description": description,
                   "ends": STAMP})

        command.upgrade(pg.config, SERVICE_SCHEMA_REVISION)
        assert db.scalar(width, {"schema": pg.schema}) == 100
        cut = dict(db.execute(rows).all())
        assert cut == {2001: None, 2002: "가" * 100, 2003: "가" * 99 + "…", 2004: "나" * 98 + "…",
                       2005: "줄바꿈\n" + "라" * 95 + "…"}
        assert all(value is None or len(value) <= 100 for value in cut.values())
        # Other columns of the cut rows stay as they were.
        assert db.execute(text("SELECT DISTINCT side_a_label, side_b_label, status FROM debate")).all() == [("합성 A", "합성 B", "voting")]
        # The database itself now refuses a 101-character description.
        with pytest.raises(DataError):
            with db.begin_nested():
                db.execute(text("UPDATE debate SET description=:value WHERE debate_id=2001"), {"value": "가" * 101})

        command.downgrade(pg.config, Q3_RANKING_REVISION)
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == Q3_RANKING_REVISION
        assert db.scalar(width, {"schema": pg.schema}) == 200
        assert dict(db.execute(rows).all()) == cut, "Downgrade only widens the column; cut text cannot come back"
        command.upgrade(pg.config, SERVICE_SCHEMA_REVISION)
        assert dict(db.execute(rows).all()) == cut, "Running the upgrade again changes nothing"


@pytest.mark.parametrize("value", [
    "postgresql+psycopg://tester:synthetic@postgres:5432/me_nu_checking",
    "postgresql+psycopg://tester:synthetic@127.0.0.1:5432/me_nu_checking",
    "postgresql+psycopg://tester:synthetic@localhost:5432/main_withdrawal_test",
    "postgresql+psycopg://tester:synthetic@127.0.0.1:5432/main_withdrawal_test?host=postgres",
    "postgresql+psycopg://tester:synthetic@127.0.0.1:5432/main_withdrawal_test?options=-csearch_path=public",
    "postgresql://tester:synthetic@127.0.0.1:5432/main_withdrawal_test",
    "postgresql+psycopg://tester:synthetic@127.0.0.1/main_withdrawal_test",
    "not-a-database-url",
])
def test_main_migration_rejects_unsafe_database_targets_before_connecting(value):
    with pytest.raises(ValueError) as error:
        disposable_url(value)
    assert "synthetic" not in str(error.value)


def test_main_migration_accepts_explicit_disposable_url_without_connecting():
    url = disposable_url("postgresql+psycopg://tester:synthetic@127.0.0.1:5432/main_withdrawal_test")
    assert (url.host, url.port, url.database, url.query) == ("127.0.0.1", 5432, "main_withdrawal_test", {})
