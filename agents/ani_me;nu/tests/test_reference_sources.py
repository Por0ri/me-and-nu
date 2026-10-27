from __future__ import annotations

import respx
from httpx import Response

from ani_menu_agent.discovery import (
    collect_candidates,
    parse_ann_search,
    parse_kitsu_characters,
    parse_kitsu_rating_ranking,
    parse_kitsu_search,
    parse_wikipedia_search,
)
from ani_menu_agent.sources.models import SourceRegistry
from ani_menu_agent.sources.page_collector import collect_reference_page
from ani_menu_agent.sources.registry import load_source_registry


def _source(source_id: str):
    return next(source for source in load_source_registry().sources if source.id == source_id)


def test_kitsu_search_parser_builds_work_detail_candidates() -> None:
    source = _source("kitsu_anime_catalog")
    items = parse_kitsu_search(
        {
            "data": [
                {"id": "20", "attributes": {"canonicalTitle": "Naruto"}},
                {"id": "21", "attributes": {"canonicalTitle": "Naruto Shippuden"}},
            ]
        },
        source,
    )

    assert [item.title for item in items] == ["Naruto", "Naruto Shippuden"]
    assert str(items[0].url) == "https://kitsu.io/anime/20"
    assert items[0].source_id == source.id


def test_kitsu_character_parser_links_character_to_exact_anime_relationship() -> None:
    source = _source("kitsu_anime_catalog")
    items = parse_kitsu_characters(
        {
            "data": [
                {
                    "id": "21733",
                    "attributes": {"role": "main"},
                    "relationships": {
                        "character": {"data": {"type": "characters", "id": "220"}}
                    },
                }
            ],
            "included": [
                {
                    "id": "220",
                    "type": "characters",
                    "attributes": {"canonicalName": "Sasuke Uchiha"},
                }
            ],
        },
        source,
        {"attributes": {"canonicalTitle": "Naruto Shippuden"}},
    )

    assert len(items) == 1
    assert items[0].title == "Sasuke Uchiha — Naruto Shippuden"
    assert items[0].content_types == ["character"]
    assert str(items[0].url) == (
        "https://kitsu.io/api/edge/media-characters/21733?include=character,media"
    )


def test_kitsu_rating_ranking_is_explicitly_attributed_and_uses_aggregate_sample_size() -> None:
    source = _source("kitsu_anime_catalog")
    items = parse_kitsu_rating_ranking(
        {
            "data": [
                {
                    "id": "20",
                    "attributes": {
                        "canonicalTitle": "Naruto",
                        "averageRating": "82.1",
                        "ratingRank": 12,
                        "userCount": 12500,
                    },
                }
            ]
        },
        source,
        10,
    )

    assert len(items) == 1
    assert items[0].content_types == ["curation"]
    assert "전체 애니 팬덤의 보편적 선호를 뜻하지 않습니다" in items[0].summary
    assert "12500" in items[0].summary


def test_wikipedia_search_parser_builds_canonical_article_candidates() -> None:
    source = _source("wikipedia_en_anime")
    items = parse_wikipedia_search(
        {
            "query": {
                "search": [
                    {"title": "Naruto (TV series)", "snippet": "Anime series <b>about</b> Naruto"}
                ]
            }
        },
        source,
    )

    assert len(items) == 1
    assert str(items[0].url) == "https://en.wikipedia.org/wiki/Naruto_%28TV_series%29"
    assert "about Naruto" in items[0].summary


def test_ann_search_parser_ignores_navigation_and_returns_only_entry_links() -> None:
    source = _source("ann_encyclopedia")
    html = """
    <nav><a href="/encyclopedia/anime.php">Anime</a></nav>
    <main>
      <a href="/encyclopedia/anime.php?id=1825">Naruto (TV)</a>
      <a href="/encyclopedia/anime.php?id=7293">Naruto Shippuden (TV)</a>
      <a href="https://other.example/encyclopedia/anime.php?id=1">wrong host title</a>
    </main>
    """

    items = parse_ann_search(html, source, content_type="guide")

    assert [item.title for item in items] == ["Naruto (TV)", "Naruto Shippuden (TV)"]
    assert all(item.source_id == source.id for item in items)


@respx.mock
def test_kitsu_reference_capture_normalizes_only_public_catalog_fields(monkeypatch) -> None:
    source = _source("kitsu_anime_catalog")
    monkeypatch.setattr("ani_menu_agent.sources.page_collector.pace_host", lambda *_: None)
    route = respx.get("https://kitsu.io/api/edge/anime/20").mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "id": "20",
                    "attributes": {
                        "canonicalTitle": "Naruto",
                        "startDate": "2002-10-03",
                        "episodeCount": 220,
                        "averageRating": "82.1",
                        "synopsis": "A young ninja seeks recognition.",
                        "userCount": 100000,
                    },
                }
            },
        )
    )

    capture = collect_reference_page(source, "https://kitsu.io/anime/20")

    assert route.called
    assert capture.title == "Naruto"
    assert "averageRating" in capture.text
    assert "userCount" not in capture.text
    assert capture.image_urls == []


@respx.mock
def test_wikipedia_reference_capture_uses_single_article_extract(monkeypatch) -> None:
    source = _source("wikipedia_en_anime")
    monkeypatch.setattr("ani_menu_agent.sources.page_collector.pace_host", lambda *_: None)
    route = respx.get("https://en.wikipedia.org/w/api.php").mock(
        return_value=Response(
            200,
            json={
                "query": {
                    "pages": [
                        {
                            "title": "Naruto (TV series)",
                            "fullurl": "https://en.wikipedia.org/wiki/Naruto_(TV_series)",
                            "extract": "Naruto is a Japanese anime television series.",
                        }
                    ]
                }
            },
        )
    )

    capture = collect_reference_page(
        source, "https://en.wikipedia.org/wiki/Naruto_(TV_series)"
    )

    assert route.called
    assert capture.title == "Naruto (TV series)"
    assert "Japanese anime television series" in capture.text
    assert str(capture.url) == "https://en.wikipedia.org/wiki/Naruto_(TV_series)"


@respx.mock
def test_work_title_discovery_queries_reference_sources_and_merges_results(monkeypatch) -> None:
    registry = load_source_registry()
    selected = [
        _source("kitsu_anime_catalog"),
        _source("wikipedia_en_anime"),
        _source("ann_encyclopedia"),
    ]
    monkeypatch.setattr(
        "ani_menu_agent.discovery.load_source_registry",
        lambda: SourceRegistry(version=registry.version, sources=selected),
    )
    monkeypatch.setattr(
        "ani_menu_agent.discovery._get_html",
        lambda *_args, **_kwargs: (
            '<main><a href="/encyclopedia/anime.php?id=1825">Naruto (TV)</a></main>',
            "https://www.animenewsnetwork.com/encyclopedia/search/name?only=anime&q=Naruto",
        ),
    )
    respx.get("https://kitsu.io/api/edge/anime").mock(
        return_value=Response(
            200,
            json={"data": [{"id": "20", "attributes": {"canonicalTitle": "Naruto"}}]},
        )
    )
    respx.get("https://en.wikipedia.org/w/api.php").mock(
        return_value=Response(
            200,
            json={"query": {"search": [{"title": "Naruto (TV series)", "snippet": "anime"}]}},
        )
    )

    items, errors = collect_candidates(
        limit=10,
        region_policy="mixed",
        content_type="guide",
        work_title="Naruto",
    )

    assert not errors
    assert {item.source_id for item in items} == {
        "kitsu_anime_catalog",
        "wikipedia_en_anime",
        "ann_encyclopedia",
    }
    assert any("Naruto" in item.title for item in items)


@respx.mock
def test_character_discovery_fetches_kitsu_anime_relationships(monkeypatch) -> None:
    source = _source("kitsu_anime_catalog")
    registry = load_source_registry()
    monkeypatch.setattr(
        "ani_menu_agent.discovery.load_source_registry",
        lambda: SourceRegistry(version=registry.version, sources=[source]),
    )
    monkeypatch.setattr("ani_menu_agent.discovery._respect_interval", lambda *_: None)
    respx.get("https://kitsu.io/api/edge/anime").mock(
        return_value=Response(
            200,
            json={"data": [{"id": "1555", "attributes": {"canonicalTitle": "Naruto"}}]},
        )
    )
    respx.get("https://kitsu.io/api/edge/anime/1555/characters").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "21733",
                        "attributes": {"role": "main"},
                        "relationships": {
                            "character": {"data": {"type": "characters", "id": "220"}}
                        },
                    }
                ],
                "included": [
                    {
                        "id": "220",
                        "type": "characters",
                        "attributes": {"canonicalName": "Sasuke Uchiha"},
                    }
                ],
            },
        )
    )

    items, errors = collect_candidates(
        content_type="character", work_title="Naruto", limit=5
    )

    assert errors == []
    assert len(items) == 1
    assert items[0].title == "Sasuke Uchiha — Naruto"
    assert items[0].content_types == ["character"]
    assert "/media-characters/21733" in str(items[0].url)


@respx.mock
def test_kitsu_character_relationship_capture_preserves_work_and_role(monkeypatch) -> None:
    source = _source("kitsu_anime_catalog")
    monkeypatch.setattr("ani_menu_agent.sources.page_collector.pace_host", lambda *_: None)
    url = "https://kitsu.io/api/edge/media-characters/21733?include=character,media"
    route = respx.get(url).mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "id": "21733",
                    "attributes": {"role": "main"},
                    "relationships": {
                        "character": {"data": {"type": "characters", "id": "220"}},
                        "media": {"data": {"type": "anime", "id": "1555"}},
                    },
                },
                "included": [
                    {
                        "id": "220",
                        "type": "characters",
                        "attributes": {
                            "canonicalName": "Sasuke Uchiha",
                            "description": "A shinobi from the Uchiha clan.",
                        },
                    },
                    {
                        "id": "1555",
                        "type": "anime",
                        "attributes": {"canonicalTitle": "Naruto Shippuden"},
                    },
                ],
            },
        )
    )

    capture = collect_reference_page(source, url)

    assert route.called
    assert capture.title == "Sasuke Uchiha"
    assert "Naruto Shippuden" in capture.text
    assert "main" in capture.text
    assert "Uchiha clan" in capture.text


@respx.mock
def test_kitsu_rating_capture_keeps_rank_rating_and_aggregate_count(monkeypatch) -> None:
    source = _source("kitsu_anime_catalog")
    monkeypatch.setattr("ani_menu_agent.sources.page_collector.pace_host", lambda *_: None)
    url = "https://kitsu.io/api/edge/anime?sort=-averageRating&page[limit]=1"
    route = respx.get(url).mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "20",
                        "attributes": {
                            "canonicalTitle": "Naruto",
                            "averageRating": "82.1",
                            "ratingRank": 12,
                            "userCount": 12500,
                        },
                    }
                ]
            },
        )
    )

    capture = collect_reference_page(source, url)

    assert route.called
    assert capture.title == "Kitsu 사용자 평점 랭킹"
    assert "82.1" in capture.text
    assert "12500" in capture.text
