"""Fixed-public-set rehearsal with real disposable SQL and no provider calls."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.models import Content
from app.repositories.carousel_repository import public_content_condition
from app.services import main_publication_transition as transition
from app.services.feed_service import content_notices

STAMP = "2026-01-01 00:00:00.000000"
ACTOR = "operator-main-preparation-2026-11-17"
IDS = [1, 2, 3]
BASIS = {"method": "carousel_import_v1", "preview": True,
    "checking_test_preview": {"database": "me_nu_checking", "environment": "checking", "quality_approval": "not_reviewed"},
    "producer_evidence": {"result": "held", "rejected_reviews": ["preserved"], "checked_at": "not supplied"},
    "operator_review": None}


class AsyncFacade:
    def __init__(self, session):
        self.session = session

    def in_transaction(self):
        return self.session.in_transaction()

    def get_bind(self):
        return self.session.get_bind()

    @asynccontextmanager
    async def begin(self):
        with self.session.begin():
            yield

    async def execute(self, query):
        return self.session.execute(query)


@pytest.fixture
def database(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "publication-test.sqlite"))
    with engine.begin() as db:
        for ddl in (
            """CREATE TABLE content (content_id BIGINT PRIMARY KEY, source_draft_id BIGINT,
            status TEXT, judgment_status TEXT, published_at TIMESTAMP, updated_at TIMESTAMP,
            deleted_at TIMESTAMP, ai_judgment_basis JSON, feed_valid_until TIMESTAMP,
            title TEXT, body TEXT, summary TEXT, image_url TEXT, topic_id BIGINT,
            content_type TEXT, production_type TEXT)""",
            """CREATE TABLE draft (draft_id BIGINT PRIMARY KEY, status TEXT, updated_at TIMESTAMP,
            factcheck_result JSON, title TEXT, body TEXT, structure_template JSON, structure_template_history JSON)""",
            """CREATE TABLE content_slide (content_id BIGINT, page INTEGER, role TEXT, eyebrow TEXT,
            title_lines JSON, body_sentences JSON, layout_json JSON, image_sha256 TEXT,
            image_width INTEGER, image_height INTEGER, image_data BLOB, PRIMARY KEY(content_id,page))""",
            """CREATE TABLE content_source (content_source_id BIGINT PRIMARY KEY, content_id BIGINT,
            agent_run_source_id BIGINT, source_site_id BIGINT, source_url TEXT, source_title TEXT,
            source_role TEXT, citation_order INTEGER, created_at TIMESTAMP)""",
            "CREATE TABLE user_account (user_id INTEGER PRIMARY KEY, nickname TEXT)",
            "CREATE TABLE recommendation_round (recommendation_round_id INTEGER PRIMARY KEY, data TEXT)",
            "CREATE TABLE recommendation_item (recommendation_item_id INTEGER PRIMARY KEY, data TEXT)",
            "INSERT INTO user_account VALUES (1,'preserved member')",
            "INSERT INTO recommendation_round VALUES (1,'preserved round')",
            "INSERT INTO recommendation_item VALUES (1,'preserved rank')",
        ):
            db.execute(text(ddl))
        for cid in range(1, 6):
            approved = cid == 3
            basis = {key: value for key, value in BASIS.items() if key not in {"preview", "checking_test_preview"}} if approved else BASIS
            values = dict(cid=cid, draft_id=cid + 100, status="pending_review" if cid == 4 else "hidden" if cid == 5 else "active",
                judgment="confirmed" if approved else "needs_review", stamp=STAMP, basis=json.dumps(basis),
                draft_status="published" if approved else "checking", template='{"version":1}', history='[{"version":0}]')
            db.execute(text("""INSERT INTO content VALUES (:cid,:draft_id,:status,:judgment,:stamp,:stamp,NULL,
                :basis,'2099-01-01 00:00:00.000000','original title','original body','summary',NULL,1,'goods','ai')"""), values)
            db.execute(text("""INSERT INTO draft VALUES (:draft_id,:draft_status,:stamp,:basis,
                'draft title','draft body',:template,:history)"""), values)
            db.execute(text("""INSERT INTO content_source VALUES (:cid,:cid,NULL,NULL,
                'https://one-piece.com/news/index.html','official','primary',1,:stamp)"""), values)
            for page in (1, 2):
                db.execute(text("""INSERT INTO content_slide VALUES (:cid,:page,'card','eyebrow',
                    '["title"]','["body"]','{}',:sha,1308,1632,X'0102')"""),
                    {"cid": cid, "page": page, "sha": "a" * 64})
    yield engine
    engine.dispose()


def snapshots(engine, ids=IDS):
    with Session(engine) as session:
        return asyncio.run(transition.snapshot_rows(AsyncFacade(session), ids))


def plan(engine, ids=IDS):
    return transition.build_plan(snapshots(engine, ids), ids, as_of=datetime.now(timezone.utc), operator_id=ACTOR)


def apply(engine, candidate):
    with Session(engine) as session:
        return asyncio.run(transition.apply_isolated_plan(AsyncFacade(session), candidate, operator_id=ACTOR))


def database_state(engine):
    names = ("content", "draft", "content_source", "content_slide", "user_account", "recommendation_round", "recommendation_item")
    with engine.connect() as db:
        return {name: [tuple(row) for row in db.execute(text("SELECT * FROM " + name + " ORDER BY 1,2"))] for name in names}


def execute(engine, sql):
    with engine.begin() as db:
        db.exec_driver_sql(sql)


def test_fixed_targets_transition_once_without_new_publications_or_review_claims(database):
    candidate = plan(database)
    before = database_state(database)
    result = apply(database, candidate)
    assert (result["transitioned"], result["already_approved"], result["replayed"]) == (2, 1, 0)
    after = database_state(database)
    for name in ("content_source", "content_slide", "user_account", "recommendation_round", "recommendation_item"):
        assert before[name] == after[name]
    assert before["content"][2:] == after["content"][2:]
    assert before["draft"][2:] == after["draft"][2:]
    rows = snapshots(database)
    for entry, row in zip(candidate["entries"][:2], rows[:2]):
        content, draft = row["content"], row["draft"]
        assert (content["status"], content["judgment_status"], draft["status"]) == ("active", "confirmed", "published")
        for metadata in (content["ai_judgment_basis"], draft["factcheck_result"]):
            assert "preview" not in metadata and "checking_test_preview" not in metadata
            assert metadata["producer_evidence"] == BASIS["producer_evidence"]
            assert metadata["operator_review"] is None
            audit, = metadata[transition.HISTORY_KEY]
            assert audit["before_value"] == {"content": entry["snapshot"]["content"], "draft": entry["snapshot"]["draft"]}
            assert audit["llm_quality_review_performed"] is False
            assert audit["approval_kind"] == "operator_bulk_publication"
            assert audit["operator_id"] == ACTOR
    with Session(database) as db:
        assert db.scalars(select(Content.content_id).where(public_content_condition()).order_by(Content.content_id)).all() == IDS
    replay = apply(database, candidate)
    assert (replay["transitioned"], replay["already_approved"], replay["replayed"]) == (0, 1, 2)
    assert database_state(database) == after


@pytest.mark.parametrize("sql", [
    "UPDATE content SET status='pending_review' WHERE content_id=1",
    "UPDATE content SET status='hidden' WHERE content_id=1",
    "UPDATE content SET deleted_at='2026-01-01' WHERE content_id=1",
    "UPDATE content SET published_at='2099-01-01' WHERE content_id=1",
    "UPDATE content SET feed_valid_until='2020-01-01' WHERE content_id=1",
    "UPDATE draft SET status='writing' WHERE draft_id=101",
    "DELETE FROM content_source WHERE content_id=1",
    "DELETE FROM content_slide WHERE content_id=1 AND page=2",
])
def test_nonpublic_or_unreviewable_rows_cannot_enter_the_fixed_plan(database, sql):
    execute(database, sql)
    with pytest.raises(transition.TransitionConflict):
        plan(database)


@pytest.mark.parametrize("sql", [
    "UPDATE content SET updated_at='2026-01-02' WHERE content_id=2",
    "UPDATE content SET title='concurrent edit without timestamp' WHERE content_id=2",
    "UPDATE draft SET structure_template='{" + '"version":2' + "}' WHERE draft_id=102",
    "UPDATE draft SET updated_at='2026-01-02' WHERE draft_id=102",
    "UPDATE content_source SET source_url='https://example.org/changed' WHERE content_id=2",
    "UPDATE content_slide SET image_sha256='" + "b" * 64 + "' WHERE content_id=2 AND page=2",
    "UPDATE content SET feed_valid_until='2020-01-01' WHERE content_id=2",
])
def test_stale_content_draft_or_material_aborts_every_target(database, sql):
    candidate = plan(database)
    execute(database, sql)
    before = database_state(database)
    with pytest.raises(transition.TransitionConflict):
        apply(database, candidate)
    assert database_state(database) == before


@pytest.mark.parametrize("phase", ["draft", "commit"])
def test_failure_rolls_back_previous_updates_and_embedded_history(database, phase):
    candidate = plan(database)
    if phase == "draft":
        execute(database, "CREATE TRIGGER deny_write BEFORE UPDATE ON draft WHEN NEW.draft_id=102 BEGIN SELECT RAISE(ABORT,'fixture'); END")
    def fail_commit(_):
        raise RuntimeError("isolated commit failure")
    before = database_state(database)
    if phase == "commit":
        event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(Exception):
            apply(database, candidate)
    finally:
        if phase == "commit":
            event.remove(Session, "before_commit", fail_commit)
    assert database_state(database) == before


def test_new_pending_row_and_unselected_hidden_row_remain_unchanged(database):
    candidate = plan(database)
    execute(database, "INSERT INTO draft SELECT 106,status,updated_at,factcheck_result,title,body,structure_template,structure_template_history FROM draft WHERE draft_id=104")
    execute(database, "INSERT INTO content SELECT 6,106,status,judgment_status,published_at,updated_at,deleted_at,ai_judgment_basis,feed_valid_until,title,body,summary,image_url,topic_id,content_type,production_type FROM content WHERE content_id=4")
    before = database_state(database)
    apply(database, candidate)
    after = database_state(database)
    assert before["content"][3:] == after["content"][3:]
    assert before["draft"][3:] == after["draft"][3:]


def test_tampered_manifest_missing_targets_and_actor_are_rejected(database):
    candidate = plan(database)
    for change in (
        lambda p: p["allowed_content_ids"].append(4),
        lambda p: p["entries"][0]["snapshot"]["content"].update(title="tampered"),
        lambda p: p.update(llm_quality_review_performed=True),
        lambda p: p.update(operator_id="someone-else"),
    ):
        modified = deepcopy(candidate)
        change(modified)
        with pytest.raises(transition.TransitionConflict):
            apply(database, modified)
    for ids in ([1, 1], [999], [True], []):
        with pytest.raises(transition.TransitionConflict):
            snapshots(database, ids)
    with Session(database) as db, pytest.raises(transition.TransitionConflict):
        asyncio.run(transition.apply_isolated_plan(AsyncFacade(db), candidate, operator_id="someone-else"))


def test_normal_publication_removes_preview_notices_but_keeps_ai_disclosure(database):
    before = snapshots(database)[0]["content"]
    assert {item["code"] for item in content_notices(SimpleNamespace(**before))} == {
        "DEV_PREVIEW", "REVIEW_REQUIRED", "AI_GENERATED",
    }
    apply(database, plan(database))
    after = snapshots(database)[0]["content"]
    assert {item["code"] for item in content_notices(SimpleNamespace(**after))} == {"AI_GENERATED"}
    assert after["ai_judgment_basis"][transition.HISTORY_KEY][-1]["before_value"]["content"]["ai_judgment_basis"]["preview"] is True


def test_replay_rejects_changed_or_missing_embedded_audit(database):
    candidate = plan(database)
    apply(database, candidate)
    execute(database, "UPDATE content SET title='edited after transition' WHERE content_id=1")
    before = database_state(database)
    with pytest.raises(transition.TransitionConflict):
        apply(database, candidate)
    assert database_state(database) == before


def test_replay_rejects_identically_tampered_audit_in_both_json_fields(database):
    candidate = plan(database)
    apply(database, candidate)
    row = snapshots(database)[0]
    for metadata in (row["content"]["ai_judgment_basis"], row["draft"]["factcheck_result"]):
        metadata[transition.HISTORY_KEY][-1]["rationale"] = "altered history"
    with database.begin() as db:
        db.execute(text("UPDATE content SET ai_judgment_basis=:value WHERE content_id=1"),
            {"value": json.dumps(row["content"]["ai_judgment_basis"])})
        db.execute(text("UPDATE draft SET factcheck_result=:value WHERE draft_id=101"),
            {"value": json.dumps(row["draft"]["factcheck_result"])})
    before = database_state(database)
    with pytest.raises(transition.TransitionConflict):
        apply(database, candidate)
    assert database_state(database) == before


def test_existing_history_is_appended_without_rewriting_prior_records(database):
    basis = BASIS | {transition.HISTORY_KEY: [{"old_event": "preserve"}]}
    with database.begin() as db:
        db.execute(text("UPDATE content SET ai_judgment_basis=:basis WHERE content_id=1"), {"basis": json.dumps(basis)})
        db.execute(text("UPDATE draft SET factcheck_result=:basis WHERE draft_id=101"), {"basis": json.dumps(basis)})
    candidate = plan(database)
    apply(database, candidate)
    row = snapshots(database)[0]
    assert row["content"]["ai_judgment_basis"][transition.HISTORY_KEY][0] == {"old_event": "preserve"}
    assert row["draft"]["factcheck_result"][transition.HISTORY_KEY][0] == {"old_event": "preserve"}
    assert apply(database, candidate)["replayed"] == 2


@pytest.mark.parametrize("url", [
    "postgresql://user:synthetic@postgres:5432/me_nu_checking",
    "postgresql://user:synthetic@localhost:5432/me_nu_checking",
    "postgresql://user:synthetic@external.example:5432/test_main_publication",
    "postgresql://user:synthetic@localhost/test_main_publication?host=external.example",
    "postgresql://user:synthetic@localhost/test_main_publication?hostaddr=52.78.2.187",
    "postgresql://user:synthetic@localhost/test_main_publication?service=production",
    "postgresql://user:synthetic@localhost/test_main_publication?host=/var/run/postgresql",
    "sqlite:////service/production.sqlite",
    "sqlite:////service/production-test.sqlite",
    "sqlite:///file:service.sqlite?mode=rw&uri=true",
])
def test_apply_guard_rejects_service_or_external_database_targets(url):
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(url=url))
    with pytest.raises(transition.TransitionConflict):
        transition._require_isolated(db)


@pytest.fixture
def checking_release_boundary(monkeypatch):
    # Synthetic IDs stand in for the private fixed set only inside this test.
    # The shipped release constant is independently pinned before substitution.
    assert transition.APPROVED_CHECKING_IDS_SHA256 == "59d795b06e5cbf43b718c820bd70cfbfcfb69e50161c96eeb482af486396dded"
    ids = list(range(1, 3531))
    ids_hash = transition.fingerprint(ids)
    monkeypatch.setattr(transition, "APPROVED_CHECKING_IDS_SHA256", ids_hash)
    candidate = dict(manifest_sha256="a" * 64, allowed_content_ids=ids, entries=[
        {"classification": "existing_checking_preview" if cid <= 3526 else "already_approved"}
        for cid in ids
    ])
    arguments = dict(expected_manifest_sha256="a" * 64, expected_ids_sha256=ids_hash,
        confirmation=transition.CHECKING_CONFIRMATION, backup_receipt={
            "verified": True, "database": "me_nu_checking", "backup_sha256": "b" * 64,
            "recovery_verified": True, "captured_before_apply": True,
            "plan_sha256": "a" * 64, "allowed_ids_sha256": ids_hash,
        })
    target = SimpleNamespace(get_bind=lambda: SimpleNamespace(
        url="postgresql+psycopg://me_nu_checking:synthetic@postgres:5432/me_nu_checking"))
    return target, candidate, arguments


def test_checking_release_requires_the_exact_fixed_scope_and_verified_receipt(checking_release_boundary):
    target, candidate, arguments = checking_release_boundary
    transition._require_checking_release(target, candidate, **arguments)


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://me_nu_checking:synthetic@localhost:5432/me_nu_checking",
    "postgresql+psycopg://me_nu_checking:synthetic@postgres:5433/me_nu_checking",
    "postgresql+psycopg://me_nu_checking:synthetic@postgres:5432/me_nu",
    "postgresql+psycopg://postgres:synthetic@postgres:5432/me_nu_checking",
    "postgresql+asyncpg://me_nu_checking:synthetic@postgres:5432/me_nu_checking",
    "postgresql+psycopg://me_nu_checking:synthetic@postgres:5432/me_nu_checking?host=external.example",
    "postgresql+psycopg://me_nu_checking:synthetic@postgres:5432/me_nu_checking?hostaddr=127.0.0.1",
    "postgresql+psycopg://me_nu_checking:synthetic@postgres:5432/me_nu_checking?service=other",
    "sqlite:///:memory:",
])
def test_checking_release_rejects_other_database_connections(checking_release_boundary, url):
    _, candidate, arguments = checking_release_boundary
    target = SimpleNamespace(get_bind=lambda: SimpleNamespace(url=url))
    with pytest.raises(transition.TransitionConflict):
        transition._require_checking_release(target, candidate, **arguments)


@pytest.mark.parametrize("field,value", [
    ("confirmation", ""), ("confirmation", "yes"),
    ("expected_manifest_sha256", "c" * 64), ("expected_manifest_sha256", None),
    ("expected_ids_sha256", "c" * 64), ("backup_receipt", None),
    ("backup_receipt", {}),
])
def test_checking_release_rejects_missing_or_mismatched_confirmation_and_hashes(checking_release_boundary, field, value):
    target, candidate, arguments = checking_release_boundary
    arguments[field] = value
    with pytest.raises(transition.TransitionConflict):
        transition._require_checking_release(target, candidate, **arguments)


@pytest.mark.parametrize("field,value", [
    ("verified", False), ("verified", 1), ("recovery_verified", False),
    ("captured_before_apply", False), ("database", "me_nu"),
    ("backup_sha256", "missing"), ("plan_sha256", "c" * 64),
    ("allowed_ids_sha256", "c" * 64),
])
def test_checking_release_rejects_unverified_or_unrelated_backup(checking_release_boundary, field, value):
    target, candidate, arguments = checking_release_boundary
    arguments["backup_receipt"][field] = value
    with pytest.raises(transition.TransitionConflict):
        transition._require_checking_release(target, candidate, **arguments)


@pytest.mark.parametrize("change", [
    lambda p: p["allowed_content_ids"].__setitem__(0, 99999),
    lambda p: p["entries"].pop(),
    lambda p: p["entries"][0].update(classification="already_approved"),
    lambda p: p["entries"][-1].update(classification="existing_checking_preview"),
])
def test_checking_release_cannot_expand_or_reclassify_the_approved_set(checking_release_boundary, change):
    target, candidate, arguments = checking_release_boundary
    change(candidate)
    with pytest.raises(transition.TransitionConflict):
        transition._require_checking_release(target, candidate, **arguments)


def test_real_plan_validation_precedes_production_writes(database):
    candidate = plan(database)
    before = database_state(database)
    candidate["manifest_sha256"] = "c" * 64
    with Session(database) as session, pytest.raises(transition.TransitionConflict):
        asyncio.run(transition.apply_checking_publication_plan(AsyncFacade(session), candidate,
            operator_id=ACTOR, expected_manifest_sha256="c" * 64,
            expected_ids_sha256=transition.APPROVED_CHECKING_IDS_SHA256,
            backup_receipt={}, confirmation=transition.CHECKING_CONFIRMATION))
    assert database_state(database) == before


@pytest.mark.parametrize("database_name,user_name", [
    ("me_nu", "me_nu_checking"), ("me_nu_checking", "wrong_user"),
])
def test_connected_identity_is_rechecked_inside_transaction(database, database_name, user_name):
    candidate = plan(database)
    before = database_state(database)
    class IdentityFacade(AsyncFacade):
        async def execute(self, query):
            if str(query).startswith("SELECT current_database()"):
                return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: dict(
                    database_name=database_name, user_name=user_name)))
            return await super().execute(query)
    with Session(database) as session, pytest.raises(transition.TransitionConflict):
        asyncio.run(transition._apply_plan(IdentityFacade(session), candidate,
            operator_id=ACTOR, verify_checking_identity=True))
    assert database_state(database) == before
