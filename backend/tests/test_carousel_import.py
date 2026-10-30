"""Local bundle validation and isolated service ingestion; no producer/runtime DB."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest
from sqlalchemy import BigInteger, Column, Integer, JSON, MetaData, Table, create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models import AgentRun, Content, ContentSource, Draft, Topic
from app.models.content_slide import ContentSlide
from app.seeds import carousel_contents as seed
from app.core.config import settings
from test_checking_carousel_preview_config import CHECKING_CONFIG, configure_checking

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
MODELS = (Topic, AgentRun, Draft, Content, ContentSource, ContentSlide)


class AsyncAdapter:
    def __init__(self, session, fail_card=False):
        self.session, self.fail_card = session, fail_card

    def get_bind(self):
        return self.session.get_bind()

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def execute(self, statement):
        return self.session.execute(statement)

    def add(self, row):
        self.session.add(row)

    async def flush(self):
        if self.fail_card and any(isinstance(row, ContentSlide) for row in self.session.new):
            raise RuntimeError("injected card write failure")
        self.session.flush()


@pytest.fixture
def engine():
    metadata = MetaData()
    for model in MODELS:
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
            elif isinstance(column.type, JSONB):
                column.type = JSON()
    references = {fk.target_fullname for table in list(metadata.tables.values()) for fk in table.foreign_keys}
    for reference in references:
        table_name, column_name = reference.split(".")
        if table_name not in metadata.tables:
            Table(table_name, metadata, Column(column_name, Integer, primary_key=True))
    db_engine = create_engine("sqlite:///:memory:")

    @event.listens_for(db_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(db_engine)
    with Session(db_engine) as session, session.begin():
        session.add(Topic(topic_code="anime", topic_name="애니메이션", is_active=True))
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def raw_bundle(tmp_path):
    image = BytesIO()
    Image.new("RGB", (1308, 1632), "white").save(image, format="PNG")
    binary = image.getvalue()
    digest = hashlib.sha256(binary).hexdigest()
    (tmp_path / "cards").mkdir()
    assets = []
    cards = []
    box = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    for page in range(1, 4):
        (tmp_path / "cards" / f"{page}.png").write_bytes(binary)
        assets.append({"page": page, "path": f"cards/{page}.png", "sha256": digest})
        cards.append({"page": page, "role": "cover" if page == 1 else "body", "eyebrow": "소식",
            "title_lines": [f"검증용 카드 {page}"], "body_sentences": ["실제 게시물이 아닌 테스트입니다."],
            "fact_ids": ["fact-1"], "rendered_image_key": digest,
            "layout": {"image_box": box, "copy_box": box, "text_overlay_box": box}})
    return {"schemaVersion": 1, "producer": "ani_menu_agent", "topicCode": "anime", "items": [{
        "package": {"request_id": "fixture-1", "content_type": "news", "menu_work_ids": [1],
            "feed_valid_until": (NOW + timedelta(days=2)).isoformat(),
            "purge_at": (NOW + timedelta(days=32)).isoformat(),
            "sources": [{"id": "source-1", "url": "https://example.com/official", "title": "테스트 출처", "role": "primary"}],
            "facts": [{"id": "fact-1", "statement": "격리 테스트 사실", "source_ids": ["source-1"], "verified": True}],
            "cards": cards, "detail_body": "카드 문장과 별개인 독립 상세 본문입니다.",
            "detail_fact_ids": ["fact-1"], "detail_source_ids": ["source-1"]},
        "assets": assets, "reviewEvidence": {"decision": "pass_candidate", "total": 100, "review_mode": "llm"}}]}


def prepare(tmp_path, raw, records=None):
    file = tmp_path / "bundle.json"
    file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    review_path = None
    if records is not None:
        review_path = tmp_path / "review.json"
        review_path.write_text(json.dumps({"schemaVersion": 1, "records": records}), encoding="utf-8")
    return seed.load_bundle(file, review_record_file=review_path, now=NOW)


def review(item, decision="approved", *, minutes=2):
    return {"digest": item.digest, "decision": decision, "reviewedBy": "isolated-test-reviewer",
            "reviewedAt": (NOW - timedelta(minutes=minutes)).isoformat(), "comment": "고정된 테스트 카드와 상세 본문을 검토했습니다."}


def counts(session):
    return {model.__tablename__: session.scalar(select(func.count()).select_from(model)) for model in MODELS[1:]}


def test_preserves_raw_package_and_digest_binds_expiry_and_pngs(tmp_path, raw_bundle):
    original = prepare(tmp_path, raw_bundle)
    assert original.items[0].raw.package == raw_bundle["items"][0]["package"]
    changed = deepcopy(raw_bundle)
    changed["items"][0]["package"]["feed_valid_until"] = (NOW + timedelta(days=3)).isoformat()
    assert prepare(tmp_path, changed).items[0].digest != original.items[0].digest
    changed = deepcopy(raw_bundle)
    changed["items"][0]["reviewEvidence"] = {"decision": "approved_for_feed"}
    assert prepare(tmp_path, changed).items[0].digest == original.items[0].digest


def test_producer_package_without_work_mapping_is_accepted(tmp_path, raw_bundle):
    raw_bundle["items"][0]["package"]["menu_work_ids"] = []
    assert prepare(tmp_path, raw_bundle).items[0].package.menu_work_ids == []
    del raw_bundle["items"][0]["package"]["menu_work_ids"]
    assert prepare(tmp_path, raw_bundle).items[0].package.menu_work_ids == []


def test_detail_may_include_additional_declared_source(tmp_path, raw_bundle):
    package = raw_bundle["items"][0]["package"]
    package["sources"].append({"id": "additional", "title": "보충 출처", "url": "https://example.com/additional"})
    package["detail_source_ids"].append("additional")
    assert prepare(tmp_path, raw_bundle).items[0].package.detail_source_ids == ["source-1", "additional"]
    # A declared supplementary source cannot replace a fact's actual source.
    package["detail_source_ids"] = ["additional"]
    with pytest.raises(seed.CarouselImportError):
        prepare(tmp_path, raw_bundle)


@pytest.mark.parametrize("case", ["missing_png", "bad_hash", "wrong_dimensions", "not_png", "traversal", "absolute", "duplicate_page",
    "missing_asset", "too_few_cards", "gap_pages", "unknown_fact", "unknown_source", "invalid_url", "credentials", "bad_layout",
    "empty_title", "long_title", "expired", "naive_expiry", "early_purge", "duplicate_request", "unknown_bundle_field", "bad_scale"])
def test_invalid_batch_is_rejected_before_db(tmp_path, raw_bundle, case):
    item = raw_bundle["items"][0]
    package = item["package"]
    if case == "missing_png":
        (tmp_path / "cards" / "3.png").unlink()
    elif case == "bad_hash":
        (tmp_path / "cards" / "1.png").write_bytes(b"tampered")
    elif case in {"wrong_dimensions", "not_png"}:
        image = BytesIO()
        Image.new("RGB", (12, 12) if case == "wrong_dimensions" else (1308, 1632)).save(image, format="PNG" if case == "wrong_dimensions" else "JPEG")
        binary = image.getvalue()
        (tmp_path / "cards" / "1.png").write_bytes(binary)
        item["assets"][0]["sha256"] = package["cards"][0]["rendered_image_key"] = hashlib.sha256(binary).hexdigest()
    elif case == "traversal":
        item["assets"][0]["path"] = "../outside.png"
    elif case == "absolute":
        item["assets"][0]["path"] = "C:\\outside.png"
    elif case == "duplicate_page":
        item["assets"][1]["page"] = 1
    elif case == "missing_asset":
        item["assets"].pop()
    elif case == "too_few_cards":
        package["cards"].pop()
    elif case == "gap_pages":
        package["cards"][-1]["page"] = 4
    elif case == "unknown_fact":
        package["cards"][0]["fact_ids"] = ["unknown"]
    elif case == "unknown_source":
        package["facts"][0]["source_ids"] = ["unknown"]
    elif case == "invalid_url":
        package["sources"][0]["url"] = "javascript:alert(1)"
    elif case == "credentials":
        package["sources"][0]["url"] = "https://secret:password@example.com/"
    elif case == "bad_layout":
        package["cards"][0]["layout"]["copy_box"]["x"] = 0.5
    elif case == "bad_scale":
        package["cards"][0]["layout"]["image_scale"] = 500
    elif case == "empty_title":
        package["cards"][0]["title_lines"] = [" "]
    elif case == "long_title":
        package["cards"][0]["title_lines"] = ["가" * 301]
    elif case == "expired":
        package["feed_valid_until"] = NOW.isoformat()
    elif case == "naive_expiry":
        package["feed_valid_until"] = "2026-11-01T12:00:00"
    elif case == "early_purge":
        package["purge_at"] = NOW.isoformat()
    elif case == "duplicate_request":
        raw_bundle["items"].append(deepcopy(item))
    elif case == "unknown_bundle_field":
        raw_bundle["approved"] = True
    with pytest.raises(seed.CarouselImportError):
        prepare(tmp_path, raw_bundle)


def test_symlink_is_rejected_even_when_target_is_inside_bundle(tmp_path, raw_bundle):
    linked = tmp_path / "linked.png"
    try:
        linked.symlink_to(tmp_path / "cards" / "1.png")
    except OSError:
        pytest.skip("symlink creation unavailable")
    raw_bundle["items"][0]["assets"][0]["path"] = "linked.png"
    with pytest.raises(seed.CarouselImportError):
        prepare(tmp_path, raw_bundle)


@pytest.mark.parametrize("case", ["wrong_digest", "future", "duplicate", "blank_reviewer", "blank_comment", "naive"])
def test_invalid_operator_review_is_rejected(tmp_path, raw_bundle, case):
    prepared = prepare(tmp_path, raw_bundle)
    record = review(prepared.items[0])
    records = [record]
    if case == "wrong_digest":
        record["digest"] = "0" * 64
    elif case == "future":
        record["reviewedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif case == "duplicate":
        records.append(deepcopy(record))
    elif case == "blank_reviewer":
        record["reviewedBy"] = " "
    elif case == "blank_comment":
        record["comment"] = " "
    elif case == "naive":
        record["reviewedAt"] = "2026-10-27T12:00:00"
    with pytest.raises(seed.CarouselImportError):
        prepare(tmp_path, raw_bundle, records)


@pytest.mark.asyncio
async def test_default_dry_run_is_read_only_and_producer_score_cannot_approve(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session:
        before = counts(session)
        report = await seed.import_bundle(AsyncAdapter(session), bundle, now=NOW)
        assert report["mode"] == "dry_run" and report["created"] == 1
        assert report["items"][0]["status"] == "pending_review"
        assert counts(session) == before
        assert not session.new and not session.dirty and not session.deleted


@pytest.mark.asyncio
async def test_atomic_stage_and_idempotent_retry_preserve_source_data(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        report = await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        assert report["created"] == 1
    with Session(engine) as session, session.begin():
        before = counts(session)
        report = await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        assert report["unchanged"] == 1 and counts(session) == before
        content = session.scalar(select(Content))
        assert (content.status, content.judgment_status, content.published_at) == ("pending_review", "needs_review", None)
        assert content.summary == "실제 게시물이 아닌 테스트입니다."
        assert content.image_url == f"/api/v1/contents/{content.content_id}/slides/1/image?topicId={content.topic_id}"
        assert session.scalar(select(Draft)).status == "checking"
        run = session.scalar(select(AgentRun))
        assert run.output_payload["package"] == raw_bundle["items"][0]["package"]
        assert run.output_payload["producer_review_evidence"]["total"] == 100
        assert run.status == "succeeded" and run.outcome == "on_hold"
        cards = session.scalars(select(ContentSlide).order_by(ContentSlide.page)).all()
        assert [card.page for card in cards] == [1, 2, 3]
        assert all(hashlib.sha256(card.image_data).hexdigest() == card.image_sha256 for card in cards)
        assert session.scalar(select(ContentSource)).source_url == "https://example.com/official"


@pytest.mark.asyncio
async def test_operator_approval_then_rejection_and_later_approval(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
    for decision, minutes, expected in [("approved", 3, "active"), ("rejected", 2, "hidden"), ("approved", 1, "active")]:
        reviewed = prepare(tmp_path, raw_bundle, [review(bundle.items[0], decision, minutes=minutes)])
        with Session(engine) as session, session.begin():
            report = await seed.import_bundle(AsyncAdapter(session), reviewed, apply=True, now=NOW)
            assert report["review_updated"] == 1
            assert session.scalar(select(Content)).status == expected
    with Session(engine) as session, session.begin():
        report = await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        assert report["unchanged"] == 1
        assert session.scalar(select(Content)).status == "active"
        assert session.scalar(select(Content)).judgment_status == "confirmed"
        assert session.scalar(select(Draft)).status == "published"
        assert session.scalar(select(AgentRun)).outcome == "publish_candidate"


@pytest.mark.asyncio
async def test_older_review_cannot_undo_newer_operator_decision(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    reviewed = prepare(tmp_path, raw_bundle, [review(bundle.items[0], minutes=1)])
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), reviewed, apply=True, now=NOW)
    old = prepare(tmp_path, raw_bundle, [review(bundle.items[0], "rejected", minutes=2)])
    with Session(engine) as session, pytest.raises(seed.CarouselImportError), session.begin():
        await seed.import_bundle(AsyncAdapter(session), old, apply=True, now=NOW)


@pytest.mark.asyncio
async def test_changed_digest_conflicts_before_any_batch_write(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
    changed = deepcopy(raw_bundle)
    changed["items"][0]["package"]["detail_body"] = "변경된 본문"
    new = deepcopy(raw_bundle["items"][0])
    new["package"]["request_id"] = "fixture-2"
    changed["items"].insert(0, new)
    conflict = prepare(tmp_path, changed)
    with Session(engine) as session:
        before = counts(session)
        with pytest.raises(seed.CarouselImportError):
            await seed.import_bundle(AsyncAdapter(session), conflict, apply=True, now=NOW)
        assert counts(session) == before and not session.new


@pytest.mark.asyncio
async def test_failure_rolls_back_all_service_rows(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session:
        with pytest.raises(RuntimeError), session.begin():
            await seed.import_bundle(AsyncAdapter(session, fail_card=True), bundle, apply=True, now=NOW)
        assert all(value == 0 for value in counts(session).values())


@pytest.mark.asyncio
async def test_no_asset_reread_after_validation(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    (tmp_path / "cards" / "1.png").write_bytes(b"changed after validation")
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        slide = session.get(ContentSlide, (1, 1))
        assert hashlib.sha256(slide.image_data).hexdigest() == bundle.items[0].package.cards[0].rendered_image_key


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["body", "slide_text", "png", "source", "method", "production", "missing_slide"])
async def test_operator_review_cannot_approve_mutated_service_copy(engine, tmp_path, raw_bundle, target):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
    with Session(engine) as session, session.begin():
        if target == "body":
            session.scalar(select(Content)).body = "검수 후 변경된 본문"
        elif target == "slide_text":
            session.get(ContentSlide, (1, 1)).title_lines = ["검수 후 변경된 카드"]
        elif target == "png":
            session.get(ContentSlide, (1, 1)).image_data = b"changed image bytes"
        elif target == "method":
            row = session.scalar(select(Content))
            row.ai_judgment_basis = {**row.ai_judgment_basis, "method": "another_importer"}
        elif target == "production":
            session.scalar(select(Content)).production_type = "human"
        elif target == "missing_slide":
            session.delete(session.get(ContentSlide, (1, 3)))
        else:
            session.scalar(select(ContentSource)).source_url = "https://example.com/changed"
    reviewed = prepare(tmp_path, raw_bundle, [review(bundle.items[0])])
    with Session(engine) as session, pytest.raises(seed.CarouselImportError), session.begin():
        await seed.import_bundle(AsyncAdapter(session), reviewed, apply=True, now=NOW)
    with Session(engine) as session:
        assert session.scalar(select(Content)).status == "pending_review"


@pytest.mark.asyncio
async def test_source_role_mapping_prioritizes_primary_and_preserves_citation(engine, tmp_path, raw_bundle):
    sources = raw_bundle["items"][0]["package"]["sources"]
    sources.insert(0, {"id": "editorial", "url": "https://example.com/editorial", "title": "편집 출처", "role": "editorial"})
    sources.append({"id": "citation", "url": "https://example.com/citation", "title": "인용 출처", "role": "citation"})
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        rows = session.scalars(select(ContentSource).order_by(ContentSource.citation_order)).all()
        assert [(row.source_role, row.citation_order) for row in rows] == [("primary", 1), ("material", 2), ("citation", 3)]
        assert rows[0].source_url == "https://example.com/official"
        # The same mapping must also pass retry integrity checks.
        result = await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        assert result["unchanged"] == 1


def test_missing_cover_body_uses_title_for_summary(tmp_path, raw_bundle):
    raw_bundle["items"][0]["package"]["cards"][0]["body_sentences"] = []
    bundle = prepare(tmp_path, raw_bundle)
    assert seed._cover_values(bundle.items[0].package) == ("검증용 카드 1", "검증용 카드 1")


@pytest.mark.parametrize("url,name", [("sqlite:///test", "test"), ("postgresql://u:p@localhost/postgres", "postgres"),
    ("postgresql://u:p@localhost/correct", "wrong"), ("postgresql://u:p@localhost/correct", "")])
def test_database_target_guard(url, name):
    with pytest.raises(seed.CarouselImportError):
        seed.validate_target(url, name)


def test_explicit_matching_postgresql_target():
    seed.validate_target("postgresql+psycopg://u:p@localhost/isolated", "isolated")


@pytest.mark.parametrize("url,name", [
    ("postgresql://u:p@db.example.com/checking_test", "checking_test"),
    ("postgresql://u:p@localhost/checking", "checking"),
    ("postgresql://u:p@localhost/checking_e2e?host=db.example.com", "checking_e2e"),
    ("postgresql://u:p@localhost/checking_e2e?hostaddr=10.0.0.1", "checking_e2e"),
    ("postgresql://u:p@localhost/checking_e2e", "other_e2e"),
    ("postgresql://u:p@localhost/checking_e2e", ""),
])
def test_local_preview_target_rejects_remote_non_test_and_override_targets(url, name):
    with pytest.raises(seed.CarouselImportError):
        seed.validate_target(url, name, local_test_preview=True)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_local_preview_accepts_explicit_loopback_test_database(host):
    seed.validate_target(f"postgresql+psycopg://u:p@{host}:55435/checking_live_e2e",
                         "checking_live_e2e", local_test_preview=True)


class LocalPreviewAdapter(AsyncAdapter):
    """Exercise transactional importer logic in SQLite after separately testing the URL gate."""
    def get_bind(self):
        return SimpleNamespace(dialect=self.session.get_bind().dialect,
                               url=make_url("postgresql://u:p@127.0.0.1/checking_live_e2e"))


@pytest.mark.asyncio
async def test_local_preview_is_visible_but_never_fabricates_quality_approval(engine, tmp_path, raw_bundle):
    raw_bundle["items"][0]["reviewEvidence"] = {"decision": "revise", "total": 42, "review_mode": "llm"}
    bundle = prepare(tmp_path, raw_bundle)
    arguments = {"local_test_preview": True, "expected_database": "checking_live_e2e", "now": NOW}
    with Session(engine) as session:
        before = counts(session)
        report = await seed.import_bundle(LocalPreviewAdapter(session), bundle, **arguments)
        assert report["items"][0]["status"] == "active"
        assert counts(session) == before and not session.new and not session.dirty
    with Session(engine) as session, session.begin():
        report = await seed.import_bundle(LocalPreviewAdapter(session), bundle, apply=True, **arguments)
        assert report["created"] == 1
        content = session.scalar(select(Content))
        assert (content.status, content.judgment_status) == ("active", "needs_review")
        assert content.ai_judgment_basis["operator_review"] is None
        assert content.ai_judgment_basis["producer_review_is_approval"] is False
        assert content.ai_judgment_basis["preview"] is True
        assert content.ai_judgment_basis["local_test_preview"] == {
            "database": "checking_live_e2e", "quality_approval": "not_reviewed"}
        run = session.scalar(select(AgentRun))
        assert run.output_payload["producer_review_evidence"] == raw_bundle["items"][0]["reviewEvidence"]
        assert run.outcome == "on_hold" and session.scalar(select(Draft)).status == "checking"
    with Session(engine) as session, session.begin():
        before = counts(session)
        report = await seed.import_bundle(LocalPreviewAdapter(session), bundle, apply=True, **arguments)
        assert report["unchanged"] == 1 and counts(session) == before


@pytest.mark.asyncio
async def test_local_preview_cannot_be_combined_with_operator_approval(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    approved = prepare(tmp_path, raw_bundle, [review(bundle.items[0])])
    with Session(engine) as session, pytest.raises(seed.CarouselImportError):
        await seed.import_bundle(LocalPreviewAdapter(session), approved, apply=True, now=NOW,
                                 local_test_preview=True, expected_database="checking_live_e2e")
        assert not session.new


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_preview", [True, False])
async def test_retry_cannot_silently_change_publication_scope(engine, tmp_path, raw_bundle, initial_preview):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(LocalPreviewAdapter(session), bundle, apply=True, now=NOW,
                                 local_test_preview=initial_preview, expected_database="checking_live_e2e")
    with Session(engine) as session, pytest.raises(seed.CarouselImportError), session.begin():
        await seed.import_bundle(LocalPreviewAdapter(session), bundle, apply=True, now=NOW,
                                 local_test_preview=not initial_preview, expected_database="checking_live_e2e")


class CheckingPreviewAdapter(AsyncAdapter):
    def get_bind(self):
        return SimpleNamespace(dialect=self.session.get_bind().dialect,
                               url=make_url(CHECKING_CONFIG["database_url"]))


@pytest.mark.asyncio
async def test_checking_preview_preserves_qc_is_idempotent_and_cannot_promote_scope(engine, tmp_path, raw_bundle, monkeypatch):
    configure_checking(monkeypatch, settings)
    raw_bundle["items"][0]["reviewEvidence"] = {"decision": "revise", "total": 42, "review_mode": "llm"}
    bundle = prepare(tmp_path, raw_bundle)
    arguments = {"checking_test_preview": True, "expected_database": "me_nu_checking", "now": NOW}
    with Session(engine) as session:
        before = counts(session)
        dry = await seed.import_bundle(CheckingPreviewAdapter(session), bundle, **arguments)
        assert dry["publication_scope"] == "checking_test_preview" and dry["quality_approved"] is False
        assert counts(session) == before and not session.new and not session.dirty
    with Session(engine) as session, session.begin():
        result = await seed.import_bundle(CheckingPreviewAdapter(session), bundle, apply=True, **arguments)
        assert result["created"] == 1
        content = session.scalar(select(Content))
        assert (content.status, content.judgment_status) == ("active", "needs_review")
        assert content.ai_judgment_basis["operator_review"] is None
        assert content.ai_judgment_basis["preview"] is True
        assert "local_test_preview" not in content.ai_judgment_basis
        assert content.ai_judgment_basis["checking_test_preview"] == {
            "database": "me_nu_checking", "environment": "checking", "quality_approval": "not_reviewed"}
        run = session.scalar(select(AgentRun))
        assert run.output_payload["package"] == raw_bundle["items"][0]["package"]
        assert run.output_payload["producer_review_evidence"] == raw_bundle["items"][0]["reviewEvidence"]
        assert run.outcome == "on_hold" and session.scalar(select(Draft)).status == "checking"
    with Session(engine) as session, session.begin():
        before = counts(session)
        repeat = await seed.import_bundle(CheckingPreviewAdapter(session), bundle, apply=True, **arguments)
        assert repeat["unchanged"] == 1 and counts(session) == before
    with Session(engine) as session, pytest.raises(seed.CarouselImportError), session.begin():
        await seed.import_bundle(CheckingPreviewAdapter(session), bundle, apply=True, now=NOW)
    approved = prepare(tmp_path, raw_bundle, [review(bundle.items[0])])
    with Session(engine) as session, pytest.raises(seed.CarouselImportError), session.begin():
        await seed.import_bundle(CheckingPreviewAdapter(session), approved, apply=True, **arguments)


def test_checking_import_requires_opt_in_exact_db_and_one_scope(monkeypatch):
    configure_checking(monkeypatch, settings)
    url = CHECKING_CONFIG["database_url"]
    seed.validate_target(url, "me_nu_checking", checking_test_preview=True)
    with pytest.raises(seed.CarouselImportError):
        seed.validate_target(url, "me_nu_checking", checking_test_preview=True, local_test_preview=True)
    with pytest.raises(seed.CarouselImportError):
        seed.validate_target(url.replace("@postgres", "@remote"), "me_nu_checking", checking_test_preview=True)
    monkeypatch.setattr(settings, "checking_carousel_test_preview_enabled", False)
    with pytest.raises(seed.CarouselImportError):
        seed.validate_target(url, "me_nu_checking", checking_test_preview=True)
