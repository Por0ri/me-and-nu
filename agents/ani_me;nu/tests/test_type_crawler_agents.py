from types import SimpleNamespace

from ani_menu_agent.agents.content_generation.graph import build_content_generation_agent
from ani_menu_agent.agents.crawlers import TYPE_CRAWLER_AGENTS, get_type_crawler
from ani_menu_agent.agents.crawlers.base import profile_signal_checks
from ani_menu_agent.agents.reviewer.graph import build_reviewer_agent
from ani_menu_agent.discovery import DiscoveryItem
from ani_menu_agent.models import CONTENT_TYPE_LABELS, ContentType
from ani_menu_agent.sources.models import Source


def test_exactly_ten_active_type_crawler_roles_match_content_types() -> None:
    assert len(TYPE_CRAWLER_AGENTS) == 10
    assert set(TYPE_CRAWLER_AGENTS) == {item.value for item in CONTENT_TYPE_LABELS}
    assert len({agent.agent_id for agent in TYPE_CRAWLER_AGENTS.values()}) == 10


def test_existing_generation_and_review_agents_are_not_duplicated() -> None:
    assert callable(build_content_generation_agent)
    assert callable(build_reviewer_agent)
    assert len(TYPE_CRAWLER_AGENTS) + 2 == 12


def test_each_crawler_uses_only_sources_mapped_to_its_type() -> None:
    for type_code, crawler in TYPE_CRAWLER_AGENTS.items():
        assert crawler.content_type.value == type_code
        assert crawler.source_ids()


def test_typed_crawler_collects_and_persists_candidates(monkeypatch) -> None:
    import ani_menu_agent.agents.crawlers.base as crawler_module

    candidate = DiscoveryItem(
        id="candidate-1",
        title="Sample event",
        url="https://example.com/event",
        source_id="source_1",
        content_types=[ContentType.EVENT.value],
    )
    source = Source(
        id="source_1",
        name="Sample event source",
        url="https://example.com/",
        category="event",
        content_types=[ContentType.EVENT],
        role="primary",
        collection="html",
        status="active",
        region="KR",
    )
    calls = {}

    def collect_candidates(**kwargs):
        calls.update(kwargs)
        return [candidate], ["one source timed out"]

    class RecordingRepository:
        def persist_discovery(self, items, region_policy):
            calls["persisted_items"] = items
            calls["persist_region_policy"] = region_policy
            return len(items)

    monkeypatch.setattr(crawler_module, "collect_candidates", collect_candidates)
    monkeypatch.setattr(
        crawler_module,
        "load_source_registry",
        lambda: SimpleNamespace(sources=[source]),
    )
    monkeypatch.setattr(crawler_module, "supports_discovery_adapter", lambda *a, **kw: True)
    run = get_type_crawler("event").run(
        work_title="Sample Work",
        limit=7,
        timeout=3.0,
        region_policy="jp_first",
        repository=RecordingRepository(),
        capture_images=False,
    )

    assert calls["content_type"] == "event"
    assert calls["work_title"] == "Sample Work"
    assert calls["limit"] == 7
    assert calls["timeout"] == 3.0
    assert calls["source_ids"] == {"source_1"}
    assert calls["persist_region_policy"] == "jp_first"
    assert run.persisted_count == 1
    assert run.errors == ["one source timed out"]
    assert calls["persisted_items"][0]["id"] == "candidate-1"


def test_character_crawler_requires_a_work_title_without_collecting() -> None:
    run = get_type_crawler("character").run(capture_images=False)

    assert run.items == []
    assert run.persisted_count == 0
    assert "--work-title" in run.errors[0]


def test_type_profiles_have_distinct_source_and_image_policies() -> None:
    assert TYPE_CRAWLER_AGENTS["humor"].profile.source_roles == frozenset(
        {"trend", "primary", "editorial", "discovery"}
    )
    assert TYPE_CRAWLER_AGENTS["humor"].profile.capture_primary_page_assets is True
    assert TYPE_CRAWLER_AGENTS["review"].profile.source_roles == frozenset(
        {"editorial", "discovery"}
    )
    assert TYPE_CRAWLER_AGENTS["event"].profile.asset_page_limit == 3


def test_all_ten_profiles_match_representative_multilingual_type_evidence() -> None:
    examples = {
        "news": "New season announcement: broadcast starts in 2027.",
        "event": "Event period October 23 to 27 at the Shibuya venue.",
        "guide": "Episode story and character introductions for this work.",
        "people": "Voice actor cast credits and profile.",
        "goods": "Official goods product preorder and release price.",
        "review": "Review and criticism by the author.",
        "humor": "Production making-of interview with the animation staff.",
        "character": "Anime character profile and birthday.",
        "curation": "Ranking poll based on 1,200 votes.",
        "music": "Anime soundtrack album release by the artist.",
    }
    assert set(examples) == set(TYPE_CRAWLER_AGENTS)
    for content_type, text in examples.items():
        checks = profile_signal_checks(TYPE_CRAWLER_AGENTS[content_type].profile, text)
        assert checks, content_type
        assert any(checks.values()), (content_type, checks)


def test_type_signal_checks_are_diagnostic_and_empty_text_matches_nothing() -> None:
    profile = TYPE_CRAWLER_AGENTS["event"].profile
    checks = profile_signal_checks(profile, "A page with no event details.")
    assert set(checks) == {name for name, _ in profile.required_signals}
    assert not any(checks.values())

    guide_checks = profile_signal_checks(
        TYPE_CRAWLER_AGENTS["guide"].profile,
        "A short history of an adaptation.",
    )
    assert not guide_checks["story_or_episode"]


def test_event_crawler_captures_and_routes_same_page_images(monkeypatch) -> None:
    import ani_menu_agent.agents.crawlers.base as crawler_module

    source = Source(
        id="event_primary",
        name="Official Event",
        url="https://example.com/events/",
        category="event",
        content_types=[ContentType.EVENT],
        role="primary",
        collection="html",
        status="active",
        region="JP",
    )
    candidate = DiscoveryItem(
        id="event_candidate",
        title="Anime collaboration cafe",
        url="https://example.com/events/cafe",
        source_id=source.id,
        content_types=[ContentType.EVENT.value],
        source_role="primary",
    )
    captured = []
    source_records = []
    curated = []

    class RecordingRepository:
        def persist_page_capture(self, payload, region):
            captured.append((payload, region))

        def upsert_record(self, **kwargs):
            source_records.append(kwargs)

    capture = SimpleNamespace(
        source_id=source.id,
        title="Official Cafe Notice",
        url="https://example.com/events/cafe",
        fetched_at="2026-10-24T00:00:00+00:00",
        published_at="2026-10-20",
        text="Cafe runs from October 23 to October 27.",
        image_urls=["https://example.com/assets/cafe-key-visual.jpg"],
        image_alt_text={"https://example.com/assets/cafe-key-visual.jpg": "Cafe key visual"},
        image_source_pages={
            "https://example.com/assets/cafe-key-visual.jpg": "https://example.com/events/cafe"
        },
        image_source_titles={
            "https://example.com/assets/cafe-key-visual.jpg": "Official Cafe Notice"
        },
        model_dump=lambda mode: {
            "source_id": source.id,
            "title": "Official Cafe Notice",
            "url": "https://example.com/events/cafe",
            "images": [],
        },
    )
    monkeypatch.setattr(
        crawler_module,
        "load_source_registry",
        lambda: SimpleNamespace(sources=[source]),
    )
    monkeypatch.setattr(crawler_module, "collect_official_page", lambda *a, **kw: capture)

    def fake_curator(state):
        curated.append(state)
        return {"package": state["package"], "persisted_image_count": 1}

    monkeypatch.setattr(crawler_module, "image_curator", fake_curator)
    result = TYPE_CRAWLER_AGENTS["event"]._capture_images(
        {"timeout": 2.0, "errors": []}, RecordingRepository(), [candidate]
    )

    assert result == {
        "captured_page_count": 1,
        "image_candidate_count": 1,
        "persisted_image_count": 1,
        "errors": [],
    }
    assert captured[0][0]["content_type"] == "event"
    assert captured[0][0]["discovery_candidate_id"] == "event_candidate"
    assert curated[0]["repository"] is not None
    assert captured[0][0]["images"][0]["download_status"] == "pending"
    assert source_records[0]["record_kind"] == "source"
    assert curated[0]["download_images"] is True
