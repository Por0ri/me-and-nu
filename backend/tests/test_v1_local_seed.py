import pytest

from app.models import (
    Content,
    ContentSource,
    ContentTag,
    Policy,
    SourceSite,
    Subtopic,
    Topic,
)
from app.seeds.v1_local import LOCAL_SOURCE_URL, seed_v1_local


class FakeAsyncSession:
    def __init__(self, scalar_results):
        self.scalar_results = iter(scalar_results)
        self.added = []
        self.ids = {}

    async def scalar(self, _statement):
        result = next(self.scalar_results)
        return result(self) if callable(result) else result

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        for instance in self.added:
            table = instance.__table__
            primary_key = next(iter(table.primary_key.columns)).name
            if getattr(instance, primary_key) is None:
                next_id = self.ids.get(table.name, 0) + 1
                self.ids[table.name] = next_id
                setattr(instance, primary_key, next_id)


def _added(session, model, index=0):
    return [item for item in session.added if isinstance(item, model)][index]


@pytest.mark.asyncio
async def test_v1_local_seed_is_idempotent_and_links_content_to_its_topic():
    first_session = FakeAsyncSession(
        [None] * 9
        + [
            lambda session: _added(session, Topic),
            lambda session: _added(session, Subtopic),
            None,
            None,
            None,
            None,
        ]
    )
    first = await seed_v1_local(first_session)

    assert first.created_policies == 4
    assert first.created_contents == 1
    assert first.created_content_sources == 1
    assert first.created_content_tags == 1
    assert len([item for item in first_session.added if isinstance(item, Content)]) == 1

    content = _added(first_session, Content)
    source = _added(first_session, ContentSource)
    tag = _added(first_session, ContentTag)
    assert source.content_id == content.content_id
    assert source.source_url == LOCAL_SOURCE_URL
    assert tag.content_id == content.content_id
    assert tag.topic_id == content.topic_id
    assert tag.subtopic_id == first.subtopic_id

    second_session = FakeAsyncSession(
        [
            _added(first_session, Topic),
            _added(first_session, Subtopic, 0),
            _added(first_session, Subtopic, 1),
            _added(first_session, SourceSite, 0),
            _added(first_session, SourceSite, 1),
        ]
        + [item.policy_id for item in first_session.added if isinstance(item, Policy)]
        + [
            _added(first_session, Topic),
            _added(first_session, Subtopic, 0),
            _added(first_session, SourceSite, 2),
            content,
            source.content_source_id,
            tag.content_tag_id,
        ]
    )
    second = await seed_v1_local(second_session)

    assert second.created_policies == 0
    assert second.created_contents == 0
    assert second.created_content_sources == 0
    assert second.created_content_tags == 0
    assert second.content_id == first.content_id
    assert second_session.added == []
