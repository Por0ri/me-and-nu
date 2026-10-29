from ani_menu_agent.models import ContentType
from ani_menu_agent.sources.models import Source
from ani_menu_agent.work_catalog import load_work_catalog
from ani_menu_agent.work_type_coverage import (
    FACT_TYPE_TO_CONTENT_TYPE,
    _is_expired,
    build_work_type_coverage,
    find_work_ids,
)


def test_work_type_coverage_separates_candidates_verified_facts_and_ready_items() -> None:
    catalog = load_work_catalog()
    source = Source(
        id="naruto_official",
        name="Naruto official",
        url="https://naruto.example/news/",
        category="official_news",
        content_types=[ContentType.EVENT],
        role="primary",
        collection="html",
        status="active",
        region="JP",
        work_title_ko="나루토",
    )
    records = [
        {
            "request_id": "crawl-1",
            "record_kind": "source",
            "status": "collected",
            "payload_json": '{"id":"naruto_official","title":"나루토 행사 소식"}',
        },
        {
            "request_id": "candidate-1",
            "record_kind": "discovery_candidate",
            "status": "discovery_only",
            "payload_json": (
                '{"source_id":"publisher_rss","title":"나루토 팝업 행사","content_types":["event"]}'
            ),
        },
        {
            "request_id": "crawl-1",
            "record_kind": "fact",
            "status": "collected",
            "payload_json": (
                '{"verified":true,"fact_type":"event_period","source_ids":["naruto_official"]}'
            ),
        },
        {
            "request_id": "crawl-2",
            "record_kind": "fact",
            "status": "collected",
            "payload_json": (
                '{"verified":true,"fact_type":"general","source_ids":["naruto_official"]}'
            ),
        },
        {
            "request_id": "crawl-3",
            "record_kind": "fact",
            "status": "collected",
            "payload_json": (
                '{"verified":true,"fact_type":"character_profile",'
                '"source_ids":["naruto_official"]}'
            ),
        },
    ]

    coverage = build_work_type_coverage(
        catalog,
        [source],
        records,
        [{"menu_work_id": 11, "content_type": "event"}],
    )

    assert coverage[11]["event"] == {
        "active_sources": 1,
        "discovery_sources": 1,
        "pending_sources": 0,
        "candidates": 1,
        "verified_facts": 1,
        "humor_posts": 0,
        "trend_signals": 0,
        "ready": 1,
    }
    assert coverage[11]["news"]["ready"] == 0


def test_work_coverage_counts_real_posts_separately_from_aggregate_trends() -> None:
    catalog = load_work_catalog()
    source = Source(
        id="mastodon_social_anime",
        name="Mastodon public timeline",
        url="https://mastodon.social/api/v1/timelines/tag/anime",
        category="community_trend",
        content_types=[ContentType.HUMOR],
        role="trend",
        collection="api",
        status="trend_only",
        region="global",
    )
    records = [
        {
            "request_id": "thread-1",
            "record_kind": "trend_source",
            "status": "trend_only:kr_first",
            "payload_json": (
                '{"id":"mastodon_social_anime","title":"Naruto fan discussion",'
                '"source_id":"mastodon_social_anime","status":"humor_post_candidate"}'
            ),
        },
        {
            "request_id": "thread-1",
            "record_kind": "humor_idea",
            "status": "trend_only:kr_first",
            "payload_json": (
                '{"source_ids":["mastodon_social_anime"],'
                '"statement":"A sufficiently detailed discussion topic that fans can respond to.",'
                '"details":{"source_url":"https://mastodon.social/@fan/123"}}'
            ),
        },
        {
            "request_id": "snapshot-1",
            "record_kind": "trend_source",
            "status": "trend_only:kr_first",
            "payload_json": (
                '{"id":"mastodon_social_anime","title":"Naruto discussion tags",'
                '"source_id":"mastodon_social_anime","status":"trend_signal"}'
            ),
        },
    ]

    coverage = build_work_type_coverage(catalog, [source], records, [])

    # A global timeline is not falsely claimed as a work-specific registered source.
    assert coverage[11]["humor"]["active_sources"] == 0
    assert coverage[11]["humor"]["humor_posts"] == 1
    assert coverage[11]["humor"]["trend_signals"] == 1


def test_find_work_ids_accepts_exact_title_and_numeric_id() -> None:
    catalog = load_work_catalog()

    assert find_work_ids(catalog, "나루토") == [11]
    assert find_work_ids(catalog, "11") == [11]
    assert find_work_ids(catalog, "not a title") == []


def test_fact_type_taxonomy_covers_each_non_humor_content_type() -> None:
    expected = {
        "news",
        "event",
        "guide",
        "people",
        "goods",
        "review",
        "character",
        "curation",
        "music",
    }

    assert expected <= set(FACT_TYPE_TO_CONTENT_TYPE.values())
    assert FACT_TYPE_TO_CONTENT_TYPE["production"] == "humor"
    assert FACT_TYPE_TO_CONTENT_TYPE["making"] == "humor"


def test_malformed_expiry_fails_closed() -> None:
    from datetime import UTC, datetime

    assert _is_expired("not-a-date", datetime.now(UTC))
