import json
from io import BytesIO

import pytest
from PIL import Image

from ani_menu_agent.cli import (
    _add_same_work_catalog_images,
    _captured_image_assets,
    _page_image_rows,
)
from ani_menu_agent.image_selection import image_belongs_to_package_sources
from ani_menu_agent.models import ContentPackage, Fact, ImageAsset, RightsBasis, SourceEvidence
from ani_menu_agent.sources import page_collector
from ani_menu_agent.sources.period_images import CC0, MAKER, compact_image_exclusions
from ani_menu_agent.sources.registry import load_source_registry

SOURCES = {s.id: s for s in load_source_registry().sources}
HACK = "https://anime.eiga.com/news/127755/"
ARTICLE = "https://animeanime.jp/article/2026/11/23/105037.html"
PRODUCT = MAKER + "/product/detail/p4934054079544/"
PHOTO = MAKER + "/sm_files/co/product/2026/figure/item_01.jpg"
OTHER = MAKER + "/sm_files/co/product/2026/figure/item_02.jpg"
NAME = "ロキシー・ミグルディア・グレイラット"
SEED = f"""<title>Product article</title><article class="arti-body">
<img src="/media/author.jpg"><a href="https://retailer.example/figure">Amazon</a>
<div class="enclosure"><p>「無職転生III {NAME} 1/7スケール 完成品フィギュア」製品情報<br>
参考価格：19,800円（税込）<br>発売日：2027年6月予定<br>ブランド：コトブキヤ</p></div>
</article><footer>無断転載を禁じます。</footer>"""
SEARCH = f"""<article class="contents_main"><div class="resultList">
<a class="resultList_itemInner" href="{PRODUCT}"><p class="resultList_title">{NAME}</p></a>
<a class="resultList_itemInner" href="/product/detail/p1111111111111/">
<p class="resultList_title">{NAME} 別売り</p></a></div></article>"""
DETAIL = f"""<h1 class="pageHeader02_title">{NAME}</h1><article class="contents_main">
<div class="detailHeader_price-taxIn">19,800円（税込）</div>
<table><tr><th>作品</th><td>無職転生Ⅲ</td></tr><tr><th>スケール</th><td>1/7</td></tr>
<tr><th>初回発売月</th><td>2027年6月</td></tr></table>
<div class="detailSlider"><div data-modal-slider-gallery data-src="{PHOTO}"></div>
<div data-modal-slider-gallery data-src="{OTHER}"></div>
<div data-modal-slider-gallery data-src="https://other.example/1.jpg"></div></div></article>"""


def capture_goods(monkeypatch, *, seed=SEED, search=SEARCH, detail=DETAIL):
    requested = []

    def get_html(url, source, _timeout):
        requested.append(url)
        if url == ARTICLE:
            return seed, url
        assert str(source.url).rstrip("/") == MAKER
        if url.startswith(MAKER + "/search/?fw="):
            return search, url
        assert url == PRODUCT
        return detail, url

    monkeypatch.setattr(page_collector, "_get_html", get_html)
    result = page_collector.collect_official_page(SOURCES["anime_anime_goods"], ARTICLE)
    return result, requested


def test_hack_photos_are_bound_to_each_article_and_keep_document_order(monkeypatch):
    prefix = "https://media.eiga.com/images/anime/news/127755/photo/"

    def img(url):
        return (
            f'<div class="newsDetailPhotoAreaC"><div class="newsDetailPhoto">'
            f'<img src="{url}" alt="Photo"></div></div>'
        )

    html = f"""<meta property="og:image" content="{prefix}unrelated.jpg">
    <div class="articleContainer"><div class="newsDetailBox">
    {img(prefix + "b.jpg")}{img(prefix + "a.jpg")}{img(prefix + "b.jpg")}
    {img(prefix.replace("127755", "127752") + "other.jpg")}
    {img(prefix.replace("media.", "ads.media.") + "wrong.jpg")}
    <aside>{img(prefix + "ad.jpg")}</aside></div></div>
    <a href="/news/123/">Related article</a>{img(prefix + "outside.jpg")}"""

    def get_html(url, *_args):
        assert url == HACK
        return html, url

    monkeypatch.setattr(page_collector, "_get_html", get_html)
    capture = page_collector.collect_official_page(SOURCES["anime_hack_events"], HACK)
    expected = [prefix + "b.jpg", prefix + "a.jpg"]
    assert [str(url) for url in capture.image_urls] == expected
    assert capture.image_source_pages == dict.fromkeys(expected, HACK)
    assert capture.image_alt_text == dict.fromkeys(expected, "Photo")


def test_manufacturer_prohibition_overrides_even_a_license_record(monkeypatch):
    grant = json.dumps({"@type": "ImageObject", "contentUrl": PHOTO, "license": CC0})
    capture, requested = capture_goods(
        monkeypatch,
        detail=DETAIL
        + f'<script type="application/ld+json">{grant}</script>'
        + "<footer>本サイトの画像の無断転用を禁じます。</footer>",
    )
    assert len(requested) == 3
    assert requested[-1] == PRODUCT
    assert not capture.image_urls and not capture.product_photo_permissions
    assert [(str(x.page_url), x.reason) for x in capture.image_exclusions] == [
        (ARTICLE, "explicit_no_redistribution_notice"),
        (PRODUCT, "manufacturer_no_redistribution"),
    ]
    assert [str(x) for x in capture.image_exclusions[-1].image_urls] == [PHOTO, OTHER]


def test_inside_goods_blocked_photos_and_unverified_poster_stay_excluded(
    monkeypatch,
):
    url = "https://www.inside-games.jp/article/2026/11/24/191443.html"
    campaign = "https://www.family.co.jp/campaign/spot/2611_dqm4_cp.html"
    source = SOURCES["inside_goods"]
    html = (
        f'<article class="arti-body"><h1>ドラゴンクエストモンスターズ4</h1>'
        f'<img src="/imgs/zoom/1809017.jpg"><a href="{campaign}">キャンペーン特設ページ</a>'
        "</article><footer>当サイトに掲載の写真・画像の無断転載を禁じます。</footer>"
    )

    def seed_only(target, *_):
        assert target == url
        return html, url

    monkeypatch.setattr(page_collector, "_get_html", seed_only)
    capture = page_collector.collect_official_page(source, url, content_type="goods")
    assert not capture.image_urls and len(capture.image_exclusions) == 1
    assert [str(link.url) for link in capture.outbound_links] == [campaign]
    evidence = SourceEvidence(
        id=source.id, title="商品情報", url=url, role="editorial", publisher=source.name
    )
    catalog = SourceEvidence(
        id="catalog",
        title="Dragon Quest",
        url="https://catalog.example/dragon-quest",
        role="reference",
        publisher="Catalog",
    )
    poster = ImageAsset(
        id="poster",
        url="https://catalog.example/poster.jpg",
        source_url=catalog.url,
        asset_kind="catalog_poster",
        provenance_source=catalog,
        menu_work_ids=[42],
    )
    package = ContentPackage(
        request_id="inside",
        topic="Dragon Quest Monsters 4",
        content_type="goods",
        content_kind="information",
        sources=[evidence],
        images=[poster],
        menu_work_ids=[42],
        facts=[
            Fact(
                id="raw",
                statement="商品情報",
                source_ids=[source.id],
                details=compact_image_exclusions(capture.model_dump(mode="json")),
            )
        ],
    )
    assert _add_same_work_catalog_images(package, [], 10) == 0
    assert not image_belongs_to_package_sources(poster, package.sources, package, card_role="cover")
    maker = SourceEvidence(
        id="campaign",
        title="Campaign press kit",
        url=campaign,
        publisher="FamilyMart",
        role="primary",
        is_official=True,
    )
    authorized = ImageAsset(
        id="press",
        url="https://www.family.co.jp/press/item.jpg",
        source_url=maker.url,
        asset_kind="goods_product",
        official=True,
        provenance_source=maker,
        menu_work_ids=[42],
        rights_status="allowed",
        rights_basis="rights_holder_permission",
        rights_evidence_url=maker.url,
        rights_scope=["instagram", "app", "commercial", "adapt"],
        rights_checked_at="2026-11-24",
        attribution_text="Publisher press kit",
        download_status="downloaded",
    )
    package.sources.append(maker)
    package.images.append(authorized)
    assert image_belongs_to_package_sources(authorized, package.sources, package)
    assert not image_belongs_to_package_sources(poster, package.sources, package, card_role="cover")


@pytest.mark.parametrize(
    ("seed", "search", "detail", "requests", "reason"),
    [
        (
            SEED.replace("ブランド：コトブキヤ", "ブランド：メーカーX"),
            SEARCH,
            DETAIL,
            1,
            "manufacturer_product_unverified",
        ),
        (SEED, SEARCH.replace(NAME, NAME + "2"), DETAIL, 2, "manufacturer_product_unverified"),
        (SEED, SEARCH, DETAIL.replace("1/7", "1/8"), 3, "manufacturer_product_unverified"),
        (
            SEED,
            SEARCH,
            DETAIL.replace("無職転生Ⅲ", "無職転生Ⅱ"),
            3,
            "manufacturer_product_unverified",
        ),
        (
            SEED,
            SEARCH,
            DETAIL.replace("2027年6月", "2027年7月"),
            3,
            "manufacturer_product_unverified",
        ),
        (SEED, SEARCH, DETAIL.replace("19,800円", "未定"), 3, "manufacturer_product_unverified"),
        (SEED, SEARCH, DETAIL, 3, "manufacturer_permission_unverified"),
    ],
)
def test_affiliate_links_partial_matches_and_unlicensed_images_stay_held(
    monkeypatch, seed, search, detail, requests, reason
):
    capture, visited = capture_goods(monkeypatch, seed=seed, search=search, detail=detail)
    assert len(visited) == requests
    assert capture.image_exclusions[-1].reason == reason
    assert not capture.image_urls


def test_only_granted_photo_reaches_package_with_separate_provenance(monkeypatch):
    grant = json.dumps(
        {
            "@graph": [
                {"@type": "ImageObject", "contentUrl": PHOTO, "license": CC0},
                {"@type": "WebPage", "contentUrl": OTHER, "license": CC0},
            ]
        }
    )
    capture, _ = capture_goods(
        monkeypatch, detail=DETAIL + f'<script type="application/ld+json">{grant}</script>'
    )
    assert [str(u) for u in capture.image_urls] == [PHOTO]
    assert capture.image_source_pages == {PHOTO: PRODUCT}
    assert [str(u) for u in capture.image_exclusions[-1].image_urls] == [OTHER]
    payload = capture.model_dump(mode="json")
    source = SOURCES["anime_anime_goods"]
    payload["images"] = _page_image_rows(capture, source, 10)
    images = _captured_image_assets(payload, source, [42])
    photo = images[0]
    assert photo.cleared_for_mennu and str(photo.rights_evidence_url) == PRODUCT
    assert (
        photo.provenance_source.is_official
        and str(photo.provenance_source.discovery_url) == ARTICLE
    )
    article = SourceEvidence(
        id=source.id, title="Article", url=ARTICLE, role="editorial", publisher=source.name
    )
    package = ContentPackage(
        request_id="photos",
        topic="Product",
        content_kind="information",
        content_type="goods",
        menu_work_ids=[42],
        sources=[article],
        images=images,
        facts=[
            Fact(
                id="raw",
                statement="Source page",
                source_ids=[source.id],
                details=compact_image_exclusions(payload),
            )
        ],
    )
    assert image_belongs_to_package_sources(photo, package.sources, package)
    reference = SourceEvidence(
        id="illustration",
        title="Work",
        publisher="Catalog",
        role="reference",
        url="https://catalog.example/works/42",
    )
    poster = ImageAsset(
        id="poster",
        url="https://catalog.example/poster.jpg",
        source_url=reference.url,
        asset_kind="catalog_poster",
        provenance_source=reference,
        menu_work_ids=[42],
    )
    assert not image_belongs_to_package_sources(poster, package.sources, package)
    package.images[0].download_status = "downloaded"
    assert not image_belongs_to_package_sources(poster, package.sources, package)
    # A separately licensed press-kit product photo can also support this item.
    primary = SourceEvidence(
        id="another_maker",
        title="Product",
        url="https://press.example/items/42",
        is_official=True,
        publisher="Manufacturer",
    )
    other_grant = photo.model_copy(
        update={
            "url": "https://press.example/photo.jpg",
            "source_url": primary.url,
            "provenance_source": primary,
            "rights_evidence_url": primary.url,
            "rights_basis": RightsBasis.OFFICIAL_MEDIA_KIT,
        }
    )
    package.sources.append(primary)
    assert image_belongs_to_package_sources(other_grant, package.sources, package)


def test_event_gallery_passes_copy_selection_and_render_resolution(monkeypatch):
    from ani_menu_agent.agents.content_generation import renderer
    from ani_menu_agent.image_selection import copy_image_pool
    from ani_menu_agent.layout_engine import build_cards
    from ani_menu_agent.models import CopyCard, RightsStatus
    from ani_menu_agent.quality import check_images, check_layout

    source = SOURCES["anime_hack_events"]
    evidence = SourceEvidence(
        id=source.id,
        url=HACK,
        title="Event",
        publisher=source.name,
        role=source.role,
        allowed_asset_hosts=source.allowed_asset_hosts,
    )
    photos = [
        ImageAsset(
            id=str(i),
            url=f"https://media.eiga.com/images/anime/news/127755/photo/{i}.jpg",
            source_url=HACK,
            width=640,
            height=360,
            download_status="downloaded",
            rights_status=RightsStatus.PUBLIC_EDITORIAL_RISK_ACCEPTED,
            rights_evidence_url=HACK,
            rights_checked_at="2026-11-24",
            attribution_text="Anime Hack",
            fingerprint=str(i),
        )
        for i in range(3)
    ]
    package = ContentPackage(
        request_id="render-gallery",
        topic="Event",
        content_type="event",
        content_kind="information",
        sources=[evidence],
        menu_work_ids=[42],
        images=photos,
    )
    selected = copy_image_pool(package, limit=3)
    assert len(selected) == 3
    package.cards = build_cards(
        [
            CopyCard(page=i + 1, role=role, image_id=photo.id, title_lines=["행사 안내"])
            for i, (photo, role) in enumerate(
                zip(selected, ("cover", "detail", "ending"), strict=True)
            )
        ],
        photos,
    )
    assert not check_images(package) and not check_layout(package)
    foreground_sizes = []
    original = renderer._contained

    def record_actual_image_size(image, width, height):
        contained = original(image, width, height)
        foreground_sizes.append(contained.size)
        return contained

    monkeypatch.setattr(renderer, "_contained", record_actual_image_size)
    raw = BytesIO()
    Image.new("RGB", (640, 360), "teal").save(raw, "JPEG")
    for card in package.cards:
        result = renderer.render_card_png(package, card, photos[int(card.image_id)], raw.getvalue())
        assert Image.open(BytesIO(result)).size == (1308, 1632)
    assert len(foreground_sizes) == 3
    assert all(790 <= w <= 800 and 445 <= h <= 450 for w, h in foreground_sizes)
    different_article = photos[0].model_copy(
        update={"source_url": "https://anime.eiga.com/news/127756/"}
    )
    same_copy = CopyCard(
        page=2, role="detail", image_id=different_article.id, title_lines=["다른 행사"]
    )
    assert build_cards([same_copy], [different_article])[0].layout.image_box.width == 0.95
