from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import ApiError
from app.db.base import Base
from app.main import create_app
from app.models import Subtopic, Topic
from app.repositories import catalog_repository
from app.services import catalog_service
from app.services.cursor import encode_cursor


def test_openapi_subtopic_contract_is_flat():
    schema = create_app(enable_dev_api=False).openapi()
    operation = schema["paths"]["/api/v1/topics/{topicId}/subtopics"]["get"]
    parameter_names = {parameter["name"] for parameter in operation["parameters"]}
    assert parameter_names == {"topicId", "q", "cursor", "limit"}
    subtopic_properties = schema["components"]["schemas"]["SubtopicItem"]["properties"]
    assert set(subtopic_properties) == {"subtopicId", "topicId", "name"}


@pytest.mark.asyncio
async def test_subtopic_list_includes_existing_child_and_binds_cursor_to_filters(monkeypatch):
    db = object()
    rows = [
        SimpleNamespace(subtopic_id=11, topic_id=1, subtopic_name="영화 정보", parent_subtopic_id=None),
        SimpleNamespace(subtopic_id=12, topic_id=1, subtopic_name="영화 리뷰", parent_subtopic_id=11),
    ]

    async def fake_list_subtopics(_, topic_id, *, q, after_id, limit):
        assert topic_id == 1
        assert q == "영화"
        return [row for row in rows if after_id is None or row.subtopic_id > after_id][:limit]

    monkeypatch.setattr(catalog_service.repo, "get_active_topic", AsyncMock(return_value=object()))
    monkeypatch.setattr(catalog_service.repo, "list_subtopics", fake_list_subtopics)

    first = await catalog_service.subtopics(db, 1, q=" 영화 ", cursor=None, limit=1)
    assert first.model_dump(by_alias=True) == {
        "items": [{"subtopicId": 11, "topicId": 1, "name": "영화 정보"}],
        "nextCursor": first.next_cursor,
    }
    assert first.next_cursor is not None

    second = await catalog_service.subtopics(db, 1, q="영화", cursor=first.next_cursor, limit=1)
    assert second.model_dump(by_alias=True) == {
        "items": [{"subtopicId": 12, "topicId": 1, "name": "영화 리뷰"}],
        "nextCursor": None,
    }

    for topic_id, q in [(1, "드라마"), (2, "영화")]:
        with pytest.raises(ApiError) as exc:
            await catalog_service.subtopics(db, topic_id, q=q, cursor=first.next_cursor, limit=1)
        assert exc.value.code == "INVALID_CURSOR"

    old_hierarchical_cursor = encode_cursor(
        {"v": 1, "topicId": 1, "q": "영화", "parent": None, "after": 11}
    )
    with pytest.raises(ApiError) as exc:
        await catalog_service.subtopics(db, 1, q="영화", cursor=old_hierarchical_cursor, limit=1)
    assert exc.value.code == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_repository_flat_query_keeps_topic_search_and_cursor_filters():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Topic.__table__, Subtopic.__table__])
    with engine.begin() as connection:
        connection.execute(
            Topic.__table__.insert(),
            [
                {"topic_id": 1, "topic_code": "movie", "topic_name": "영화", "is_active": True},
                {"topic_id": 2, "topic_code": "music", "topic_name": "음악", "is_active": True},
            ],
        )
        connection.execute(
            Subtopic.__table__.insert(),
            [
                {"subtopic_id": 11, "topic_id": 1, "subtopic_name": "영화 정보"},
                {"subtopic_id": 12, "topic_id": 1, "parent_subtopic_id": 11, "subtopic_name": "영화 리뷰"},
                {"subtopic_id": 13, "topic_id": 2, "subtopic_name": "영화 음악"},
            ],
        )

        with Session(bind=connection) as session:
            class SessionAdapter:
                async def scalars(self, statement):
                    return session.scalars(statement)

            db = SessionAdapter()
            first = await catalog_repository.list_subtopics(db, 1, q="영화", after_id=None, limit=1)
            second = await catalog_repository.list_subtopics(db, 1, q="영화", after_id=11, limit=2)
            no_match = await catalog_repository.list_subtopics(db, 1, q="없음", after_id=None, limit=2)

    assert [row.subtopic_id for row in first] == [11]
    assert [row.subtopic_id for row in second] == [12]
    assert no_match == []
