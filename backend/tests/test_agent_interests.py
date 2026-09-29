from copy import deepcopy
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentRun, Content, ContentTag, Draft, Subtopic, Topic
from app.seeds.agent_contents import ImportConflict, import_articles, load_committed_articles, prepare_articles
from app.seeds.agent_interests import MAPPING_PATH, import_interests, validate_mapping
from test_agent_contents_import import AsyncSessionAdapter, PROVENANCE, counts, memory_db


@pytest.fixture
def interest_inputs():
    records = []
    work_mapping = {"version": 1, "items": {}}
    interests = {"version": 1, "basis": "팀 검토 전 임시 분류", "source_ref": PROVENANCE["git_commit"],
                 "topics": {}, "items": {}}
    for code, content_type in [("movie", "영화 리뷰"), ("music", "음악 리뷰"), ("anime", "작품리뷰")]:
        item_id = f"{code}-0123456789"
        records.append({
            "id": item_id, "topicId": f"topic-{code}", "title": f"{code} 원본 제목",
            "body": f"{code} 원본 본문", "summary": "원본 요약", "origin": "committed export",
            "agent": f"{code}-review", "agentVersion": "1.0", "subtopic": content_type,
            "sources": [{"url": f"https://example.com/{code}", "name": "원문 출처"}],
            "createdAt": "2026-09-30T09:00:00",
        })
        work_mapping["items"][item_id] = {"topic_code": code, "subtopics": [f"{code} 작품명"],
                                          "evidence": "원본의 작품명"}
        names = [content_type] + [f"관심사 {index}" for index in range(1, 7)]
        interests["topics"][code] = [{"name": name, "category": "content_type"} for name in names]
        interests["items"][item_id] = {"topic_code": code, "tags": names, "evidence": "본문 근거"}
    return prepare_articles(records, work_mapping), interests


def article_snapshot(session):
    """Include all columns, including the original import and Agent provenance."""
    return {
        model.__name__: [tuple(row) for row in session.execute(select(model.__table__)).all()]
        for model in (Content, Draft, AgentRun)
    }


@pytest.mark.parametrize("case", ["extra_id", "missing_id", "wrong_source", "wrong_topic", "empty_tags",
                                  "unknown_tag", "duplicate_tag", "missing_evidence", "six_interests",
                                  "duplicate_name", "wrong_category", "unused_interest"])
def test_mapping_rejects_incomplete_or_ambiguous_assignments(interest_inputs, case):
    articles, mapping = interest_inputs
    item = mapping["items"][articles[0].raw["id"]]
    catalog = mapping["topics"]["movie"]
    if case == "extra_id":
        mapping["items"]["movie-ffffffff00"] = deepcopy(item)
    elif case == "missing_id":
        del mapping["items"][articles[0].raw["id"]]
    elif case == "wrong_source":
        mapping["source_ref"] = "f" * 40
    elif case == "wrong_topic":
        item["topic_code"] = "music"
    elif case == "empty_tags":
        item["tags"] = []
    elif case == "unknown_tag":
        item["tags"].append("알 수 없는 관심사")
    elif case == "duplicate_tag":
        item["tags"].append(item["tags"][0])
    elif case == "missing_evidence":
        item["evidence"] = " "
    elif case == "six_interests":
        catalog.pop()
    elif case == "duplicate_name":
        catalog[1]["name"] = catalog[0]["name"]
    elif case == "wrong_category":
        catalog[0]["category"] = "work"
    elif case == "unused_interest":
        item["tags"].pop()
    with pytest.raises(ValueError):
        validate_mapping(articles, mapping, PROVENANCE)


@pytest.mark.asyncio
async def test_requires_every_original_article_to_be_imported(memory_db, interest_inputs):
    articles, mapping = interest_inputs
    with Session(memory_db) as session, session.begin():
        await import_articles(AsyncSessionAdapter(session), articles[:2], PROVENANCE, apply=True)
    with Session(memory_db) as session:
        before = counts(session)
        with pytest.raises(ImportConflict, match="Import all committed articles"):
            await import_interests(AsyncSessionAdapter(session), articles, mapping, PROVENANCE, apply=True)
        assert counts(session) == before


@pytest.mark.asyncio
async def test_dry_run_does_not_add_subjects_or_change_content(memory_db, interest_inputs):
    articles, mapping = interest_inputs
    with Session(memory_db) as session, session.begin():
        await import_articles(AsyncSessionAdapter(session), articles, PROVENANCE, apply=True)
    with Session(memory_db) as session, session.begin():
        before, snapshots = counts(session), article_snapshot(session)
        report = await import_interests(AsyncSessionAdapter(session), articles, mapping, PROVENANCE)
        assert report["mode"] == "dry_run"
        assert len(report["new_subtopics"]) == 21
        assert report["new_content_tags"] == 21
        assert counts(session) == before
        assert article_snapshot(session) == snapshots
        assert not session.new


@pytest.mark.asyncio
async def test_reuses_existing_names_preserves_article_data_and_reruns_safely(memory_db, interest_inputs):
    articles, mapping = interest_inputs
    with Session(memory_db) as session, session.begin():
        topic = Topic(topic_code="movie", topic_name="영화", is_active=True)
        session.add(topic)
        session.flush()
        session.add(Subtopic(topic_id=topic.topic_id, subtopic_name="영화 리뷰", depth_level=1))
        session.flush()
        await import_articles(AsyncSessionAdapter(session), articles, PROVENANCE, apply=True)
    with Session(memory_db) as session, session.begin():
        snapshots = article_snapshot(session)
        original_tags = {(row.content_id, row.topic_id, row.subtopic_id) for row in session.scalars(select(ContentTag))}
        first = await import_interests(AsyncSessionAdapter(session), articles, mapping, PROVENANCE, apply=True)
        assert len(first["new_subtopics"]) == 20
        assert first["new_content_tags"] == 20
        assert first["already_linked"] == 1
        assert article_snapshot(session) == snapshots
        assert session.scalar(select(func.count()).select_from(Subtopic).where(Subtopic.subtopic_name == "영화 리뷰")) == 1
        assert original_tags.issubset({(row.content_id, row.topic_id, row.subtopic_id) for row in session.scalars(select(ContentTag))})
        # Same label in multiple domains must refer to different domain-scoped rows.
        assert session.scalar(select(func.count()).select_from(Subtopic).where(Subtopic.subtopic_name == "관심사 1")) == 3
        before = counts(session)
        second = await import_interests(AsyncSessionAdapter(session), articles, mapping, PROVENANCE, apply=True)
        assert second["new_subtopics"] == []
        assert second["new_content_tags"] == 0
        assert second["already_linked"] == 21
        assert counts(session) == before
        assert article_snapshot(session) == snapshots


@pytest.mark.asyncio
async def test_ambiguous_interest_names_stop_before_any_writes(memory_db, interest_inputs):
    articles, mapping = interest_inputs
    with Session(memory_db) as session, session.begin():
        await import_articles(AsyncSessionAdapter(session), articles, PROVENANCE, apply=True)
        topic_id = session.scalar(select(Topic.topic_id).where(Topic.topic_code == "movie"))
        session.add_all([Subtopic(topic_id=topic_id, subtopic_name="관심사 1", depth_level=1) for _ in range(2)])
    with Session(memory_db) as session:
        before = counts(session)
        with pytest.raises(ImportConflict, match="Ambiguous existing interest"):
            await import_interests(AsyncSessionAdapter(session), articles, mapping, PROVENANCE, apply=True)
        assert counts(session) == before


@pytest.mark.asyncio
async def test_changed_original_fingerprint_stops_before_interest_writes(memory_db, interest_inputs):
    articles, mapping = interest_inputs
    with Session(memory_db) as session, session.begin():
        await import_articles(AsyncSessionAdapter(session), articles, PROVENANCE, apply=True)
        run = session.scalar(select(AgentRun).where(AgentRun.queue_task_id == articles[0].import_key))
        payload = deepcopy(run.input_payload)
        payload["import"]["item_sha256"] = "changed"
        run.input_payload = payload
    with Session(memory_db) as session:
        before = counts(session)
        with pytest.raises(ImportConflict, match="Imported article changed"):
            await import_interests(AsyncSessionAdapter(session), articles, mapping, PROVENANCE, apply=True)
        assert counts(session) == before


class FailingTagAdapter(AsyncSessionAdapter):
    async def flush(self):
        if any(isinstance(row, ContentTag) for row in self.session.new):
            raise RuntimeError("injected tag write failure")
        await super().flush()


@pytest.mark.asyncio
async def test_tag_failure_rolls_back_added_subtopics_too(memory_db, interest_inputs):
    articles, mapping = interest_inputs
    with Session(memory_db) as session, session.begin():
        await import_articles(AsyncSessionAdapter(session), articles, PROVENANCE, apply=True)
        before = counts(session)
    with Session(memory_db) as session:
        with pytest.raises(RuntimeError, match="injected tag write failure"), session.begin():
            await import_interests(FailingTagAdapter(session), articles, mapping, PROVENANCE, apply=True)
        assert counts(session) == before


def test_committed_interest_mapping_covers_every_article_and_frontend_catalog():
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    articles, provenance = load_committed_articles(mapping["source_ref"])
    validate_mapping(articles, mapping, provenance)
    assert len(articles) == 49
    frontend_path = MAPPING_PATH.parents[3] / "frontend/lib/onboarding/provisional-interests.json"
    frontend = json.loads(frontend_path.read_text(encoding="utf-8"))
    assert frontend == {code: [entry["name"] for entry in entries] for code, entries in mapping["topics"].items()}
