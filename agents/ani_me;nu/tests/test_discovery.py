from ani_menu_agent.discovery import (
    DiscoveryItem,
    parse_anilist,
    parse_anime_schedule,
    parse_html_listing,
    parse_release_rss,
    rank_candidates,
)
from ani_menu_agent.sources.models import Source


def test_parse_anime_schedule() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Test</title><item>
    <title>Episode 1 is out</title><link>https://example.com/anime/1</link>
    <pubDate>Wed, 07 Oct 2026 10:00:00 GMT</pubDate><description>Now airing</description>
    </item></channel></rss>"""
    items = parse_anime_schedule(xml)
    assert len(items) == 1
    assert items[0].source_id == "anime_schedule_rss"
    assert items[0].title == "Episode 1 is out"


def test_parse_release_rss_filters_to_anime_related_candidates() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Press</title>
      <item><title>TV anime collaboration cafe announced</title>
        <link>https://example.com/anime</link><description>Limited event</description></item>
      <item><title>Generic collaboration cafe announced</title>
        <link>https://example.com/cafe</link><description>Food and goods</description></item>
      <item><title>New manga comic release</title>
        <link>https://example.com/comic</link><description>New comic volume</description></item>
      <item><title>New office chair released</title>
        <link>https://example.com/chair</link><description>Business product</description></item>
    </channel></rss>"""
    items = parse_release_rss(xml, "press_rss", "JP")
    assert len(items) == 1
    assert items[0].source_id == "press_rss"
    assert items[0].region == "JP"
    assert items[0].status == "discovery_only"


def test_release_rss_does_not_treat_manga_or_comics_alone_as_anime() -> None:
    xml = b"""<rss><channel><item>
    <title>New manga comic release</title>
    <link>https://example.com/comic</link><description>New volume</description>
    </item></channel></rss>"""
    assert parse_release_rss(xml, "press_rss", "JP") == []


def test_release_rss_ignores_product_animation_features() -> None:
    xml = """<rss><channel><item>
    <title>アニメーションを表示できるLEDボールが新登場</title>
    <link>https://example.com/led-toy</link>
    <description>LED display animations for a toy product</description>
    </item></channel></rss>""".encode()
    assert parse_release_rss(xml, "press_rss", "JP") == []


def test_parse_anilist() -> None:
    payload = {
        "data": {
            "Page": {
                "media": [
                    {
                        "id": 123,
                        "title": {"romaji": "Sample", "english": "Sample Anime", "native": "샘플"},
                        "siteUrl": "https://anilist.co/anime/123",
                        "startDate": {"year": 2027, "month": 4, "day": 1},
                    }
                ]
            }
        }
    }
    items = parse_anilist(payload)
    assert items[0].title == "Sample Anime"
    assert items[0].published_at == "2027-04-01"
    assert items[0].status == "discovery_only"
    assert items[0].region == "JP"


def test_parse_html_listing_keeps_only_same_host_article_candidates() -> None:
    source = Source(
        id="news_site",
        name="News",
        url="https://news.example/",
        category="news",
        role="discovery",
        collection="html",
        status="active",
        region="global",
    )
    html = """<main>
      <article><a href="/news/anime-1">New anime season announced</a></article>
      <article><a href="https://outside.example/story">Outside story title</a></article>
      <li><a href="/category/anime">Anime category overview</a></li>
      <article><a href="/news/anime-1">Duplicate anime story</a></article>
    </main>"""
    items = parse_html_listing(html, source)
    assert len(items) == 1
    assert items[0].title == "New anime season announced"
    assert str(items[0].url) == "https://news.example/news/anime-1"
    assert items[0].status == "discovery_only"


def test_html_listing_community_items_are_tagged_as_trend_signals() -> None:
    source = Source(
        id="community",
        name="Community",
        url="https://community.example/anime/",
        category="trend",
        role="trend",
        collection="html",
        status="trend_only",
        region="KR",
    )
    items = parse_html_listing(
        '<article><a href="/anime/thread/123">팬들이 기다리는 다음 시즌</a></article>',
        source,
    )
    assert items[0].source_role == "trend"
    assert items[0].status == "trend_signal"


def test_html_listing_rejects_site_navigation_outside_configured_board_path() -> None:
    source = Source(
        id="community",
        name="Community",
        url="https://community.example/family/211/board/300015",
        category="trend",
        role="trend",
        collection="html",
        status="trend_only",
        region="KR",
    )
    html = """<ul>
      <li><a href="/family/211">애니메이션 게시판</a></li>
      <li><a href="/family/211/board/300015/read/123">이번 분기 신작 이야기</a></li>
    </ul>"""
    items = parse_html_listing(html, source)
    assert len(items) == 1
    assert items[0].title == "이번 분기 신작 이야기"


def test_region_policy_is_configurable() -> None:
    items = [
        DiscoveryItem(
            id="jp",
            title="일본 행사",
            url="https://example.com/jp",
            source_id="x",
            region="JP",
        ),
        DiscoveryItem(
            id="kr",
            title="한국 행사",
            url="https://example.com/kr",
            source_id="x",
            region="KR",
        ),
    ]
    assert rank_candidates(items, "kr_first")[0].id == "kr"
    assert rank_candidates(items, "jp_first")[0].id == "jp"
