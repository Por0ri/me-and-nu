import hashlib
import json

from ani_menu_agent.config import Settings
from ani_menu_agent.models import (
    ContentKind,
    ContentPackage,
    Fact,
    ImageAsset,
    RightsStatus,
    SourceEvidence,
    WorkMatchEvidence,
)
from ani_menu_agent.storage import AgentRepository, _classify_legacy_reference_art


def test_legacy_official_art_is_reclassified_but_goods_and_event_assets_are_not_reused() -> None:
    source = SourceEvidence(
        id="one_piece_official",
        title="ONE PIECE.com character page",
        url="https://one-piece.com/character/luffy",
        publisher="ONE PIECE.com",
        is_official=True,
        role="primary",
    )
    image = ImageAsset(
        id="legacy-art",
        url="https://one-piece.com/assets/luffy-main.jpg",
        source_url="https://one-piece.com/character/luffy",
        official=True,
        source_description="",
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        download_status="downloaded",
        width=1200,
        height=800,
    )

    generic_art = _classify_legacy_reference_art(
        image,
        source,
        source_role="primary",
        origin_content_type="news",
    )
    assert generic_art is not None
    assert generic_art.asset_kind == "character_art"
    assert generic_art.asset_metadata_confidence == 0.4
    assert (
        _classify_legacy_reference_art(
            image,
            source,
            source_role="primary",
            origin_content_type="goods",
        )
        is None
    )
    goods_source = source.model_copy(update={"title": "ONE PIECE pop-up shop goods"})
    assert (
        _classify_legacy_reference_art(
            image,
            goods_source,
            source_role="primary",
            origin_content_type=None,
        )
        is None
    )


def test_discovery_candidate_refreshes_content_types_across_routing_runs(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "discovery.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    repository.initialize()
    common = {
        "id": "shared-source-item",
        "title": "Official announcement",
        "url": "https://official.example/news/123",
        "region": "JP",
        "status": "discovery_only",
    }

    repository.persist_discovery([{**common, "content_types": ["news"]}], "mixed")
    repository.persist_discovery([{**common, "content_types": ["event"]}], "mixed")

    candidate = repository.load_discovery_candidate("shared-source-item")
    assert candidate["content_types"] == ["event"]


def test_unresolved_work_candidate_is_retained_with_resolution_note(monkeypatch, tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "unresolved-work.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    repository.initialize()
    candidate = {
        "id": "new-anime-title",
        "source_id": "anime_editorial",
        "content_types": ["review"],
        "title": "Uncatalogued Anime – Episodes 1-2",
        "url": "https://editorial.example/review/new-anime",
        "region": "global",
        "status": "discovery_only",
    }
    repository.persist_discovery([candidate], "mixed")

    assert repository.mark_discovery_work_unresolved(
        "new-anime-title", "no unique catalog match"
    )
    saved = repository.load_discovery_candidate("new-anime-title")

    assert saved["title"] == candidate["title"]
    assert saved["work_resolution"]["status"] == "unresolved_work_id"
    assert saved["work_resolution"]["reason"] == "no unique catalog match"

    repository.persist_discovery([{**candidate, "summary": "refreshed metadata"}], "mixed")
    refreshed = repository.load_discovery_candidate("new-anime-title")
    assert refreshed["work_resolution"]["status"] == "unresolved_work_id"


def test_source_candidate_is_stored_as_separate_record_kind(monkeypatch, tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "source-candidates.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    repository.initialize()
    candidate = {
        "id": "source-candidate:abcdef",
        "host": "publisher.example",
        "root_url": "https://publisher.example/",
        "sample_urls": {"event": ["https://publisher.example/event/one"]},
        "discovered_via": ["known_aggregator"],
        "content_types": ["event"],
        "status": "pending_validation",
    }

    repository.persist_source_candidate(candidate)

    assert repository.load_source_candidate(candidate["id"]) == candidate
    assert repository.list_source_candidates(status="pending_validation") == [candidate]


def test_duplicate_check_reads_baseline_database_without_writing_to_it(
    monkeypatch, tmp_path
) -> None:
    from pathlib import Path

    sample = json.loads(
        (Path(__file__).resolve().parents[1] / "examples" / "sample_content.json").read_text(
            encoding="utf-8"
        )
    )
    package = ContentPackage.model_validate(sample)
    baseline_path = tmp_path / "baseline.sqlite3"
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(baseline_path),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    baseline = AgentRepository()
    with baseline.connect() as connection:
        connection.execute(
            "INSERT INTO feed_queue (request_id, topic, content_type, package_json, queued_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                package.request_id,
                package.topic,
                package.content_type,
                json.dumps(package.model_dump(mode="json"), ensure_ascii=False),
                "2026-10-21T00:00:00Z",
            ),
        )

    new_path = tmp_path / "generation-200.sqlite3"
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(new_path),
        ANI_MENU_DUPLICATE_BASELINE_PATH=str(baseline_path),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    isolated = AgentRepository()
    duplicate = isolated.find_duplicate_content(package.model_copy(update={"request_id": "new"}))

    assert duplicate is not None
    assert duplicate["request_id"] == package.request_id
    assert isolated.counts()["feed_queue"] == 0


def test_recapturing_page_without_images_clears_stale_request_images(monkeypatch, tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "stale-images.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    repository.initialize()
    url = "https://official.example/news/123"
    request_id = f"crawl-{hashlib.sha1(url.encode('utf-8')).hexdigest()[:14]}"
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO agent_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "image",
                f"image:{request_id}:stale-recommendation",
                "image_asset",
                request_id,
                "Old related image",
                "JP",
                None,
                None,
                None,
                None,
                "downloaded",
                "{}",
                "2026-10-20T00:00:00Z",
                "2026-10-20T00:00:00Z",
            ),
        )

    repository.persist_page_capture({"url": url, "title": "Current article", "images": []}, "JP")

    with repository.connect() as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM agent_records WHERE agent_type='image' "
            "AND request_id=? AND record_kind='image_asset'",
            (request_id,),
        ).fetchone()[0]
    assert remaining == 0


def test_text_only_article_loads_other_page_images_from_same_official_work(
    monkeypatch, tmp_path
) -> None:
    current_settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "generation.sqlite3"),
    )
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "reference.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: current_settings)
    repository = AgentRepository()
    repository.initialize()
    article_source = SourceEvidence(
        id="anime_corner",
        title="One Piece fan discussion",
        url="https://animecorner.me/one-piece-discussion/",
        publisher="Anime Corner",
        role="editorial",
    )
    package = ContentPackage(
        request_id="crawl-one-piece-text-only",
        topic="원피스 팬 토론",
        content_kind=ContentKind.INFORMATION,
        menu_work_ids=[8],
        sources=[article_source],
    )
    image_source = SourceEvidence(
        id="one_piece_official",
        title="ONE PIECE.com character page",
        url="https://one-piece.com/character/luffy",
        publisher="ONE PIECE.com",
        is_official=True,
        role="primary",
    )
    image = ImageAsset(
        id="same-work-official-image",
        url="https://one-piece.com/assets/luffy.jpg",
        source_url="https://one-piece.com/character/luffy",
        official=True,
        asset_kind="character_art",
        depicted_entities=["루피"],
        asset_metadata_confidence=0.9,
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        rights_evidence_url="https://one-piece.com/character/luffy",
        rights_checked_at="2026-10-21T00:00:00Z",
        attribution_text="ONE PIECE.com 공식 이미지",
        storage_key="baseline-image-key",
        download_status="downloaded",
        width=1200,
        height=1200,
    )
    source_package = ContentPackage(
        request_id="crawl-one-piece-official-image-source",
        topic="원피스 공식 캐릭터 이미지",
        content_kind=ContentKind.INFORMATION,
        sources=[image_source],
        images=[image],
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    reference_repository = AgentRepository()
    reference_repository.initialize()
    reference_repository.upsert_record(
        agent_type="information",
        record_id=f"source:{source_package.request_id}:{image_source.id}",
        record_kind="source",
        package=source_package,
        payload=image_source.model_dump(mode="json"),
    )
    reference_repository.upsert_record(
        agent_type="image",
        record_id=f"image:{source_package.request_id}:{image.id}",
        record_kind="image_asset",
        package=source_package,
        payload=image.model_dump(mode="json"),
        status=image.rights_status.value,
    )
    monkeypatch.setenv("ANI_MENU_IMAGE_REFERENCE_PATH", settings.database_path)
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: current_settings)

    supplemented = repository._supplement_same_work_official_images(package)

    assert [item.id for item in supplemented] == ["same-work-official-image"]
    assert supplemented[0].provenance_source is not None
    assert supplemented[0].menu_work_ids == [8]
    assert supplemented[0].storage_key is None
    assert supplemented[0].download_status == "pending"

    event_package = package.model_copy(update={"content_type": "event"})
    assert repository._supplement_same_work_official_images(event_package) == []


def test_load_content_package_preserves_requested_type_before_image_supplement(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "typed-event.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    repository.initialize()
    source = SourceEvidence(
        id="one_piece_official",
        title="ONE PIECE pop-up event notice",
        url="https://one-piece.com/news/event-2026",
        publisher="ONE PIECE.com",
        is_official=True,
        role="primary",
    )
    package = ContentPackage(
        request_id="crawl-typed-event-package",
        topic="ONE PIECE pop-up event",
        content_kind=ContentKind.INFORMATION,
        content_type="event",
        sources=[source],
        facts=[
            Fact(
                id="event-date",
                statement="The pop-up event opens on October 20.",
                source_ids=[source.id],
                verified=True,
                time_sensitive=True,
                fact_type="event_schedule",
            )
        ],
    )
    repository.persist_information(package)

    loaded = repository.load_content_package(
        package.request_id, "information", content_type="event"
    )

    assert loaded.content_type == "event"
    assert loaded.menu_work_ids == [8]
    assert loaded.images == []


def test_source_record_keeps_work_match_evidence_across_db_reload(monkeypatch, tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "kakashi-work-match.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    source = SourceEvidence(
        id="kitsu_anime_catalog",
        title="Kakashi Hatake",
        url="https://kitsu.io/api/edge/media-characters/21732?include=character,media",
        publisher="Kitsu Anime Catalog API",
        is_official=False,
        role="reference",
        region="global",
    )
    package = ContentPackage(
        request_id="crawl-kakashi-evidence",
        topic="카카시 프로필",
        content_kind=ContentKind.INFORMATION,
        content_type="character",
        menu_work_ids=[11],
        work_match_evidence=[
            WorkMatchEvidence(
                menu_work_id=11,
                basis="article_title",
                matched_value="Naruto: Shippuuden",
                evidence_url="https://kitsu.io/api/edge/media/1555",
                confidence=0.99,
            )
        ],
        sources=[source],
        facts=[
            Fact(
                id="kakashi-fact",
                statement="Kakashi is a character in Naruto: Shippuuden.",
                source_ids=[source.id],
                verified=True,
                fact_type="character_profile",
            )
        ],
    )
    repository.persist_information(package)

    loaded = repository.load_content_package(
        package.request_id, "information", content_type="character"
    )

    assert loaded.menu_work_ids == [11]
    assert loaded.work_match_evidence[0].matched_value == "Naruto: Shippuuden"


def test_non_ready_retry_holds_existing_feed_queue_item(monkeypatch, tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ANI_MENU_DATABASE_BACKEND="sqlite",
        ANI_MENU_DATABASE_PATH=str(tmp_path / "held-feed.sqlite3"),
    )
    monkeypatch.setattr("ani_menu_agent.storage.get_settings", lambda: settings)
    repository = AgentRepository()
    package = ContentPackage(
        request_id="crawl-held-retry",
        topic="Test product",
        content_kind=ContentKind.INFORMATION,
        content_type="goods",
        sources=[
            SourceEvidence(
                id="test_official",
                title="Product announcement",
                url="https://official.example/news/held",
                publisher="Official",
                is_official=True,
                checked_at="2026-10-20T00:00:00Z",
                role="primary",
            )
        ],
    )
    repository.enqueue_for_feed(package)
    repository.persist_generated_content(package, "needs_crawler_update")

    with repository.connect() as connection:
        status = connection.execute(
            "SELECT status FROM feed_queue WHERE request_id=?",
            (package.request_id,),
        ).fetchone()[0]
    assert status == "held_for_quality_review"


def test_explicit_sqlite_backend_overrides_configured_postgres_url(monkeypatch, tmp_path) -> None:
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
        title="A new licensed product announcement",
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
        menu_work_ids=[8],
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
                "generated_pending_review",
                package.model_dump_json(),
                "2026-11-20T00:00:00Z",
                "2026-11-20T00:00:00Z",
                "2026-10-20T00:00:00Z",
                "2026-10-20T00:00:00Z",
            ),
        )

    assert repository.find_duplicate_content(package) is not None
    assert repository.find_duplicate_content(package, retry_same_request=True) is None
    url_variant = package.model_copy(
        update={
            "request_id": "crawl-retry-variant",
            "sources": [
                source.model_copy(update={"url": "https://official.example/news/123/"})
            ],
        }
    )
    assert repository.find_duplicate_content(url_variant) is not None
    assert repository.find_duplicate_content(url_variant, retry_same_request=True) is None

    alternate_source = package.model_copy(
        update={
            "request_id": "crawl-alternate-source-retry",
            "sources": [source.model_copy(update={"url": "https://other.example/news/456"})],
        }
    )
    assert repository.find_duplicate_content(alternate_source) is not None
    assert repository.find_duplicate_content(alternate_source, retry_same_request=True) is None


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
        detail_body="상세 페이지에서 읽을 독립된 행사 안내입니다. " * 15,
        detail_fact_ids=["event-fact"],
        detail_source_ids=["official"],
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
    assert payload["detail_body"].startswith("상세 페이지에서 읽을 독립된 행사 안내")
    assert payload["detail_article"]["body"] == payload["detail_body"]
    assert payload["detail_article"]["fact_ids"] == ["event-fact"]
    assert payload["detail_article"]["source_ids"] == ["official"]
    assert "https://official.example/event" in payload["detail_markdown"]
    assert payload["detail_article"]["markdown"] == payload["detail_markdown"]
    assert (
        payload["detail_markdown"]
        .rstrip()
        .endswith("이 콘텐츠는 원문과 확인된 자료를 바탕으로 AI를 활용해 생성·정리했습니다.")
    )


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
