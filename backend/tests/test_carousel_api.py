"""Carousel reads against an isolated SQLite DB; no configured DB is contacted."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import Request
from PIL import Image
from sqlalchemy import delete, event, inspect, update
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_optional_auth_context
from app.core.exceptions import ApiError
from app.core.config import settings
from app.db.session import get_db
from app.main import create_app
from app.models import AgentRun, Content, ContentSlide, ContentSource, Draft, SavedItem, Tap, Topic, UserAccount
from app.repositories import bookmark_repository, carousel_repository, content_repository
from app.services import feed_service, reaction_service
from test_fe_integration_seed import AsyncAdapter, memory_db  # noqa: F401
from test_checking_carousel_preview_config import configure_checking


def png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (1308, 1632), "white").save(buffer, format="PNG")
    return buffer.getvalue()


LAYOUT = {
    "template": "split", "fit_mode": "contain_blur", "background_style": "blur",
    "image_box": {"x": 0, "y": 0, "width": 1, "height": 0.7, "internal_path": "secret"},
    "copy_box": {"x": 0.06, "y": 0.7, "width": 0.88, "height": 0.29},
    "text_overlay_box": {"x": 0.06, "y": 0.67, "width": 0.88, "height": 0.29},
    "image_scale": 1, "image_offset_y": 0, "contrast_mode": "scrim_required",
    "blur_background": False, "review_report": {"private": "secret"},
}


@pytest.fixture
def carousel_db(memory_db):
    now = datetime.now(timezone.utc)
    png = png_bytes()
    with memory_db.begin() as connection:
        connection.execute(Topic.__table__.insert(), [
            {"topic_id": 1, "topic_code": "anime", "topic_name": "애니", "is_active": True},
            {"topic_id": 2, "topic_code": "other", "topic_name": "다른 토픽", "is_active": True},
        ])
        connection.execute(UserAccount.__table__.insert(), [
            {"user_id": value, "nickname": f"사용자{value}", "auth_provider": "kakao",
             "provider_user_id": str(value), "onboarding_completed_at": now}
            for value in (1, 2)
        ])
        connection.execute(Tap.__table__.insert(), [
            {"tap_id": 1, "user_id": 1, "topic_id": 1},
            {"tap_id": 2, "user_id": 1, "topic_id": 2},
        ])
        for content_id in range(101, 114):
            topic_id = 2 if content_id == 113 else 1
            legacy = content_id in (110, 111)
            connection.execute(AgentRun.__table__.insert().values(
                agent_run_id=content_id, topic_id=topic_id, agent_code="test_import",
                agent_version="1", status="succeeded", outcome="publish_candidate", input_payload={},
            ))
            connection.execute(Draft.__table__.insert().values(
                draft_id=content_id, agent_run_id=content_id, topic_id=topic_id,
                production_type="ai", status="published",
            ))
            connection.execute(Content.__table__.insert().values(
                content_id=content_id, topic_id=topic_id, source_draft_id=content_id,
                production_type="ai", title=f"글 {content_id}", summary="요약", body="독립 상세 본문",
                content_type="event" if content_id == 112 else "news",
                image_url=None if legacy else feed_service.slide_image_url(content_id, topic_id, 1),
                judgment_status="needs_review" if content_id in (103, 111) else "confirmed",
                ai_judgment_basis={"preview": True} if content_id == 111 else (
                    None if legacy else {"method": "carousel_import_v1", "private_review": "secret"}
                ),
                status="hidden" if content_id == 106 else "active",
                deleted_at=now if content_id == 107 else None,
                feed_valid_until=(None if legacy or content_id == 109 else
                                  now + timedelta(days=-1 if content_id == 102 else 1)),
                published_at=now + timedelta(seconds=content_id),
            ))
            if content_id != 108:
                for source_no in (1, 2):
                    connection.execute(ContentSource.__table__.insert().values(
                        content_source_id=content_id * 10 + source_no, content_id=content_id,
                        source_url=f"https://example.com/{content_id}/{source_no}",
                        source_title=f"출처 {source_no}", source_role="primary" if source_no == 1 else "citation",
                        citation_order=source_no,
                    ))
            pages = [] if legacy else ([1, 2] if content_id == 104 else
                    ([1, 3, 4] if content_id == 105 else list(range(1, 11 if content_id == 112 else 4))))
            for page in reversed(pages):
                connection.execute(ContentSlide.__table__.insert().values(
                    content_id=content_id, page=page, role="cover" if page == 1 else "body",
                    eyebrow="소식", title_lines=[f"제목 {page}"], body_sentences=[f"본문 {page}"],
                    layout_json=LAYOUT, image_data=png, image_sha256=sha256(png).hexdigest(),
                    image_width=1308, image_height=1632,
                ))
            connection.execute(SavedItem.__table__.insert().values(
                saved_item_id=content_id, user_id=1, content_id=content_id,
                tap_id=topic_id, status="kept", saved_tags=[], saved_at=now + timedelta(seconds=content_id),
            ))
    with Session(memory_db, expire_on_commit=False) as session:
        yield SimpleNamespace(db=AsyncAdapter(session), session=session, engine=memory_db, png=png)


@pytest.mark.asyncio
async def test_public_scope_is_shared_and_filters_before_pagination(carousel_db):
    db = carousel_db.db
    page = await content_repository.list_public_contents(db, 1, subtopic_id=None, after=None, limit=2)
    assert [item.content_id for item in page] == [112, 111]
    after = (page[-1].published_at, page[-1].content_id)
    rest = await content_repository.list_public_contents(db, 1, subtopic_id=None, after=after, limit=20)
    assert [item.content_id for item in rest] == [110, 101]
    saved = await bookmark_repository.list_saved_bookmarks(db, 1, topic_id=1, q=None, after=None, limit=20)
    assert [content.content_id for _, content in saved] == [112, 111, 110, 101]
    for content_id in range(102, 110):
        assert await content_repository.get_public_content(db, content_id, 1) is None
        assert await content_repository.get_visible_content(db, content_id) is None
    assert await content_repository.get_public_content(db, 113, 1) is None


@pytest.mark.asyncio
async def test_feed_detail_keep_order_sources_full_body_and_public_fields_only(carousel_db):
    statements = []
    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(carousel_db.engine, "before_cursor_execute", capture)
    try:
        feed = await feed_service.get_feed(carousel_db.db, 1, 1, subtopic_id=None, cursor=None, limit=20)
        detail = await feed_service.get_content_detail(carousel_db.db, 1, 1, 101)
    finally:
        event.remove(carousel_db.engine, "before_cursor_execute", capture)
    body = detail.model_dump(by_alias=True)
    assert detail.display_mode == "full_body" and detail.body == "독립 상세 본문"
    assert detail.content_type == "news"
    assert [item["page"] for item in body["slides"]] == [1, 2, 3]
    assert body["slides"][0]["imageUrl"] == "/api/v1/contents/101/slides/1/image?topicId=1"
    assert body["slides"][0]["layout"]["textOverlayBox"] == {"x": 0.06, "y": 0.67, "width": 0.88, "height": 0.29}
    assert [item["citationOrder"] for item in body["sources"]] == [1, 2]
    assert body["sourceUrl"] == body["sources"][0]["url"]
    assert body["sources"][0]["publisher"] is None
    assert body["notices"] == [{"code": "AI_GENERATED", "message": "AI가 생성한 콘텐츠입니다."}]
    assert "secret" not in detail.model_dump_json()
    assert "imageSha256" not in body["slides"][0]
    assert all("image_data" not in statement for statement in statements)
    items = {item.id: item for item in feed.sections[0].contents}
    assert items[101].slides == detail.slides
    assert len(items[112].slides) == 10
    assert items[110].slides == [] and items[110].notices == []
    assert items[111].slides == []
    assert [notice["code"] for notice in items[111].notices] == ["REVIEW_REQUIRED", "DEV_PREVIEW"]


@pytest.mark.asyncio
async def test_metadata_keeps_png_deferred_and_image_returns_exact_bytes(carousel_db):
    slides = await carousel_repository.list_slides(carousel_db.db, 101)
    assert "image_data" in inspect(slides[0]).unloaded
    assert await feed_service.get_content_slide_image(carousel_db.db, 1, 1, 101, 2) == carousel_db.png


@pytest.mark.asyncio
async def test_local_test_preview_shares_feed_detail_save_and_image_scope(carousel_db, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@127.0.0.1/checking_live_e2e")
    monkeypatch.setattr(settings, "event_data_source", "local_demo")
    carousel_db.session.execute(update(Content).where(Content.content_id == 103).values(
        ai_judgment_basis={"method": "carousel_import_v1", "preview": True,
                           "local_test_preview": {"database": "checking_live_e2e", "quality_approval": "not_reviewed"}},
    ))
    feed = await feed_service.get_feed(carousel_db.db, 1, 1, subtopic_id=None, cursor=None, limit=20)
    item = next(item for item in feed.sections[0].contents if item.id == 103)
    assert [notice["code"] for notice in item.notices] == ["REVIEW_REQUIRED", "DEV_PREVIEW", "AI_GENERATED"]
    detail = await feed_service.get_content_detail(carousel_db.db, 1, 1, 103)
    assert detail.display_mode == "full_body" and detail.body == "독립 상세 본문"
    assert detail.notices == item.notices
    assert await feed_service.get_content_slide_image(carousel_db.db, 1, 1, 103, 1) == carousel_db.png
    saved = await bookmark_repository.list_saved_bookmarks(carousel_db.db, 1, topic_id=1, q=None, after=None, limit=20)
    assert 103 in [content.content_id for _, content in saved]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["remote", "production_db", "production_events", "other_database", "forged_review", "missing_marker", "query_override"])
async def test_unapproved_preview_stays_hidden_outside_matching_local_test(carousel_db, monkeypatch, case):
    url = "postgresql://u:p@127.0.0.1/checking_live_e2e"
    source = "local_demo"
    marker = {"database": "checking_live_e2e", "quality_approval": "not_reviewed"}
    if case == "remote":
        url = "postgresql://u:p@db.example.com/checking_live_e2e"
    elif case == "production_db":
        url = "postgresql://u:p@127.0.0.1/checking"
    elif case == "production_events":
        source = "production"
    elif case == "other_database":
        marker["database"] = "other_e2e"
    elif case == "forged_review":
        marker["quality_approval"] = "approved"
    elif case == "missing_marker":
        marker = None
    else:
        url += "?hostaddr=10.0.0.1"
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "event_data_source", source)
    carousel_db.session.execute(update(Content).where(Content.content_id == 103).values(
        ai_judgment_basis={"method": "carousel_import_v1", "preview": True, "local_test_preview": marker},
    ))
    assert await content_repository.get_public_content(carousel_db.db, 103, 1) is None
    assert await carousel_repository.get_slide_image(carousel_db.db, 103, 1) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["expired", "missing_slide", "missing_sources", "hidden", "deleted"])
async def test_local_preview_keeps_expiry_media_and_withdrawal_gates(carousel_db, monkeypatch, case):
    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@localhost/checking_live_e2e")
    monkeypatch.setattr(settings, "event_data_source", "automated_test")
    changes = {"ai_judgment_basis": {"method": "carousel_import_v1", "preview": True,
               "local_test_preview": {"database": "checking_live_e2e", "quality_approval": "not_reviewed"}}}
    if case == "expired":
        changes["feed_valid_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif case == "missing_slide":
        carousel_db.session.execute(delete(ContentSlide).where(ContentSlide.content_id == 103, ContentSlide.page == 2))
    elif case == "missing_sources":
        carousel_db.session.execute(delete(ContentSource).where(ContentSource.content_id == 103))
    elif case == "hidden":
        changes["status"] = "hidden"
    else:
        changes["deleted_at"] = datetime.now(timezone.utc)
    carousel_db.session.execute(update(Content).where(Content.content_id == 103).values(**changes))
    assert await content_repository.get_public_content(carousel_db.db, 103, 1) is None
    assert await carousel_repository.get_slide_image(carousel_db.db, 103, 1) is None


def set_checking_preview(carousel_db):
    basis = {"method": "carousel_import_v1", "preview": True, "checking_test_preview": {
        "database": "me_nu_checking", "environment": "checking", "quality_approval": "not_reviewed"}}
    carousel_db.session.execute(update(Content).where(Content.content_id == 103).values(ai_judgment_basis=basis))
    return basis


@pytest.mark.asyncio
async def test_checking_preview_shares_feed_detail_save_reaction_image_and_test_notices(carousel_db, monkeypatch):
    configure_checking(monkeypatch, settings)
    set_checking_preview(carousel_db)
    feed = await feed_service.get_feed(carousel_db.db, 1, 1, subtopic_id=None, cursor=None, limit=20)
    card = next(item for section in feed.sections for item in section.contents if item.id == 103)
    assert {notice["code"] for notice in card.notices} >= {"REVIEW_REQUIRED", "DEV_PREVIEW", "AI_GENERATED"}
    detail = await feed_service.get_content_detail(carousel_db.db, 1, 1, 103)
    assert {notice["code"] for notice in detail.notices} >= {"REVIEW_REQUIRED", "DEV_PREVIEW", "AI_GENERATED"}
    saved = await bookmark_repository.list_saved_bookmarks(carousel_db.db, 1, topic_id=1, q=None, after=None, limit=20)
    assert 103 in [content.content_id for _, content in saved]
    await reaction_service.set_bookmark(carousel_db.db, 1, 1, 103)
    await reaction_service.set_like(carousel_db.db, 1, 1, 103)
    assert await feed_service.get_content_slide_image(carousel_db.db, 1, 1, 103, 1) == carousel_db.png
    # Turning off the explicit test scope hides the same stored rows everywhere.
    monkeypatch.setattr(settings, "checking_carousel_test_preview_enabled", False)
    assert await content_repository.get_public_content(carousel_db.db, 103, 1) is None
    assert await carousel_repository.get_slide_image(carousel_db.db, 103, 1) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["production", "labs", "wrong_db", "url_override", "wrong_origin",
    "wrong_marker", "approved", "local_marker", "expired", "missing_slide", "missing_sources", "hidden", "deleted"])
async def test_checking_preview_cannot_escape_scope_or_skip_validity_gates(carousel_db, monkeypatch, case):
    configure_checking(monkeypatch, settings)
    basis = set_checking_preview(carousel_db)
    changes = {}
    if case == "production": monkeypatch.setattr(settings, "event_data_source", "production")
    elif case == "labs": monkeypatch.setattr(settings, "menu_deployment_env", "labs")
    elif case == "wrong_db": monkeypatch.setattr(settings, "database_url", settings.database_url.replace("/me_nu_checking", "/me_nu"))
    elif case == "url_override": monkeypatch.setattr(settings, "database_url", settings.database_url + "?hostaddr=10.0.0.1")
    elif case == "wrong_origin": monkeypatch.setattr(settings, "oauth_frontend_origin", "https://me-nu.kr")
    elif case == "approved": changes["judgment_status"] = "confirmed"
    elif case == "wrong_marker":
        basis["checking_test_preview"]["database"] = "another"
        changes["ai_judgment_basis"] = basis
    elif case == "local_marker":
        changes["ai_judgment_basis"] = {"method": "carousel_import_v1", "preview": True,
            "local_test_preview": {"database": "me_nu_checking", "quality_approval": "not_reviewed"}}
    elif case == "expired": changes["feed_valid_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif case == "missing_slide":
        carousel_db.session.execute(delete(ContentSlide).where(ContentSlide.content_id == 103, ContentSlide.page == 2))
    elif case == "missing_sources":
        carousel_db.session.execute(delete(ContentSource).where(ContentSource.content_id == 103))
    elif case == "hidden": changes["status"] = "hidden"
    else: changes["deleted_at"] = datetime.now(timezone.utc)
    if changes:
        carousel_db.session.execute(update(Content).where(Content.content_id == 103).values(**changes))
    assert await content_repository.get_public_content(carousel_db.db, 103, 1) is None
    assert await carousel_repository.get_slide_image(carousel_db.db, 103, 1) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("content_id", list(range(102, 112)) + [113, 999])
async def test_image_rejects_unavailable_noncarousel_or_other_topic(carousel_db, content_id):
    with pytest.raises(ApiError) as exc:
        await feed_service.get_content_slide_image(carousel_db.db, 1, 1, content_id, 1)
    assert exc.value.status_code == 404 and exc.value.code == "CONTENT_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["other_user", "deleted_tap", "inactive_topic", "missing_page"])
async def test_image_requires_current_owned_topic_and_page(carousel_db, change):
    user_id, page = (2 if change == "other_user" else 1), (4 if change == "missing_page" else 1)
    if change == "deleted_tap":
        carousel_db.session.execute(update(Tap).where(Tap.tap_id == 1).values(deleted_at=datetime.now(timezone.utc)))
    if change == "inactive_topic":
        carousel_db.session.execute(update(Topic).where(Topic.topic_id == 1).values(is_active=False))
    with pytest.raises(ApiError) as exc:
        await feed_service.get_content_slide_image(carousel_db.db, user_id, 1, 101, page)
    assert exc.value.status_code == 404
    assert exc.value.code == ("CONTENT_NOT_FOUND" if change == "missing_page" else "TOPIC_NOT_FOUND")


@pytest.mark.asyncio
async def test_withdrawal_after_metadata_read_still_blocks_image_bytes(carousel_db):
    assert await content_repository.get_public_content(carousel_db.db, 101, 1)
    carousel_db.session.execute(update(Content).where(Content.content_id == 101).values(status="hidden"))
    assert await carousel_repository.get_slide_image(carousel_db.db, 101, 1) is None


@pytest.mark.asyncio
async def test_expired_carousel_rejects_new_reaction_but_keeps_delete(carousel_db):
    for operation in (reaction_service.set_like, reaction_service.set_bookmark):
        with pytest.raises(ApiError) as exc:
            await operation(carousel_db.db, 1, 1, 102)
        assert exc.value.code == "CONTENT_NOT_FOUND"
    await reaction_service.clear_bookmark(carousel_db.db, 1, 1, 102)
    saved = carousel_db.session.get(SavedItem, 102)
    assert saved.status == "deleted"


@pytest.mark.asyncio
async def test_image_http_auth_validation_and_private_png_contract(carousel_db):
    app = create_app(enable_dev_api=False)
    state = {"session": None}
    user = SimpleNamespace(user_id=1, onboarding_completed_at=None, account_status="active")
    async def auth(_request: Request):
        return None if state["session"] is None else AuthContext(
            session=SimpleNamespace(session_state=state["session"]), raw_token="test", user=user,
        )
    async def database():
        yield carousel_db.db
    app.dependency_overrides[get_optional_auth_context] = auth
    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = "/api/v1/contents/101/slides/1/image?topicId=1"
        assert (await client.get(url)).status_code == 401
        state["session"] = "onboarding_pending"
        assert (await client.get(url)).status_code == 403
        state["session"] = "active"
        user.onboarding_completed_at = datetime.now(timezone.utc)
        response = await client.get(url)
        assert response.status_code == 200 and response.content == carousel_db.png
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "etag" not in response.headers
        assert (await client.get("/api/v1/contents/101/slides/0/image?topicId=1")).status_code == 422
        assert (await client.get("/api/v1/contents/101/slides/11/image?topicId=1")).status_code == 422
        assert (await client.get("/api/v1/contents/101/slides/1/image?topicId=9223372036854775808")).status_code == 422
        carousel_db.session.execute(delete(ContentSlide).where(ContentSlide.content_id == 101, ContentSlide.page == 3))
        unavailable = await client.get(url)
        assert unavailable.status_code == 404
        assert unavailable.headers["cache-control"] == "private, no-store"


def test_image_openapi_and_default_additive_response_fields():
    spec = create_app(enable_dev_api=False).openapi()
    operation = spec["paths"]["/api/v1/contents/{contentId}/slides/{page}/image"]["get"]
    assert set(operation["responses"]["200"]["content"]) == {"image/png"}
    assert {"401", "403", "404", "422"} <= set(operation["responses"])
    for schema in ("FeedCard", "ContentDetail"):
        assert "slides" in spec["components"]["schemas"][schema]["properties"]
        assert "slides" not in spec["components"]["schemas"][schema]["required"]
    assert "sources" in spec["components"]["schemas"]["ContentDetail"]["properties"]
