from ani_menu_agent.discovery import (
    DiscoveryItem,
    _collectable_sources_for_type,
    _inferred_candidate_types,
    collect_candidates,
    parse_anilist,
    parse_anime_schedule,
    parse_html_listing,
    parse_mastodon_tag_timeline,
    parse_release_rss,
    rank_candidates,
    supports_discovery_adapter,
)
from ani_menu_agent.models import ContentType
from ani_menu_agent.sources.models import Source, SourceRegistry


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


def test_mastodon_trend_parser_stores_only_aggregated_tags_not_post_text_or_accounts() -> None:
    from ani_menu_agent.models import ContentType

    source = Source(
        id="mastodon_test",
        name="Mastodon test",
        url="https://mastodon.example/api/v1/timelines/tag/anime",
        category="community_trend",
        content_types=[ContentType.HUMOR],
        role="trend",
        collection="api",
        status="trend_only",
        region="global",
    )
    posts = [
        {
            "id": "post-a",
            "created_at": "2026-10-21T01:00:00Z",
            "visibility": "public",
            "content": "PRIVATE USER TEXT THAT MUST NOT BE SAVED",
            "account": {"acct": "private-user"},
            "tags": [{"name": "anime"}, {"name": "bleach"}],
        },
        {
            "id": "post-b",
            "created_at": "2026-10-21T02:00:00Z",
            "visibility": "public",
            "content": "ANOTHER PRIVATE POST",
            "account": {"acct": "another-user"},
            "tags": [{"name": "Bleach"}],
        },
    ]

    items = parse_mastodon_tag_timeline(posts, source)

    assert len(items) == 1
    assert items[0].content_types == ["humor"]
    assert "#Bleach" in items[0].title
    assert "PRIVATE USER TEXT" not in items[0].model_dump_json()
    assert "private-user" not in items[0].model_dump_json()


def test_mastodon_parser_fails_closed_for_nonpublic_or_sensitive_posts(monkeypatch) -> None:
    source = Source(
        id="mastodon_test",
        name="Mastodon test",
        url="https://mastodon.example/api/v1/timelines/tag/anime",
        category="community_trend",
        content_types=[ContentType.HUMOR],
        role="trend",
        collection="api",
        status="trend_only",
        region="global",
    )
    monkeypatch.setattr("ani_menu_agent.discovery._catalog_work_titles_for_tags", lambda _tags: [])
    monkeypatch.setattr(
        "ani_menu_agent.discovery._catalog_work_titles_in_text", lambda _body: ["Bleach"]
    )
    body = "Who would win in a fight? " + ("Bleach fandom debate. " * 5)
    posts = [
        {
            "id": "unlisted",
            "visibility": "unlisted",
            "content": body,
            "url": "https://mastodon.example/@a/1",
            "tags": [{"name": "meme"}],
        },
        {
            "id": "sensitive",
            "visibility": "public",
            "sensitive": True,
            "content": body,
            "url": "https://mastodon.example/@b/2",
            "tags": [{"name": "meme"}],
        },
    ]

    assert parse_mastodon_tag_timeline(posts, source) == []


def test_mastodon_parser_accepts_engaged_english_discussion_and_skips_bots(monkeypatch) -> None:
    source = Source(
        id="mastodon_test",
        name="Mastodon test",
        url="https://mastodon.example/api/v1/timelines/tag/Naruto",
        category="community_trend",
        content_types=[ContentType.HUMOR],
        role="trend",
        collection="api",
        status="trend_only",
        region="global",
    )
    monkeypatch.setattr(
        "ani_menu_agent.discovery._catalog_work_titles_for_tags", lambda _tags: ["Naruto"]
    )
    monkeypatch.setattr("ani_menu_agent.discovery._catalog_work_titles_in_text", lambda _body: [])
    body = (
        "<p>I keep thinking about Naruto's choices in the final arc. "
        "Which character had the most convincing growth, and why? "
        "I would love to hear how other fans read that ending.</p>"
    )
    posts = [
        {
            "id": "bot-post",
            "visibility": "public",
            "content": body,
            "url": "https://mastodon.example/@bot/1",
            "tags": [{"name": "Naruto"}, {"name": "bot"}],
            "favourites_count": 100,
            "reblogs_count": 100,
        },
        {
            "id": "human-post",
            "visibility": "public",
            "content": body + " @someone user@example.org",
            "url": "https://mastodon.example/@user/2",
            "account": {"acct": "user@mastodon.example"},
            "tags": [{"name": "Naruto"}],
            "favourites_count": 2,
            "reblogs_count": 2,
        },
    ]

    items = parse_mastodon_tag_timeline(posts, source)
    post_items = [item for item in items if item.is_community_post]

    assert len(post_items) == 1
    assert post_items[0].source_id == "mastodon_test"
    assert "someone" not in post_items[0].summary
    assert "user@example.org" not in post_items[0].summary
    assert "user@mastodon.example" not in post_items[0].model_dump_json()


def test_work_title_routes_mastodon_to_work_specific_tag_timeline(monkeypatch) -> None:
    source = Source(
        id="mastodon_test",
        name="Mastodon test",
        url="https://mastodon.example/api/v1/timelines/tag/anime",
        category="community_trend",
        content_types=[ContentType.HUMOR],
        role="trend",
        collection="api",
        status="trend_only",
        region="global",
    )
    monkeypatch.setattr(
        "ani_menu_agent.discovery.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[source]),
    )
    requested: list[str] = []

    class Response:
        def json(self):
            return []

    def fake_get(_client, url, _interval):
        requested.append(url)
        return Response()

    monkeypatch.setattr("ani_menu_agent.discovery._get_with_backoff", fake_get)

    items, errors = collect_candidates(content_type="humor", work_title="My Hero Academia")

    assert errors == []
    assert items == []
    assert requested == ["https://mastodon.example/api/v1/timelines/tag/MyHeroAcademia"]


def test_source_report_distinguishes_successful_empty_from_failed(monkeypatch) -> None:
    source = Source(
        id="mastodon_test",
        name="Mastodon test",
        url="https://mastodon.example/api/v1/timelines/tag/anime",
        category="community_trend",
        content_types=[ContentType.HUMOR],
        role="trend",
        collection="api",
        status="trend_only",
        region="global",
    )
    monkeypatch.setattr(
        "ani_menu_agent.discovery.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[source]),
    )

    class Response:
        def json(self):
            return []

    monkeypatch.setattr(
        "ani_menu_agent.discovery._get_with_backoff",
        lambda *_args: Response(),
    )
    report = []

    items, errors = collect_candidates(content_type="humor", source_report=report)

    assert items == []
    assert errors == []
    assert report == [
        {
            "source_id": "mastodon_test",
            "status": "succeeded_empty",
            "attempted": True,
            "candidate_count": 0,
            "error": "",
        }
    ]


def test_collect_candidates_rejects_unknown_content_type() -> None:
    import pytest

    with pytest.raises(ValueError, match="지원하지 않는 콘텐츠 유형"):
        collect_candidates(content_type="not-a-type")


def test_type_filter_only_selects_sources_mapped_to_that_type(monkeypatch) -> None:
    from ani_menu_agent.models import ContentType

    sources = [
        Source(
            id="news_feed",
            name="News",
            url="https://news.example/feed.xml",
            category="news",
            content_types=[ContentType.NEWS],
            role="discovery",
            collection="rss",
            status="active",
            region="JP",
        ),
        Source(
            id="event_feed",
            name="Events",
            url="https://events.example/feed.xml",
            category="event",
            content_types=[ContentType.EVENT],
            role="discovery",
            collection="rss",
            status="active",
            region="JP",
        ),
    ]
    monkeypatch.setattr(
        "ani_menu_agent.discovery.load_source_registry",
        lambda: SourceRegistry(version=1, sources=sources),
    )

    active = _collectable_sources_for_type("news")
    assert set(active) == {"news_feed"}


def test_official_staff_interview_is_classified_as_people_and_behind() -> None:
    from ani_menu_agent.discovery import _inferred_candidate_types

    source = Source(
        id="official_interviews",
        name="Official staff interviews",
        url="https://official.example/news/",
        category="official_news",
        content_types=[ContentType.PEOPLE, ContentType.BEHIND],
        role="primary",
        collection="html",
        status="active",
        region="JP",
    )
    item = DiscoveryItem(
        id="interview-1",
        title="制作陣インタビュー：演出の舞台裏",
        url="https://official.example/news/interview-1",
        source_id=source.id,
        content_types=["people", "behind"],
    )

    assert _inferred_candidate_types(item, source) == {"people", "behind"}


def test_japanese_plush_and_lottery_release_is_classified_as_goods() -> None:
    from ani_menu_agent.discovery import _inferred_candidate_types

    source = Source(
        id="press_releases",
        name="Press Releases",
        url="https://press.example/",
        category="news",
        content_types=[ContentType.NEWS, ContentType.GOODS],
        role="primary",
        collection="html",
        status="active",
        region="JP",
    )
    item = DiscoveryItem(
        id="goods-release",
        title="TV anime character ぬいぐるみとキャラクターくじの販売が決定",
        url="https://press.example/news/lottery-plush-release",
        source_id=source.id,
        content_types=["news", "goods"],
    )

    assert "goods" in _inferred_candidate_types(item, source)


def test_source_without_an_implemented_adapter_is_not_reported_as_collectable() -> None:
    from ani_menu_agent.models import ContentType

    source = Source(
        id="unsupported_api",
        name="Unsupported API",
        url="https://api.example/v1",
        category="unknown_api",
        content_types=[ContentType.NEWS],
        role="discovery",
        collection="api",
        status="active",
        region="global",
    )

    assert not supports_discovery_adapter(source)


def test_source_can_be_crawlable_without_being_a_discovery_root() -> None:
    from ani_menu_agent.models import ContentType

    source = Source(
        id="press_release_pages",
        name="Press Release Detail Pages",
        url="https://press.example/",
        category="press_release",
        content_types=[ContentType.GOODS],
        role="primary",
        collection="html",
        status="active",
        region="JP",
        discovery_enabled=False,
    )

    assert not supports_discovery_adapter(source)


def test_active_primary_html_source_can_discover_its_own_article_urls(monkeypatch) -> None:
    from ani_menu_agent.models import ContentType

    source = Source(
        id="official_news",
        name="Official News",
        url="https://official.example/news/",
        category="official_news",
        content_types=[ContentType.NEWS],
        role="primary",
        collection="html",
        status="active",
        region="JP",
    )
    monkeypatch.setattr(
        "ani_menu_agent.discovery.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[source]),
    )
    monkeypatch.setattr(
        "ani_menu_agent.discovery._get_html",
        lambda _url, _source, timeout: (
            '<article><a href="/news/season-2">Second season announced today</a></article>',
            "https://official.example/news/",
        ),
    )

    items, errors = collect_candidates(content_type="news")

    assert errors == []
    assert [item.source_id for item in items] == ["official_news"]
    assert str(items[0].url) == "https://official.example/news/season-2"
    assert items[0].status == "discovery_only"


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


def test_html_listing_does_not_return_the_listing_or_archive_page_as_an_article() -> None:
    source = Source(
        id="news_archive",
        name="News archive",
        url="https://news.example/news/archive",
        category="news",
        role="discovery",
        collection="html",
        status="active",
        region="global",
    )
    html = """
    <main>
      <a href="/news/archive">Anime news archive</a>
      <a href="/news/tag/anime">Anime news tag</a>
      <a href="/news/story-42">New season announcement</a>
    </main>
    """

    items = parse_html_listing(html, source)

    assert [str(item.url) for item in items] == ["https://news.example/news/story-42"]


def test_taxonomy_listing_can_discover_root_level_canonical_articles() -> None:
    source = Source(
        id="editorial_review",
        name="Editorial reviews",
        url="https://reviews.example/category/anime/",
        category="review",
        content_types=[],
        role="primary",
        collection="html",
        status="active",
        region="global",
    )

    items = parse_html_listing(
        """
        <main>
          <article><a href="/spring-2027-premiere-review/">Spring 2027 premiere review</a></article>
          <a href="/author/editor/">Staff Editor</a>
          <a href="/category/news/">News archive index</a>
        </main>
        """,
        source,
    )

    assert [str(item.url) for item in items] == [
        "https://reviews.example/spring-2027-premiere-review"
    ]


def test_review_listing_does_not_route_polls_or_user_ranking_ui_as_reviews() -> None:
    source = Source(
        id="editorial_review",
        name="Editorial reviews",
        url="https://reviews.example/category/review/",
        category="review",
        content_types=[ContentType.REVIEW, ContentType.CURATION],
        role="editorial",
        collection="html",
        status="active",
        region="global",
    )
    items = parse_html_listing(
        """
        <main>
          <article><a href="/blue-box-season-2-review/">Blue Box Season 2 Review</a></article>
          <a href="/db/community/rankings/anime/ranked-list/create">Create Ranking</a>
          <a href="/polls">Latest Polls</a>
          <a href="/polls/rankings/anime-of-the-week">Anime Trends Poll</a>
        </main>
        """,
        source,
    )

    assert {str(item.url) for item in items} == {
        "https://reviews.example/blue-box-season-2-review",
        "https://reviews.example/polls/rankings/anime-of-the-week",
    }
    review = next(item for item in items if "blue-box" in str(item.url))
    poll = next(item for item in items if "/polls/" in str(item.url))
    assert _inferred_candidate_types(review, source) == {"review"}
    assert _inferred_candidate_types(poll, source) == {"curation"}


def test_root_listing_discovers_article_slugs_but_skips_static_pages() -> None:
    source = Source(
        id="editorial_home",
        name="Editorial home",
        url="https://reviews.example/",
        category="review",
        role="primary",
        collection="html",
        status="active",
        region="global",
    )

    items = parse_html_listing(
        """
        <main>
          <a href="/great-anime-season-premiere-review/">Great Anime Season Premiere Review</a>
          <a href="/submissions/">Submissions</a>
        </main>
        """,
        source,
    )

    assert [str(item.url) for item in items] == [
        "https://reviews.example/great-anime-season-premiere-review"
    ]


def test_article_card_links_are_prioritized_over_navigation_when_limited() -> None:
    source = Source(
        id="anime_media",
        name="Anime Media",
        url="https://media.example/category/anime/",
        category="review",
        role="primary",
        collection="html",
        status="active",
        region="global",
    )

    items = parse_html_listing(
        """
        <nav><a href="/database/">Database</a></nav>
        <main>
          <article>
            <a href="/anime-premiere-review/">Anime Premiere Review: A Strong Start</a>
          </article>
        </main>
        """,
        source,
        limit=1,
    )

    assert len(items) == 1
    assert items[0].title == "Anime Premiere Review: A Strong Start"


def test_html_listing_supports_links_outside_semantic_article_wrappers() -> None:
    source = Source(
        id="official_news",
        name="Official news",
        url="https://anime.example/news/index.html",
        category="anime",
        role="primary",
        collection="html",
        status="active",
        region="JP",
    )

    items = parse_html_listing(
        '<div class="card"><a href="/news/2026/new-season">'
        "New season production announcement</a></div>",
        source,
    )

    assert [str(item.url) for item in items] == ["https://anime.example/news/2026/new-season"]


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


def test_candidate_ranking_round_robins_sources_to_avoid_source_starvation() -> None:
    items = [
        DiscoveryItem(
            id=f"alpha-{index}",
            title=f"Alpha {index}",
            url=f"https://alpha.example/{index}",
            source_id="alpha",
            region="global",
        )
        for index in range(4)
    ] + [
        DiscoveryItem(
            id="beta-1",
            title="Beta result",
            url="https://beta.example/1",
            source_id="beta",
            region="global",
        )
    ]

    ranked = rank_candidates(items, region_policy="mixed")

    assert [item.source_id for item in ranked[:3]] == ["alpha", "beta", "alpha"]


def test_candidate_ranking_interleaves_work_titles_without_a_per_work_quota() -> None:
    items = [
        DiscoveryItem(
            id="naruto-1",
            title="Naruto latest episode review",
            url="https://reviews.example/naruto-1",
            source_id="reviews",
            region="global",
        ),
        DiscoveryItem(
            id="one-piece-1",
            title="ONE PIECE new episode review",
            url="https://reviews.example/one-piece-1",
            source_id="reviews",
            region="global",
        ),
        DiscoveryItem(
            id="naruto-2",
            title="Naruto new season review",
            url="https://reviews.example/naruto-2",
            source_id="reviews",
            region="global",
        ),
    ]

    ranked = rank_candidates(items, region_policy="mixed")

    assert [item.id for item in ranked] == ["naruto-1", "one-piece-1", "naruto-2"]
