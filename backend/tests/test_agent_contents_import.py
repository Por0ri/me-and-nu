from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import BigInteger, Integer, JSON, MetaData, create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.models import (
    AgentRun,
    AgentRunSource,
    Content,
    ContentSource,
    ContentTag,
    Draft,
    Subtopic,
    Topic,
)
from app.seeds.agent_contents import ImportConflict, import_articles, prepare_articles


@pytest.fixture
def record():
    return {
        "id": "movie-0123456789",
        "topicId": "topic-movie",
        "title": "원본 영화 리뷰",
        "body": "첫 번째 문단.\n\n두 번째 문단.",
        "summary": "원본 요약",
        "origin": "committed export",
        "agent": "movie-review",
        "agentVersion": "1.0",
        "subtopic": "영화 리뷰",
        "sources": [{"url": "https://example.com/review", "name": "원본 출처"}],
        "createdAt": "2026-09-30T09:15:00",
    }


@pytest.fixture
def mapping(record):
    return {
        "version": 1,
        "items": {
            record["id"]: {
                "topic_code": "movie",
                "subtopics": ["테스트 영화"],
                "evidence": "원본 작품명과 본문의 주제",
            }
        },
    }


def test_prepare_rejects_duplicate_article_ids(record, mapping):
    with pytest.raises(ValueError, match="duplicate article id"):
        prepare_articles([record, deepcopy(record)], mapping)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "movie-not-a-valid-id", "article id"),
        ("topicId", "topic-music", "Topic mismatch"),
        ("body", " \n\t", "body"),
        ("subtopic", "음악 리뷰", "Unknown article type"),
        ("sources", [], "No usable source"),
        ("sources", None, "Missing sources"),
    ],
)
def test_prepare_rejects_invalid_article(record, mapping, field, value, message):
    record[field] = value
    with pytest.raises(ValueError, match=message):
        prepare_articles([record], mapping)


@pytest.mark.parametrize(
    "source",
    [
        None,
        "https://example.com/review",
        {"url": "ftp://example.com/review", "name": "source"},
        {"url": "https:///missing-host", "name": "source"},
        {"url": "https://user:password@example.com/review", "name": "source"},
        {"url": "https://example.com/bad path", "name": "source"},
        {"url": "https://example.com:99999/review", "name": "source"},
        {"url": "https://example.com/review", "name": " "},
    ],
)
def test_prepare_rejects_invalid_sources(record, mapping, source):
    record["sources"] = [source]
    with pytest.raises(ValueError):
        prepare_articles([record], mapping)


@pytest.mark.parametrize("kind", ["missing", "wrong_topic"])
def test_prepare_requires_matching_subject_mapping(record, mapping, kind):
    if kind == "missing":
        mapping["items"].clear()
    else:
        mapping["items"][record["id"]]["topic_code"] = "music"
    with pytest.raises(ValueError, match="subtopic mapping"):
        prepare_articles([record], mapping)


def test_prepare_deduplicates_sources_and_subjects_without_changing_original(record, mapping):
    record["sources"] += [
        {"url": " https://example.com/review ", "name": "duplicate name"},
        {"url": "https://example.com/context", "name": "두 번째 출처"},
    ]
    mapping["items"][record["id"]]["subtopics"] += [" 테스트 영화 "]
    original = deepcopy(record)

    article = prepare_articles([record], mapping)[0]

    assert article.sources == (
        ("https://example.com/review", "원본 출처"),
        ("https://example.com/context", "두 번째 출처"),
    )
    assert article.subtopics == ("테스트 영화",)
    assert article.content_type == "movie_review"
    assert article.raw == original
    assert record == original


@pytest.mark.parametrize("stamp", ["2026-09-30T09:15:00", "2026-09-30T09:15:00+09:00", "2026-09-30T00:15:00Z"])
def test_prepare_converts_naive_korean_and_aware_dates_to_utc(record, mapping, stamp):
    record["createdAt"] = stamp
    article = prepare_articles([record], mapping)[0]
    assert article.published_at == datetime(2026, 9, 30, 0, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize("change", ["body", "mapping"])
def test_fingerprint_detects_original_or_mapping_changes(record, mapping, change):
    before = prepare_articles([record], mapping)[0].fingerprint
    assert prepare_articles([deepcopy(record)], deepcopy(mapping))[0].fingerprint == before
    if change == "body":
        record["body"] += "\n원문 수정."
    else:
        mapping["items"][record["id"]]["subtopics"] = ["다른 영화"]
    assert prepare_articles([record], mapping)[0].fingerprint != before


class AsyncSessionAdapter:
    """Run real SQL in SQLite without requiring an extra async database driver."""

    def __init__(self, session, fail_title=None):
        self.session = session
        self.bind = session.bind
        self.fail_title = fail_title
        self.contents_before_failure = None

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    def add(self, instance):
        self.session.add(instance)

    async def flush(self):
        if any(isinstance(item, Content) and item.title == self.fail_title for item in self.session.new):
            with self.session.no_autoflush:
                self.contents_before_failure = self.session.scalar(select(func.count()).select_from(Content))
            raise RuntimeError("injected second article failure")
        self.session.flush()


@pytest.fixture
def memory_db():
    # Clone only DDL types: keep the application's ORM and schema constraints.
    # This fixture never imports the configured engine or opens the user's DB.
    metadata = MetaData()
    tables = {model.__table__ for model in IMPORTED_MODELS}
    pending = list(tables)
    while pending:
        for foreign_key in pending.pop().foreign_keys:
            parent = foreign_key.column.table
            if parent not in tables:
                tables.add(parent)
                pending.append(parent)
    for table in tables:
        cloned = table.to_metadata(metadata)
        for column in cloned.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            elif isinstance(column.type, BigInteger):
                column.type = Integer()
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


IMPORTED_MODELS = (Topic, Subtopic, AgentRun, Draft, Content, AgentRunSource, ContentSource, ContentTag)
PROVENANCE = {"git_commit": "a" * 40, "path": "agent/contents/contents.json", "manifest_sha256": "b" * 64}


def counts(session):
    return {model.__name__: session.scalar(select(func.count()).select_from(model)) for model in IMPORTED_MODELS}


@pytest.mark.asyncio
async def test_dry_run_reports_plan_without_writing(memory_db, record, mapping):
    articles = prepare_articles([record], mapping)
    with Session(memory_db) as session, session.begin():
        report = await import_articles(AsyncSessionAdapter(session), articles, PROVENANCE)
        assert report["mode"] == "dry_run"
        assert report["new_contents"] == 1
        assert report["new_topics"] == ["movie"]
        assert report["new_subtopics"] == [{"topic": "movie", "name": "테스트 영화"}]
        assert not session.new
        assert all(value == 0 for value in counts(session).values())


@pytest.mark.asyncio
async def test_import_preserves_provenance_links_and_is_idempotent(memory_db, record, mapping):
    record["sources"].append(deepcopy(record["sources"][0]))
    articles = prepare_articles([record], mapping)
    article = articles[0]
    with Session(memory_db) as session, session.begin():
        first = await import_articles(AsyncSessionAdapter(session), articles, PROVENANCE, apply=True)

    with Session(memory_db) as session, session.begin():
        before = counts(session)
        second = await import_articles(AsyncSessionAdapter(session), articles, PROVENANCE, apply=True)
        assert second["already_imported"] == 1
        assert second["new_contents"] == second["new_sources"] == 0
        assert second["new_topics"] == second["new_subtopics"] == []
        assert second["content_ids"] == first["content_ids"]
        assert counts(session) == before == {model.__name__: 1 for model in IMPORTED_MODELS}
        assert first["duplicate_source_rows_removed"] == 1

        content = session.scalar(select(Content))
        draft = session.scalar(select(Draft))
        run = session.scalar(select(AgentRun))
        source = session.scalar(select(ContentSource))
        run_source = session.scalar(select(AgentRunSource))
        tag = session.scalar(select(ContentTag))
        subject = session.scalar(select(Subtopic))
        assert content.title == record["title"]
        assert content.body == record["body"]
        assert content.summary == record["summary"]
        assert content.judgment_status == "needs_review"
        assert content.source_draft_id == draft.draft_id
        assert draft.agent_run_id == run.agent_run_id
        assert run.input_payload["record"] == record
        assert run.input_payload["import"]["item_sha256"] == article.fingerprint
        assert run.output_payload["regenerated"] is False
        assert content.ai_judgment_basis["judgment_logs_available"] is False
        assert content.ai_judgment_basis["git_commit"] == PROVENANCE["git_commit"]
        assert source.content_id == content.content_id
        assert source.agent_run_source_id == run_source.agent_run_source_id
        assert run_source.agent_run_id == run.agent_run_id
        assert source.source_role == "primary"
        assert source.citation_order == 1
        assert tag.content_id == content.content_id
        assert tag.topic_id == content.topic_id == subject.topic_id
        assert tag.subtopic_id == subject.subtopic_id


@pytest.mark.asyncio
async def test_changed_mapping_conflicts_without_overwriting_prior_import(memory_db, record, mapping):
    articles = prepare_articles([record], mapping)
    with Session(memory_db) as session, session.begin():
        await import_articles(AsyncSessionAdapter(session), articles, PROVENANCE, apply=True)

    mapping["items"][record["id"]]["subtopics"] = ["변경된 작품"]
    changed = prepare_articles([record], mapping)
    with Session(memory_db) as session:
        with pytest.raises(ImportConflict, match="Imported article changed"), session.begin():
            await import_articles(AsyncSessionAdapter(session), changed, PROVENANCE, apply=True)
        assert counts(session) == {model.__name__: 1 for model in IMPORTED_MODELS}
        assert session.scalar(select(Subtopic.subtopic_name)) == "테스트 영화"
        assert session.scalar(select(Content.body)) == record["body"]


@pytest.mark.asyncio
async def test_failure_after_first_article_rolls_back_entire_import(memory_db, record, mapping):
    second = {**deepcopy(record), "id": "movie-abcdef0123", "title": "두 번째 영화"}
    mapping["items"][second["id"]] = deepcopy(mapping["items"][record["id"]])
    articles = prepare_articles([record, second], mapping)

    with Session(memory_db) as session:
        adapter = AsyncSessionAdapter(session, fail_title=second["title"])
        with pytest.raises(RuntimeError, match="injected second article failure"), session.begin():
            await import_articles(adapter, articles, PROVENANCE, apply=True)
        assert adapter.contents_before_failure == 1

    with Session(memory_db) as session:
        assert all(value == 0 for value in counts(session).values())
