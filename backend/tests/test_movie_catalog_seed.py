import pytest

from app.models import SourceSite, Subtopic, Topic
from app.seeds.movie_catalog import (
    MOVIE_SOURCE_SITES,
    MOVIE_SUBTOPICS,
    MOVIE_TOPIC,
    seed_movie_catalog,
)


class FakeAsyncSession:
    def __init__(self, scalar_results):
        self.scalar_results = iter(scalar_results)
        self.added = []
        self.statements = []
        self.flush_count = 0

    async def scalar(self, statement):
        self.statements.append(statement)
        return next(self.scalar_results)

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        self.flush_count += 1
        for index, instance in enumerate(self.added, start=1):
            if isinstance(instance, Topic) and instance.topic_id is None:
                instance.topic_id = 100 + index
            elif (
                isinstance(instance, Subtopic)
                and instance.subtopic_id is None
            ):
                instance.subtopic_id = 200 + index
            elif (
                isinstance(instance, SourceSite)
                and instance.source_site_id is None
            ):
                instance.source_site_id = 300 + index


@pytest.mark.asyncio
async def test_seed_movie_catalog_creates_minimum_reference_data():
    session = FakeAsyncSession([None, None, None, None, None])

    result = await seed_movie_catalog(session)

    assert result.created_topics == 1
    assert result.created_subtopics == 2
    assert result.created_source_sites == 2
    assert result.created_total == 5
    assert session.flush_count == 5

    topic = next(item for item in session.added if isinstance(item, Topic))
    subtopics = [
        item for item in session.added if isinstance(item, Subtopic)
    ]
    source_sites = [
        item for item in session.added if isinstance(item, SourceSite)
    ]

    assert topic.topic_code == MOVIE_TOPIC["topic_code"]
    assert {item.subtopic_name for item in subtopics} == {
        values["subtopic_name"] for values in MOVIE_SUBTOPICS
    }
    assert {item.media_name for item in source_sites} == {
        values["media_name"] for values in MOVIE_SOURCE_SITES
    }
    assert all(item.topic_id == topic.topic_id for item in subtopics)
    assert all(item.topic_id == topic.topic_id for item in source_sites)


@pytest.mark.asyncio
async def test_seed_movie_catalog_reuses_existing_reference_data():
    topic = Topic(topic_id=1, **MOVIE_TOPIC)
    subtopics = [
        Subtopic(topic_id=1, parent_subtopic_id=None, **values)
        for values in MOVIE_SUBTOPICS
    ]
    source_sites = [
        SourceSite(topic_id=1, **values)
        for values in MOVIE_SOURCE_SITES
    ]
    session = FakeAsyncSession([topic, *subtopics, *source_sites])

    result = await seed_movie_catalog(session)

    assert result.created_total == 0
    assert session.added == []
    assert session.flush_count == 0
    assert len(session.statements) == 5
