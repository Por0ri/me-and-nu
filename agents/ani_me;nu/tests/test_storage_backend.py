import json

from ani_menu_agent.config import Settings
from ani_menu_agent.models import ContentKind, ContentPackage, SourceEvidence
from ani_menu_agent.storage import AgentRepository


def test_explicit_sqlite_backend_overrides_configured_postgres_url(
    monkeypatch, tmp_path
) -> None:
    sqlite_path = tmp_path / "explicit.sqlite3"
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_URL="postgresql://unused:unused@example.invalid/unused",
        ANI_MENU_DATABASE_PATH=str(sqlite_path),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)

    repository = AgentRepository()

    assert repository.dialect == "sqlite"
    assert repository.path == sqlite_path
    assert sqlite_path.is_file()


def test_duplicate_check_allows_only_explicit_retry_of_same_failed_request(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "retry.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    repository.initialize()
    source = SourceEvidence(
        id="test_official",
        title="A new product announcement",
        url="https://official.example/news/123",
        publisher="Official",
        is_official=True,
        checked_at="2026-10-20T00:00:00Z",
        role="primary",
    )
    package = ContentPackage(
        request_id="crawl-retry-test",
        topic="Test product",
        content_kind=ContentKind.INFORMATION,
        sources=[source],
    )
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO content_drafts "
            "(request_id, topic, content_kind, status, package_json, valid_until, purge_at, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                package.request_id,
                package.topic,
                package.content_kind.value,
                "review_returned",
                package.model_dump_json(),
                "2026-11-20T00:00:00Z",
                "2026-11-20T00:00:00Z",
                "2026-10-20T00:00:00Z",
                "2026-10-20T00:00:00Z",
            ),
        )

    assert repository.find_duplicate_content(package) is not None
    assert repository.find_duplicate_content(package, retry_same_request=True) is None


def test_event_dates_and_domestic_overseas_location_are_queryable_in_sqlite(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "events.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    package = ContentPackage(
        request_id="event-query-test",
        topic="장송의 프리렌 콜라보 카페",
        content_kind=ContentKind.INFORMATION,
        content_type="event",
        event_start="2026-10-10",
        event_end="2026-11-09",
        event_location="시부야 공원거리점",
        event_country="일본",
        event_region="overseas",
        sources=[
            SourceEvidence(
                id="test_official",
                title="Official event",
                url="https://official.example/event",
                publisher="Official",
                is_official=True,
                role="primary",
            )
        ],
    )

    repository.persist_generated_content(package, "ready_for_feed")
    repository.enqueue_for_feed(package)

    with repository.connect() as connection:
        draft = connection.execute(
            "SELECT event_start, event_end, event_location, event_country, event_region "
            "FROM content_drafts WHERE request_id=?",
            (package.request_id,),
        ).fetchone()
        queued = connection.execute(
            "SELECT event_start, event_end, event_location, event_country, event_region "
            "FROM feed_queue WHERE request_id=?",
            (package.request_id,),
        ).fetchone()

    assert tuple(draft) == (
        "2026-10-10",
        "2026-11-09",
        "시부야 공원거리점",
        "JP",
        "overseas",
    )
    assert tuple(queued) == tuple(draft)


def test_feed_payload_exposes_country_code_but_hides_internal_region_and_expiry(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "public-feed.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    package = ContentPackage(
        request_id="public-feed-test",
        topic="해외 콜라보 행사",
        content_kind=ContentKind.INFORMATION,
        content_type="event",
        event_start="2026-10-20",
        event_end="2026-10-20",
        event_country="Japan",
        event_location="도쿄 시부야 PARCO",
        event_region="overseas",
        audience_region="KR",
        feed_valid_until="2099-10-21T00:00:00+00:00",
        sources=[
            SourceEvidence(
                id="official",
                title="Official event",
                url="https://official.example/event",
                publisher="Official",
                is_official=True,
            )
        ],
    )
    repository.persist_generated_content(package, "ready_for_feed")
    repository.enqueue_for_feed(package)

    item = repository.active_feed_items()[0]
    payload = item["package"]

    assert payload["event_country"] == "JP"
    assert payload["event_start"] == payload["event_end"] == "2026-10-20"
    assert "event_location" not in payload
    assert "event_region" not in payload
    assert "audience_region" not in payload
    assert "feed_valid_until" not in payload
    assert "purge_at" not in payload
    assert "valid_until" not in item


def test_news_country_is_cleared_before_sqlite_persistence(monkeypatch, tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "news-country.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    package = ContentPackage(
        request_id="news-country-test",
        topic="New anime announcement",
        content_kind=ContentKind.INFORMATION,
        content_type="news",
        event_country="JP",
        sources=[
            SourceEvidence(
                id="official",
                title="Official announcement",
                url="https://official.example/news",
                publisher="Official",
                is_official=True,
            )
        ],
    )

    repository.persist_generated_content(package, "ready_for_feed")
    repository.enqueue_for_feed(package)

    with repository.connect() as connection:
        draft = connection.execute(
            "SELECT event_country FROM content_drafts WHERE request_id=?",
            (package.request_id,),
        ).fetchone()
        queued = connection.execute(
            "SELECT event_country FROM feed_queue WHERE request_id=?",
            (package.request_id,),
        ).fetchone()

    assert draft["event_country"] is None
    assert queued["event_country"] is None


def test_legacy_review_analysis_content_type_is_migrated(monkeypatch, tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "review-migration.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    package = ContentPackage(
        request_id="legacy-review-test",
        topic="작품 분석",
        content_kind=ContentKind.INFORMATION,
        content_type="review",
    )
    repository.persist_generated_content(package, "rejected_archived")
    legacy_json = package.model_dump(mode="json")
    legacy_json["content_type"] = "review_analysis"
    with repository.connect() as connection:
        connection.execute(
            "UPDATE content_drafts SET content_type='review_analysis', package_json=? "
            "WHERE request_id=?",
            (json.dumps(legacy_json, ensure_ascii=False), package.request_id),
        )
        connection.execute(
            "INSERT INTO content_types (type_code, label_ko, sort_order, active) "
            "VALUES ('review_analysis', '리뷰 분석', 7, 1)"
        )

    migrated = AgentRepository(path=repository.path)

    with migrated.connect() as connection:
        draft = connection.execute(
            "SELECT content_type, package_json FROM content_drafts WHERE request_id=?",
            (package.request_id,),
        ).fetchone()
        legacy_type = connection.execute(
            "SELECT type_code FROM content_types WHERE type_code='review_analysis'"
        ).fetchone()
    assert draft["content_type"] == "review"
    assert json.loads(draft["package_json"])["content_type"] == "review"
    assert legacy_type is None


def test_nonpassing_retry_holds_stale_ready_feed_revision(monkeypatch, tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "feed-safety.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    package = ContentPackage(
        request_id="feed-safety-test",
        topic="하이큐!! 콜라보 행사",
        content_kind=ContentKind.INFORMATION,
        content_type="event",
    )

    repository.persist_generated_content(package, "ready_for_feed")
    repository.enqueue_for_feed(package)
    repository.set_content_status(package.request_id, "rejected_archived")

    with repository.connect() as connection:
        draft_status = connection.execute(
            "SELECT status FROM content_drafts WHERE request_id=?", (package.request_id,)
        ).fetchone()[0]
        feed_status = connection.execute(
            "SELECT status FROM feed_queue WHERE request_id=?", (package.request_id,)
        ).fetchone()[0]

    assert draft_status == "rejected_archived"
    assert feed_status == "held_for_quality_review"
