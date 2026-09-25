"""The publication boundary keeps review decisions and provenance visible."""

from datetime import timezone

import pytest

from app.models import AgentRun, AgentRunSource, Content, ContentSource, ContentTag, Draft, Subtopic
from app.services.movie_agent_publication import (
    PREVIEW_MARKER,
    MovieAgentPublicationError,
    publish_movie_agent_run,
)


class Rows:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class FakeAsyncSession:
    def __init__(self, scalar_results, sources=None):
        self.scalar_results = list(scalar_results)
        self.sources = sources or []
        self.added = []
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0

    async def scalar(self, statement):
        return self.scalar_results.pop(0)

    async def scalars(self, statement):
        return Rows(self.sources)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flush_count += 1
        for row in self.added:
            if isinstance(row, Content) and row.content_id is None:
                row.content_id = 100

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1

    async def refresh(self, row):
        pass

    def rows(self, row_type):
        return [row for row in self.added if isinstance(row, row_type)]


def _fixtures(*, outcome="publish_candidate", draft_status="approved", requires_review=False):
    run = AgentRun(
        agent_run_id=3,
        topic_id=1,
        agent_code="movie_review",
        agent_version="V2.0",
        status="succeeded",
        outcome=outcome,
        input_payload={},
    )
    draft = Draft(
        draft_id=7,
        agent_run_id=3,
        topic_id=1,
        production_type="ai",
        content_type="movie_review",
        title="  기생충의 공간  ",
        body="  계단을 따라 계급이 드러난다.\n  영화 본문이다.  ",
        status=draft_status,
        factcheck_result={"requires_review": requires_review, "items": []},
    )
    sources = [
        AgentRunSource(
            agent_run_source_id=11,
            agent_run_id=3,
            source_site_id=21,
            source_type="metadata",
            source_url="https://www.themoviedb.org/movie/496243",
            source_title="TMDB",
        ),
        AgentRunSource(
            agent_run_source_id=12,
            agent_run_id=3,
            source_site_id=22,
            source_type="context",
            source_url="https://ko.wikipedia.org/wiki/기생충_(영화)",
            source_title="한국어 위키백과",
        ),
    ]
    subtopic = Subtopic(subtopic_id=5, topic_id=1, subtopic_name="영화 리뷰")
    return run, draft, sources, subtopic


@pytest.mark.asyncio
async def test_confirmed_candidate_is_visible_with_sources_and_movie_subtopic():
    run, draft, sources, subtopic = _fixtures()
    db = FakeAsyncSession([run, draft, None, subtopic], sources)

    content = await publish_movie_agent_run(db, run.agent_run_id)

    assert content.source_draft_id == draft.draft_id
    assert content.topic_id == run.topic_id
    assert content.production_type == "ai"
    assert content.content_type == "movie_review"
    assert content.title == "기생충의 공간"
    assert content.body == "계단을 따라 계급이 드러난다.\n  영화 본문이다."
    assert content.summary == "계단을 따라 계급이 드러난다. 영화 본문이다."
    assert content.status == "active"
    assert content.judgment_status == "confirmed"
    assert content.published_at.tzinfo == timezone.utc
    assert draft.status == "published"
    assert [(row.agent_run_source_id, row.source_site_id, row.citation_order) for row in db.rows(ContentSource)] == [
        (11, 21, 1), (12, 22, 2)
    ]
    assert [(row.topic_id, row.subtopic_id) for row in db.rows(ContentTag)] == [(1, 5)]
    assert db.commit_count == 1
    assert db.rollback_count == 0


@pytest.mark.asyncio
async def test_duplicate_publish_returns_existing_content_without_new_rows():
    run, draft, _, _ = _fixtures()
    existing = Content(content_id=100, source_draft_id=draft.draft_id, topic_id=1, title="기존 글")
    db = FakeAsyncSession([run, draft, existing])

    result = await publish_movie_agent_run(db, run.agent_run_id)

    assert result is existing
    assert db.added == []
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_preview_cannot_be_relabelled_as_approved_publication():
    run, draft, _, _ = _fixtures(outcome="rejected_archive", draft_status="discarded")
    existing = Content(
        content_id=100,
        source_draft_id=draft.draft_id,
        topic_id=1,
        title="검토 필요 글",
        ai_judgment_basis={"preview": True},
    )
    db = FakeAsyncSession([run, draft, existing])

    with pytest.raises(MovieAgentPublicationError) as exc:
        await publish_movie_agent_run(db, run.agent_run_id)

    assert exc.value.code == "AGENT_DRAFT_PREVIEW_ONLY"
    assert db.rollback_count == 1
    assert db.commit_count == 0


@pytest.mark.asyncio
async def test_review_required_candidate_needs_explicit_preview():
    run, draft, sources, subtopic = _fixtures(requires_review=True)
    db = FakeAsyncSession([run, draft, None])
    with pytest.raises(MovieAgentPublicationError) as exc:
        await publish_movie_agent_run(db, run.agent_run_id)
    assert exc.value.code == "AGENT_DRAFT_REVIEW_REQUIRED"
    assert db.rollback_count == 1
    assert db.rows(Content) == []

    preview_db = FakeAsyncSession([run, draft, None, subtopic], sources)
    content = await publish_movie_agent_run(preview_db, run.agent_run_id, force_preview=True)
    assert content.judgment_status == "needs_review"
    assert content.summary.startswith(PREVIEW_MARKER)
    assert content.ai_judgment_basis["preview"] is True
    assert draft.status == "approved"


@pytest.mark.asyncio
async def test_rejected_archive_is_only_publishable_as_marked_preview():
    run, draft, sources, subtopic = _fixtures(outcome="rejected_archive", draft_status="discarded")
    db = FakeAsyncSession([run, draft, None])
    with pytest.raises(MovieAgentPublicationError) as exc:
        await publish_movie_agent_run(db, run.agent_run_id)
    assert exc.value.code == "AGENT_DRAFT_NOT_PUBLISHABLE"

    preview_db = FakeAsyncSession([run, draft, None, subtopic], sources)
    content = await publish_movie_agent_run(preview_db, run.agent_run_id, force_preview=True)
    assert content.status == "active"
    assert content.judgment_status == "needs_review"
    assert content.ai_judgment_basis["outcome"] == "rejected_archive"
    assert draft.status == "discarded"


@pytest.mark.asyncio
async def test_no_valid_sources_rolls_back_without_publishing():
    run, draft, sources, _ = _fixtures()
    sources[0].source_url = "javascript:alert(1)"
    sources[1].source_url = "http://"
    db = FakeAsyncSession([run, draft, None], sources)

    with pytest.raises(MovieAgentPublicationError) as exc:
        await publish_movie_agent_run(db, run.agent_run_id)

    assert exc.value.code == "AGENT_SOURCE_REQUIRED"
    assert db.rows(Content) == []
    assert draft.status == "approved"
    assert db.rollback_count == 1


@pytest.mark.asyncio
async def test_failed_run_is_not_publishable_even_with_preview_flag():
    run, draft, _, _ = _fixtures(outcome="rejected_archive", draft_status="discarded")
    run.status = "failed"
    db = FakeAsyncSession([run, draft, None])

    with pytest.raises(MovieAgentPublicationError) as exc:
        await publish_movie_agent_run(db, run.agent_run_id, force_preview=True)

    assert exc.value.code == "AGENT_RUN_NOT_PUBLISHABLE"
    assert db.rows(Content) == []
    assert db.rollback_count == 1
