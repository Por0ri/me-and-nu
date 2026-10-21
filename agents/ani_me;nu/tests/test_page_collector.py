from bs4 import BeautifulSoup

from ani_menu_agent.sources.models import Source
from ani_menu_agent.sources.page_collector import (
    _image_candidates,
    _is_decorative_image_url,
    _published_date_from_html,
    collect_official_page,
)


def test_decorative_site_assets_are_excluded_from_image_candidates() -> None:
    assert _is_decorative_image_url("https://orb-cafe.com/assets/images/common/logo.svg")
    assert _is_decorative_image_url("https://orb-cafe.com/assets/icon_search.png")
    assert not _is_decorative_image_url(
        "https://orb-cafe.com/wp-content/uploads/frieren_main_info_kv.webp"
    )


def test_image_candidates_ignore_sidebar_and_related_article_images() -> None:
    soup = BeautifulSoup(
        '<html><head><meta property="og:image" content="/assets/ogp.jpg"></head>'
        "<body><main><article><h1>Official event</h1>"
        '<img src="/uploads/event-main.jpg" alt="Event visual"></article></main>'
        '<aside><img src="/uploads/unrelated-recommendation.jpg" alt="Related"></aside>'
        "</body></html>",
        "html.parser",
    )

    image_urls, _ = _image_candidates(soup, "https://anime.example/news/123/")

    assert image_urls == ["https://anime.example/uploads/event-main.jpg"]


def test_specific_article_body_wins_over_broad_main_container() -> None:
    soup = BeautifulSoup(
        '<main><div class="news_detail_cont">'
        '<img src="/uploads/story-image.jpg" alt="Story">'
        '<a href="/news/related/">related story</a></div>'
        '<aside><img src="/uploads/recommended.jpg" alt="Recommendation">'
        '<a href="/news/unrelated/">recommendation</a></aside></main>',
        "html.parser",
    )

    image_urls, _ = _image_candidates(soup, "https://anime.example/news/123/")

    assert image_urls == ["https://anime.example/uploads/story-image.jpg"]


def test_image_candidates_ignore_linked_related_news_cards_inside_article_scope() -> None:
    soup = BeautifulSoup(
        "<html><body><article><h1>Current story</h1>"
        '<img src="/uploads/current-story.jpg" alt="Current story">'
        '<ul><li class="el-card-block__item">'
        '<a href="/news/other-story/"><img src="/uploads/other-story.jpg" '
        'alt="Unrelated story"></a></li></ul></article></body></html>',
        "html.parser",
    )

    image_urls, _ = _image_candidates(soup, "https://anime.example/news/current-story/")

    assert "https://anime.example/uploads/current-story.jpg" in image_urls
    assert "https://anime.example/uploads/other-story.jpg" not in image_urls


def test_image_candidates_prefer_larger_official_cms_rendition() -> None:
    soup = BeautifulSoup(
        '<article><img src="/news/SYS/CONTENTS/abc123/w707" alt="Official visual"></article>',
        "html.parser",
    )

    image_urls, descriptions = _image_candidates(soup, "https://anime.example/news/123/")

    assert image_urls[0] == "https://anime.example/news/SYS/CONTENTS/abc123/w1200"
    assert "https://anime.example/news/SYS/CONTENTS/abc123/w707" not in image_urls
    assert descriptions[image_urls[0]] == "Official visual"


def test_publication_date_uses_json_ld_when_open_graph_date_is_missing() -> None:
    soup = BeautifulSoup(
        '<script type="application/ld+json">'
        '{"@type":"NewsArticle","datePublished":"2026-10-19T10:00:00+09:00"}'
        "</script>",
        "html.parser",
    )

    assert _published_date_from_html(soup) == "2026-10-19T10:00:00+09:00"


def test_publication_date_does_not_guess_from_event_date_in_article_body() -> None:
    soup = BeautifulSoup("<article>開催日: 2026年10月30日</article>", "html.parser")

    assert _published_date_from_html(soup) is None


def test_publication_date_reads_exact_date_near_article_headline() -> None:
    soup = BeautifulSoup(
        "<main><p class='generated-class'>2026/09/29</p><h1>Official news</h1>"
        "<p>Event begins 2026/10/08</p></main>",
        "html.parser",
    )

    assert _published_date_from_html(soup) == "2026-09-29"


def test_page_capture_follows_trusted_article_links_for_additional_official_images(
    monkeypatch,
) -> None:
    from ani_menu_agent.sources import page_collector

    source = Source(
        id="anime_official",
        name="공식 사이트",
        url="https://anime.example/news/",
        category="anime",
        role="primary",
        collection="html",
        status="active",
        region="JP",
        allowed_asset_hosts=["anime.example"],
    )
    seed_html = """
    <html><head><title>공식 소식</title>
    <meta property="article:published_time" content="2026-10-14T09:00:00+09:00">
    <meta property="og:image" content="/media/main-visual.jpg"></head><body>
    <a href="/news/2026/12345/">특집 상세 보기</a>
    <a href="https://untrusted.example/news/999/">외부 링크</a>
    <img src="/assets/icon-small.png" width="32" height="32" alt="아이콘">
    </body></html>
    """
    detail_html = """
    <html><head><meta property="og:image" content="/media/original-event-visual.webp"></head>
    <body><img src="/media/thumbnail-event.jpg" width="120" height="80" alt="행사 사진">
    </body></html>
    """
    requested: list[str] = []

    def fake_get_html(url, _source, _timeout):
        requested.append(url)
        return (seed_html if url.endswith("/news/") else detail_html), url

    monkeypatch.setattr(page_collector, "_get_html", fake_get_html)

    capture = collect_official_page(source, "https://anime.example/news/")

    assert "https://anime.example/news/2026/12345/" in requested
    assert capture.published_at == "2026-10-14T09:00:00+09:00"
    assert not any("untrusted.example" in url for url in requested)
    assert "https://anime.example/media/original-event-visual.webp" in {
        str(url) for url in capture.image_urls
    }
    assert (
        capture.image_source_pages["https://anime.example/media/original-event-visual.webp"]
        == "https://anime.example/news/2026/12345/"
    )


def test_page_capture_can_disable_related_images_for_content_generation(monkeypatch) -> None:
    from ani_menu_agent.sources import page_collector

    source = Source(
        id="anime_official",
        name="Official site",
        url="https://anime.example/news/",
        category="anime",
        role="primary",
        collection="html",
        status="active",
        region="JP",
        allowed_asset_hosts=["anime.example"],
    )
    html = """
    <html><head><title>Specific article</title></head><body>
      <a href="/news/other-story/">Related story with unrelated image</a>
      <img src="/media/current-story.jpg" alt="Current story visual">
    </body></html>
    """
    requested: list[str] = []

    def fake_get_html(url, _source, _timeout):
        requested.append(url)
        return html, url

    monkeypatch.setattr(page_collector, "_get_html", fake_get_html)
    capture = collect_official_page(
        source,
        "https://anime.example/news/current-story/",
        expand_related_pages=False,
    )

    assert requested == ["https://anime.example/news/current-story/"]
    assert [str(url) for url in capture.image_urls] == [
        "https://anime.example/media/current-story.jpg"
    ]
