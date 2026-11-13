"""Review queue SQL + protected HTTP contract on disposable in-memory fixtures."""

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app.repositories import admin_content_repository as repo
from app.schemas.admin_content import AdminContentQuery
from app.services import admin_content_service as service
from test_admin_content import db  # noqa: F401 - reuse the isolated SQL adapter
from test_admin_content_api import api, login  # noqa: F401 - reuse auth + HTTP boundary


# Deliberately newer pending/mismatched rows and older active rows defeat a
# page-local sort. Same-time IDs verify the tie breaker in every priority group.
SETUP = [
    "DELETE FROM content",
    """INSERT INTO content (content_id, title, judgment_status, status, created_at, published_at) VALUES
        (1, '달빛 일반 대기', 'needs_review', 'pending_review', '2026-11-12 00:00:00', NULL),
        (2, '달빛 active 미검수', 'needs_review', 'active', '2026-11-01 00:00:00', NULL),
        (3, '달빛 승인 완료', 'confirmed', 'active', '2026-11-13 00:00:00', NULL),
        (4, '달빛 반려 완료', 'failed', 'hidden', '2026-11-13 00:00:00', NULL),
        (5, '달빛 대기 상태 불일치', 'confirmed', 'pending_review', '2026-11-13 00:00:00', NULL),
        (6, '달빛 판정 전', 'pending', 'pending_review', '2026-11-13 00:00:00', NULL),
        (7, '달빛 숨김 미검수', 'needs_review', 'hidden', '2026-11-13 00:00:00', NULL),
        (8, '달빛 삭제 미검수', 'needs_review', 'deleted', '2026-11-13 00:00:00', NULL),
        (9, '달빛 100%_OST active', 'needs_review', 'active', '2026-11-02 00:00:00', NULL),
        (10, '달빛 미래 발행 active', 'needs_review', 'active', '2026-11-02 00:00:00', '2099-01-01 00:00:00'),
        (11, '달빛 일반 대기 동률', 'needs_review', 'pending_review', '2026-11-12 00:00:00', NULL),
        (12, '다른 일반 대기', 'needs_review', 'pending_review', '2026-11-11 00:00:00', NULL),
        (13, '달빛 이전 숨김 미검수', 'needs_review', 'hidden', '2026-11-10 00:00:00', NULL)
    """,
    "CREATE TABLE draft (draft_id BIGINT PRIMARY KEY, title TEXT, judgment_status TEXT)",
    "INSERT INTO draft VALUES (99, '달빛 Draft 단독', 'needs_review')",
]
REVIEW_IDS = [10, 9, 2, 11, 1, 12, 8, 7, 13]
ALL_IDS = [8, 7, 6, 5, 4, 3, 11, 1, 12, 13, 10, 9, 2]
CASES = [
    ({}, REVIEW_IDS),
    ({"q": " 달빛 "}, [10, 9, 2, 11, 1, 8, 7, 13]),
    ({"q": "ost"}, [9]),
    ({"q": "%_"}, [9]),
    ({"q": "' OR 1=1 --"}, []),
    ({"q": "Draft"}, []),
    ({"judgmentStatus": "needs_review"}, REVIEW_IDS),
    ({"judgmentStatus": "confirmed"}, []),
    ({"judgmentStatus": "failed"}, []),
    ({"judgmentStatus": "pending"}, []),
    ({"status": "active"}, [10, 9, 2]),
    ({"status": "pending_review"}, [11, 1, 12]),
    ({"status": "hidden"}, [7, 13]),
    ({"status": "deleted"}, [8]),
    ({"q": "달빛", "status": "pending_review", "judgmentStatus": "needs_review"}, [11, 1]),
]


@pytest_asyncio.fixture
async def review_db(db):
    for statement in SETUP:
        await db.execute(text(statement))
    db.statements.clear()
    return db


@pytest.fixture
def review_api(api):
    with api.engine.begin() as connection:
        for statement in SETUP:
            connection.execute(text(statement))
    login(api)
    return api


@pytest.mark.asyncio
@pytest.mark.parametrize(("filters", "ids"), CASES)
async def test_review_sql_filters_total_and_every_page(review_db, filters, ids):
    seen = []
    for number in range(1, (len(ids) + 1) // 2 + 2):
        result = await service.list_contents(review_db, AdminContentQuery(mode="review", page=number, pageSize=2, **filters))
        assert result.total == len(ids)
        assert [item.content_id for item in result.items] == ids[(number - 1) * 2:number * 2]
        assert all(item.judgment_status == "needs_review" for item in result.items)
        seen.extend(item.content_id for item in result.items)
    assert seen == ids


@pytest.mark.parametrize(("filters", "ids"), CASES)
def test_review_http_filters_total_and_every_page(review_api, filters, ids):
    for number in range(1, (len(ids) + 1) // 2 + 2):
        response = review_api.client.get("/api/v1/admin/contents", params={"mode": "review", "page": number, "pageSize": 2, **filters})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == len(ids)
        assert body["page"] == number and body["pageSize"] == 2
        assert [item["contentId"] for item in body["items"]] == ids[(number - 1) * 2:number * 2]
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_all_mode_and_existing_needs_review_filter_keep_old_order(review_db):
    for mode in ({}, {"mode": "all"}):
        result = await service.list_contents(review_db, AdminContentQuery(**mode))
        assert result.total == 13
        assert [item.content_id for item in result.items] == ALL_IDS
        filtered = await service.list_contents(review_db, AdminContentQuery(judgmentStatus="needs_review", **mode))
        assert [item.content_id for item in filtered.items] == [id for id in ALL_IDS if id in REVIEW_IDS]
        assert filtered.total == 9


@pytest.mark.asyncio
async def test_postgres_queue_predicate_shared_by_count_and_page_before_offset(review_db):
    await repo.list_contents(review_db, AdminContentQuery(mode="review", q="%_", status="active", page=3, pageSize=2))
    count, page = [statement.compile(dialect=postgresql.dialect()) for statement in review_db.statements]
    assert "CASE WHEN" in str(page)
    assert "content.created_at DESC, content.content_id DESC" in str(page)
    assert "CASE WHEN" not in str(count)
    assert "needs_review" in count.params.values()
    assert "ILIKE" in str(page) and "ESCAPE '/'" in str(page)
    assert "/%/_" in count.params.values()
    for key, value in count.params.items():
        assert page.params[key] == value
    assert 4 in page.params.values()


def test_review_mode_validation_and_permission_precede_reads(api):
    path = "/api/v1/admin/contents?mode=invalid"
    assert api.client.get(path).status_code == 401
    login(api)
    api.permission.return_value = False
    assert api.client.get(path).status_code == 403
    assert api.client.get("/api/v1/admin/contents?mode=review").status_code == 403
    api.permission.return_value = True
    assert api.client.get(path).status_code == 422
    for read in api.reads:
        read.assert_not_awaited()
    with pytest.raises(ValidationError):
        AdminContentQuery(mode="invalid")


def test_review_detail_return_uses_identical_query_and_no_visibility_claim(review_api):
    path = "/api/v1/admin/contents?mode=review&q=달빛&status=active&page=2&pageSize=1"
    before = review_api.client.get(path).json()
    assert before["total"] == 3
    content_id = before["items"][0]["contentId"]
    detail = review_api.client.get(f"/api/v1/admin/contents/{content_id}")
    assert detail.status_code == 200
    assert detail.json()["judgmentStatus"] == "needs_review"
    assert review_api.client.get(path).json() == before
    future = review_api.client.get("/api/v1/admin/contents/10").json()
    assert future["status"] == "active" and future["publishedAt"].startswith("2099")
    assert set(future) == {"contentId", "title", "judgmentStatus", "status", "createdAt", "publishedAt", "updatedAt", "summary", "body", "sources"}
