import json

from ani_menu_agent.discovery import DiscoveryItem
from ani_menu_agent.models import ContentType
from ani_menu_agent.source_discovery import (
    SourceCandidate,
    extract_source_candidates,
    promote_source_candidate,
    validate_source_candidate,
)
from ani_menu_agent.sources.models import Source


def _source(source_id: str, url: str) -> Source:
    return Source(
        id=source_id,
        name=source_id,
        url=url,
        category="news",
        content_types=[ContentType.NEWS],
        role="primary",
        collection="html",
        status="active",
        region="JP",
    )


def test_source_leads_are_separate_from_article_candidates_and_deduplicated() -> None:
    captures = [
        {
            "source_id": "anime_news_network",
            "url": "https://news.example/reports/1",
            "content_type": "news",
            "outbound_links": [
                {"url": "https://new-publisher.example/news/2026-announcement", "text": "원문"},
                {"url": "https://twitter.com/post/123", "text": "공유"},
            ],
        },
        {
            "source_id": "anime_news_network",
            "url": "https://news.example/reports/2",
            "content_type": "event",
            "outbound_links": [
                {"url": "https://new-publisher.example/events/collaboration", "text": "주최자"}
            ],
        },
    ]

    leads = extract_source_candidates(
        captures,
        [
            _source("anime_news_network", "https://news.example/"),
            _source("already_registered", "https://already.example/"),
        ],
    )

    assert len(leads) == 1
    lead = leads[0]
    assert lead.host == "new-publisher.example"
    assert set(lead.content_types) == {"news", "event"}
    assert len(lead.sample_urls["news"]) == 1
    assert "twitter.com" not in lead.host


def test_source_candidate_validation_requires_typed_listing_and_article_capture(
    monkeypatch,
) -> None:
    candidate = SourceCandidate(
        id="source-candidate:test",
        host="publisher.example",
        root_url="https://publisher.example/",
        sample_urls={"news": ["https://publisher.example/news/2027-announcement"]},
        content_types=["news"],
    )
    item = DiscoveryItem(
        id="item-1",
        title="2027 anime announcement",
        url="https://publisher.example/news/2027-announcement",
        source_id="source_probe",
        content_types=["news"],
    )

    monkeypatch.setattr(
        "ani_menu_agent.source_discovery._get_html",
        lambda *_args, **_kwargs: ("<html></html>", "https://publisher.example/"),
    )
    monkeypatch.setattr(
        "ani_menu_agent.source_discovery.parse_html_listing",
        lambda *_args, **_kwargs: [item],
    )

    class Page:
        title = "2027 anime announcement"
        text = "An anime announcement for the 2027 season is now confirmed." * 4

    monkeypatch.setattr(
        "ani_menu_agent.source_discovery.collect_official_page",
        lambda *_args, **_kwargs: Page(),
    )

    result = validate_source_candidate(candidate)

    assert result.status == "validated"
    assert result.content_types == ["news"]
    assert result.validation["checks_by_type"]["news"]["captured_articles"] == 1


def test_promote_only_validated_source_and_write_type_map(tmp_path) -> None:
    registry_path = tmp_path / "sources.json"
    type_map_path = tmp_path / "source_type_map.json"
    registry_path.write_text(json.dumps({"version": 1, "sources": []}), encoding="utf-8")
    type_map_path.write_text(json.dumps({"version": 1, "sources": {}}), encoding="utf-8")
    candidate = SourceCandidate(
        id="source-candidate:test",
        host="publisher.example",
        root_url="https://publisher.example/",
        content_types=["news", "event"],
        status="validated",
        last_checked_at="2026-10-25T00:00:00+00:00",
    )

    source_id = promote_source_candidate(
        candidate,
        registry_path=registry_path,
        type_map_path=type_map_path,
    )

    source_config = json.loads(registry_path.read_text(encoding="utf-8"))["sources"][0]
    type_map = json.loads(type_map_path.read_text(encoding="utf-8"))["sources"]
    assert source_config["id"] == source_id
    assert source_config["role"] == "discovery"
    assert type_map[source_id] == ["event", "news"]


def test_unvalidated_source_candidate_cannot_be_promoted(tmp_path) -> None:
    candidate = SourceCandidate(
        id="source-candidate:pending",
        host="pending.example",
        root_url="https://pending.example/",
    )

    try:
        promote_source_candidate(
            candidate,
            registry_path=tmp_path / "missing-sources.json",
            type_map_path=tmp_path / "missing-map.json",
        )
    except ValueError as exc:
        assert "검증 통과" in str(exc)
    else:
        raise AssertionError("unvalidated source candidates must not be promoted")
