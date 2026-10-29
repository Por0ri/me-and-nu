from ani_menu_agent.image_selection import (
    assign_distinct_images,
    cover_usable_editorial_images,
    distinct_editorial_images,
)
from ani_menu_agent.models import (
    ContentPackage,
    CopyCard,
    ImageAsset,
    RightsStatus,
    SourceEvidence,
    TermTranslation,
)


def test_sparse_copy_prompt_uses_images_that_fit_the_adaptive_cover_layout() -> None:
    source = SourceEvidence(
        id="official",
        title="Official event",
        url="https://example.com/news/1",
        publisher="Official",
        is_official=True,
    )

    def image(image_id: str, width: int, height: int) -> ImageAsset:
        return ImageAsset(
            id=image_id,
            url=f"https://example.com/images/{image_id}.jpg",
            storage_key=image_id,
            source_url=source.url,
            official=True,
            rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
            width=width,
            height=height,
            rights_evidence_url=source.url,
            rights_checked_at="2026-10-21T00:00:00Z",
            attribution_text="Official source image",
            download_status="downloaded",
        )

    images = [image("hero", 1200, 630), image("small", 786, 393)]

    usable = cover_usable_editorial_images(images, [source])

    assert [item.id for item in usable] == ["hero", "small"]


def test_small_official_art_can_serve_as_detail_visual_but_not_cover() -> None:
    from ani_menu_agent.layout_engine import build_cards

    source = SourceEvidence(
        id="official",
        title="Official news",
        url="https://example.com/news/1",
        publisher="Official",
        is_official=True,
    )
    image = ImageAsset(
        id="small-detail-art",
        url="https://example.com/images/detail.jpg",
        source_url=source.url,
        storage_key="small-detail-art",
        official=True,
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        width=220,
        height=280,
        rights_evidence_url=source.url,
        rights_checked_at="2026-10-21T00:00:00Z",
        attribution_text="Official source image",
        download_status="downloaded",
    )
    copy = CopyCard(
        page=2,
        role="detail",
        title_lines=["작은 원본 이미지"],
        body_sentences=["상세 페이지의 작은 배치로 선명도를 지킵니다."],
        image_id=image.id,
    )

    selected = distinct_editorial_images([image], [source])
    detail_layout = build_cards([copy, copy.model_copy(update={"page": 3})], [image])[0].layout

    assert [item.id for item in selected] == [image.id]
    assert cover_usable_editorial_images([image], [source]) == []
    assert detail_layout.template == "low_res_spotlight"


def test_small_portrait_official_image_uses_a_crisp_lower_third_safe_cover_tile() -> None:
    from ani_menu_agent.image_selection import _fits_card_resolution
    from ani_menu_agent.layout_engine import build_cards
    from ani_menu_agent.models import CopyCard

    source = SourceEvidence(
        id="official",
        title="Official news",
        url="https://example.com/news/1",
        publisher="Official",
        is_official=True,
    )
    image = ImageAsset(
        id="small-poster",
        url="https://example.com/images/poster.jpg",
        source_url=source.url,
        official=True,
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        width=296,
        height=390,
        rights_evidence_url=source.url,
        rights_checked_at="2026-10-21T00:00:00Z",
        attribution_text="Official source image",
        download_status="downloaded",
    )
    card = CopyCard(
        page=1,
        role="cover",
        eyebrow="Official news",
        title_lines=["New announcement"],
        body_sentences=["A relevant source image, shown without upscaling."],
        image_id=image.id,
    )
    layout = build_cards([card], [image])[0].layout

    assert layout.template == "low_res_spotlight"
    assert layout.copy_box.y == 0.68
    assert _fits_card_resolution(image, card)


def test_wide_low_resolution_art_is_enlarged_and_tucked_near_lower_copy() -> None:
    from ani_menu_agent.layout_engine import build_cards
    from ani_menu_agent.models import CopyCard
    from ani_menu_agent.policy import POLICY

    source = SourceEvidence(
        id="official",
        title="Official goods announcement",
        url="https://example.com/news/1",
        publisher="Official",
        is_official=True,
    )
    image = ImageAsset(
        id="wide-low-resolution",
        url="https://example.com/images/wide.jpg",
        source_url=source.url,
        official=True,
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        rights_evidence_url=source.url,
        rights_checked_at="2026-10-21T00:00:00Z",
        attribution_text="Official image",
        download_status="downloaded",
        width=786,
        height=393,
    )
    card = CopyCard(
        page=1,
        role="cover",
        eyebrow="굿즈 소식",
        title_lines=["공식 굿즈 공개"],
        body_sentences=["원본 비율을 유지한 공식 이미지."],
        image_id=image.id,
    )

    layout = build_cards([card], [image])[0].layout
    vertical_gap = layout.copy_box.y - (layout.image_box.y + layout.image_box.height)

    assert layout.template == "low_res_spotlight"
    assert layout.image_box.width >= 0.89
    assert layout.image_box.y <= 0.20
    assert vertical_gap <= POLICY.max_cover_vertical_gap + 0.001


def test_vision_rejected_art_is_not_reselected_for_repair() -> None:
    source = SourceEvidence(
        id="official",
        title="Official event",
        url="https://example.com/news/1",
        publisher="Official",
        is_official=True,
    )
    package = ContentPackage(
        request_id="vision-repair",
        topic="공식 행사",
        content_kind="information",
        sources=[source],
    )
    image = ImageAsset(
        id="bad-art",
        url="https://example.com/images/unrelated.jpg",
        source_url=str(source.url),
        official=True,
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        width=1200,
        height=1600,
        rights_evidence_url=source.url,
        rights_checked_at="2026-10-21T00:00:00Z",
        attribution_text="Official source image",
        download_status="downloaded",
        vision_review_rejected=True,
    )

    selected = distinct_editorial_images([image], [source], package)

    assert selected == []


def test_landscape_1000px_image_is_not_rejected_by_a_fractional_cover_upscale() -> None:
    source = SourceEvidence(
        id="official",
        title="Official event",
        url="https://example.com/news/1",
        publisher="Official",
        is_official=True,
    )
    image = ImageAsset(
        id="banner",
        url="https://example.com/images/banner.jpg",
        source_url=source.url,
        official=True,
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        width=1000,
        height=525,
        rights_evidence_url=source.url,
        rights_checked_at="2026-10-21T00:00:00Z",
        attribution_text="Official source image",
        download_status="downloaded",
    )

    usable = cover_usable_editorial_images([image], [source])

    assert [item.id for item in usable] == ["banner"]


def test_season_specific_package_excludes_other_season_and_movie_artwork() -> None:
    source = SourceEvidence(
        id="haikyu_official",
        title="Haikyu Official",
        url="https://haikyu.jp/season3/",
        publisher="Official",
        is_official=True,
    )
    package = ContentPackage(
        request_id="haikyu-season-3",
        topic="하이큐!! 시즌 3",
        content_kind="information",
        sources=[source],
        facts=[],
    )

    def image(image_id: str, filename: str) -> ImageAsset:
        return ImageAsset(
            id=image_id,
            url=f"https://haikyu.jp/assets/{filename}",
            source_url=str(source.url),
            official=True,
            rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
            width=1200,
            height=1600,
            rights_evidence_url=str(source.url),
            rights_checked_at="2026-10-21T00:00:00Z",
            attribution_text="Official source image",
            download_status="downloaded",
        )

    images = [
        image("s3", "visual_season3.jpg"),
        image("s3-large", "visual_season3-l.jpg"),
        image("s2", "visual_season2.jpg"),
        image("movie4", "visual_movie4.jpg"),
    ]

    selected = distinct_editorial_images(images, [source], package)

    assert [item.id for item in selected] == ["s3"]


def test_same_work_official_art_can_support_a_text_only_source() -> None:
    article_source = SourceEvidence(
        id="anime_corner",
        title="One Piece discussion roundup",
        url="https://animecorner.me/one-piece-discussion/",
        publisher="Anime Corner",
        role="editorial",
    )
    official_asset_source = SourceEvidence(
        id="one_piece_official",
        title="ONE PIECE.com official image gallery",
        url="https://one-piece.com/",
        publisher="ONE PIECE.com",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["one-piece.com"],
    )
    package = ContentPackage(
        request_id="one-piece-discussion",
        topic="원피스 팬 토론",
        content_kind="information",
        menu_work_ids=[7],
        sources=[article_source],
    )
    image = ImageAsset(
        id="official-luffy-art",
        url="https://one-piece.com/assets/luffy.jpg",
        source_url="https://one-piece.com/character/luffy",
        official=True,
        asset_kind="character_art",
        depicted_entities=["루피"],
        asset_metadata_confidence=0.9,
        provenance_source=official_asset_source,
        menu_work_ids=[7],
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        rights_evidence_url="https://one-piece.com/character/luffy",
        rights_checked_at="2026-10-21T00:00:00Z",
        attribution_text="ONE PIECE.com 공식 이미지",
        download_status="downloaded",
        width=1200,
        height=1200,
    )

    assert distinct_editorial_images([image], package.sources, package) == [image]


def test_official_art_for_another_work_is_not_borrowed() -> None:
    source = SourceEvidence(
        id="other-official",
        title="Other official page",
        url="https://other.example/",
        publisher="Other",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["other.example"],
    )
    package = ContentPackage(
        request_id="one-piece-discussion",
        topic="원피스 팬 토론",
        content_kind="information",
        menu_work_ids=[7],
        sources=[],
    )
    image = ImageAsset(
        id="wrong-work-image",
        url="https://other.example/assets/hero.jpg",
        source_url="https://other.example/news/1",
        official=True,
        provenance_source=source,
        menu_work_ids=[8],
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        rights_evidence_url="https://other.example/news/1",
        rights_checked_at="2026-10-21T00:00:00Z",
        attribution_text="Official source image",
        download_status="downloaded",
        width=1200,
        height=1200,
    )

    assert distinct_editorial_images([image], package.sources, package) == []


def test_public_same_work_editorial_image_is_usable_with_attribution() -> None:
    article = SourceEvidence(
        id="anime_corner",
        title="Cyberpunk: Edgerunners 2 episode review",
        url="https://animecorner.me/cyberpunk-edgerunners-2-review/",
        publisher="Anime Corner",
        role="editorial",
        allowed_asset_hosts=["static.animecorner.me"],
    )
    package = ContentPackage(
        request_id="edgerunners-review",
        topic="Cyberpunk: Edgerunners 2 review",
        content_kind="information",
        content_type="review",
        menu_work_ids=[70],
        sources=[article],
    )
    image = ImageAsset(
        id="edgerunners-article-visual",
        url="https://static.animecorner.me/2026/10/visual.png",
        source_url=str(article.url),
        official=False,
        menu_work_ids=[70],
        rights_status=RightsStatus.PUBLIC_EDITORIAL_RISK_ACCEPTED,
        rights_evidence_url=article.url,
        rights_checked_at="2026-10-22T00:00:00Z",
        attribution_text="Image: Anime Corner, Cyberpunk review",
        download_status="downloaded",
        width=1200,
        height=675,
    )

    assert image.public_editorial_source_matches([article])
    assert distinct_editorial_images([image], package.sources, package) == [image]


def test_event_or_goods_cannot_borrow_editorial_images_from_another_page() -> None:
    cited_page = SourceEvidence(
        id="anime_corner",
        title="Pop-up event notice",
        url="https://animecorner.me/work-pop-up-event/",
        publisher="Anime Corner",
        role="editorial",
        allowed_asset_hosts=["static.animecorner.me"],
    )
    image = ImageAsset(
        id="same-work-review-image",
        url="https://static.animecorner.me/2026/10/review.png",
        source_url="https://animecorner.me/work-episode-review/",
        official=False,
        menu_work_ids=[70],
        rights_status=RightsStatus.PUBLIC_EDITORIAL_RISK_ACCEPTED,
        rights_evidence_url="https://animecorner.me/work-episode-review/",
        rights_checked_at="2026-10-22T00:00:00Z",
        attribution_text="Image: Anime Corner review",
        download_status="downloaded",
        width=1200,
        height=675,
    )
    package = ContentPackage(
        request_id="work-event",
        topic="Work pop-up",
        content_kind="information",
        content_type="event",
        menu_work_ids=[70],
        sources=[cited_page],
    )

    assert distinct_editorial_images([image], package.sources, package) == []


def test_event_detail_cannot_use_generic_same_work_official_art() -> None:
    event_page = SourceEvidence(
        id="work_official",
        title="Pop-up event notice",
        url="https://work.example.com/news/popup-2026/",
        publisher="Work Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["work.example.com"],
    )
    artwork_page = SourceEvidence(
        id="work_official",
        title="Official character visual",
        url="https://work.example.com/character/hero/",
        publisher="Work Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["work.example.com"],
    )
    image = ImageAsset(
        id="work-key-visual",
        url="https://work.example.com/assets/key-visual.jpg",
        source_url=str(artwork_page.url),
        provenance_source=artwork_page,
        official=True,
        menu_work_ids=[70],
        asset_kind="official_key_visual",
        asset_metadata_confidence=0.9,
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        rights_evidence_url=artwork_page.url,
        rights_checked_at="2026-10-23T00:00:00Z",
        attribution_text="Work Official key visual",
        download_status="downloaded",
        width=1200,
        height=675,
    )
    package = ContentPackage(
        request_id="work-event",
        topic="Work pop-up",
        content_kind="information",
        content_type="event",
        menu_work_ids=[70],
        sources=[event_page],
    )

    assert distinct_editorial_images(
        [image], package.sources, package, card_role="detail"
    ) == []
    assert distinct_editorial_images(
        [image], package.sources, package, card_role="cover"
    ) == []
    legacy_image = image.model_copy(update={"asset_metadata_confidence": 0.4})
    assert distinct_editorial_images(
        [legacy_image], package.sources, package, card_role="detail"
    ) == []


def test_event_detail_explains_generic_official_art_is_not_event_imagery() -> None:
    event_page = SourceEvidence(
        id="work_official",
        title="Pop-up event notice",
        url="https://work.example.com/news/popup-2026/",
        publisher="Work Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["work.example.com"],
    )
    artwork_page = SourceEvidence(
        id="work_official",
        title="Official character visual",
        url="https://work.example.com/character/hero/",
        publisher="Work Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["work.example.com"],
    )
    image = ImageAsset(
        id="work-key-visual",
        url="https://work.example.com/assets/key-visual.jpg",
        source_url=str(artwork_page.url),
        provenance_source=artwork_page,
        official=True,
        menu_work_ids=[70],
        asset_kind="official_key_visual",
        asset_metadata_confidence=0.9,
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        rights_evidence_url=artwork_page.url,
        rights_checked_at="2026-10-23T00:00:00Z",
        attribution_text="Work Official key visual",
        download_status="downloaded",
        width=1200,
        height=675,
    )
    package = ContentPackage(
        request_id="work-event",
        topic="Work pop-up",
        content_kind="information",
        content_type="event",
        menu_work_ids=[70],
        sources=[event_page],
        images=[image],
    )
    card = CopyCard(
        page=2,
        role="detail",
        eyebrow="Work pop-up",
        title_lines=["Event details"],
        body_sentences=["Confirmed dates and venue details."],
        image_id="missing-source-image-2",
    )

    assigned = assign_distinct_images([card], package)

    assert assigned[0].image_id != image.id


def test_event_detail_cannot_borrow_another_event_visual_or_goods_photo() -> None:
    cited_page = SourceEvidence(
        id="work_official",
        title="Pop-up event notice",
        url="https://work.example.com/news/popup-2026/",
        publisher="Work Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["work.example.com"],
    )
    previous_event = SourceEvidence(
        id="work_official",
        title="Previous event visual",
        url="https://work.example.com/event/2025/",
        publisher="Work Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["work.example.com"],
    )
    goods_page = SourceEvidence(
        id="work_official",
        title="Goods page",
        url="https://work.example.com/goods/",
        publisher="Work Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["work.example.com"],
    )
    unrelated_news_page = SourceEvidence(
        id="work_official",
        title="Work Official news",
        url="https://work.example.com/news/123/",
        publisher="Work Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["work.example.com"],
    )
    package = ContentPackage(
        request_id="work-event",
        topic="Work pop-up",
        content_kind="information",
        content_type="event",
        menu_work_ids=[70],
        sources=[cited_page],
    )

    def official_image(image_id: str, source: SourceEvidence, asset_kind: str) -> ImageAsset:
        return ImageAsset(
            id=image_id,
            url=f"https://work.example.com/assets/{image_id}.jpg",
            source_url=str(source.url),
            provenance_source=source,
            official=True,
            menu_work_ids=[70],
            asset_kind=asset_kind,
            rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
            rights_evidence_url=source.url,
            rights_checked_at="2026-10-23T00:00:00Z",
            attribution_text="Work Official visual",
            download_status="downloaded",
            width=1200,
            height=675,
        )

    assert distinct_editorial_images(
        [official_image("old-event", previous_event, "event_visual")],
        package.sources,
        package,
        card_role="detail",
    ) == []
    assert distinct_editorial_images(
        [official_image("other-goods", goods_page, "official_key_visual")],
        package.sources,
        package,
        card_role="detail",
    ) == []
    unrelated_bonus_art = official_image(
        "unrelated-bonus", unrelated_news_page, "official_key_visual"
    ).model_copy(update={"source_description": "\u30df\u30cb\u30b2\u30fc\u30e0\u7279\u5178"})
    assert distinct_editorial_images(
        [unrelated_bonus_art], package.sources, package, card_role="detail"
    ) == []
    unrelated_menu_art = official_image(
        "unrelated-menu", unrelated_news_page, "official_key_visual"
    ).model_copy(
        update={"source_description": "\u30c1\u30fc\u30ba\u30dd\u30c6\u30c8"}
    )
    assert distinct_editorial_images(
        [unrelated_menu_art], package.sources, package, card_role="detail"
    ) == []
    unrelated_expo_art = official_image(
        "unrelated-expo", unrelated_news_page, "official_key_visual"
    ).model_copy(
        update={"source_description": "MAPPA EXPO 15th Anniversary official art"}
    )
    assert distinct_editorial_images(
        [unrelated_expo_art], package.sources, package, card_role="detail"
    ) == []
    unrelated_japanese_exhibition = official_image(
        "unrelated-japanese-exhibition", unrelated_news_page, "official_key_visual"
    ).model_copy(update={"source_description": "展覧会 描き下ろしキービジュアル"})
    assert distinct_editorial_images(
        [unrelated_japanese_exhibition], package.sources, package, card_role="detail"
    ) == []
    unrelated_screening_art = official_image(
        "unrelated-screening", unrelated_news_page, "official_key_visual"
    ).model_copy(update={"source_description": "劇場版 特別上映会"})
    assert distinct_editorial_images(
        [unrelated_screening_art], package.sources, package, card_role="detail"
    ) == []
    unrelated_event_title = official_image(
        "unrelated-event-title", unrelated_news_page, "official_key_visual"
    ).model_copy(
        update={
            "source_description": "official key visual",
            "provenance_source": unrelated_news_page.model_copy(
                update={"title": "MAPPA EXPO 15th Anniversary"}
            ),
        }
    )
    guide_package = package.model_copy(
        update={"topic": "Work season guide", "content_type": "guide"}
    )
    assert distinct_editorial_images(
        [unrelated_event_title], guide_package.sources, guide_package
    ) == []


def test_movie_key_visual_is_not_used_for_a_non_movie_announcement() -> None:
    article = SourceEvidence(
        id="haikyu_official",
        title="Haikyu game announcement",
        url="https://haikyu.jp/news/5939/",
        publisher="Haikyu Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["haikyu.jp"],
    )
    movie_page = SourceEvidence(
        id="haikyu_official",
        title="Haikyu movie page",
        url="https://haikyu.jp/movie/mv2024/",
        publisher="Haikyu Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["haikyu.jp"],
    )
    package = ContentPackage(
        request_id="haikyu-game-announcement",
        topic="Haikyu!! ALL Challengers game announcement",
        content_kind="information",
        content_type="news",
        menu_work_ids=[70],
        sources=[article, movie_page],
    )
    image = ImageAsset(
        id="haikyu-movie-key-visual",
        url="https://haikyu.jp/assets/visual_movie-l.jpg",
        source_url=str(movie_page.url),
        official=True,
        provenance_source=movie_page,
        menu_work_ids=[70],
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        rights_evidence_url=movie_page.url,
        rights_checked_at="2026-10-22T00:00:00Z",
        attribution_text="Haikyu Official",
        download_status="downloaded",
        width=1200,
        height=675,
    )

    assert distinct_editorial_images([image], package.sources, package) == []


def test_movie_key_visual_remains_usable_for_a_movie_topic() -> None:
    package = ContentPackage(
        request_id="haikyu-movie-news",
        topic="Haikyu!! movie film",
        content_kind="information",
        content_type="news",
        menu_work_ids=[70],
        sources=[],
    )
    source = SourceEvidence(
        id="haikyu_official",
        title="Haikyu movie page",
        url="https://haikyu.jp/movie/mv2024/",
        publisher="Haikyu Official",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["haikyu.jp"],
    )
    image = ImageAsset(
        id="haikyu-movie-key-visual",
        url="https://haikyu.jp/assets/visual_movie-l.jpg",
        source_url=str(source.url),
        official=True,
        provenance_source=source,
        menu_work_ids=[70],
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        rights_evidence_url=source.url,
        rights_checked_at="2026-10-22T00:00:00Z",
        attribution_text="Haikyu Official",
        download_status="downloaded",
        width=1200,
        height=675,
    )

    assert distinct_editorial_images([image], [source], package) == [image]


def test_goods_product_asset_cannot_be_reused_for_a_review_even_on_same_work() -> None:
    source = SourceEvidence(
        id="one_piece_official",
        title="One Piece goods notice",
        url="https://one-piece.com/goods/limited-shirt",
        publisher="ONE PIECE.com",
        is_official=True,
        role="primary",
    )
    package = ContentPackage(
        request_id="one-piece-discussion",
        topic="원피스 캐릭터 토론",
        content_kind="information",
        content_type="review",
        menu_work_ids=[8],
        sources=[source],
    )
    image = ImageAsset(
        id="limited-shirt-product-photo",
        url="https://one-piece.com/assets/limited-shirt.jpg",
        source_url=str(source.url),
        official=True,
        menu_work_ids=[8],
        asset_kind="goods_product",
        asset_metadata_confidence=0.95,
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        download_status="downloaded",
        width=1200,
        height=1200,
    )

    assert distinct_editorial_images([image], package.sources, package) == []


def test_character_match_overrides_an_unrelated_preferred_image_id() -> None:
    article = SourceEvidence(
        id="one_piece_official",
        title="ONE PIECE story update",
        url="https://one-piece.com/news/story-update",
        publisher="ONE PIECE.com",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["one-piece.com"],
    )
    character_page = article.model_copy(
        update={"title": "ONE PIECE Luffy character page", "url": "https://one-piece.com/character/luffy"}
    )
    generic = ImageAsset(
        id="generic-key-visual",
        url="https://one-piece.com/assets/key-visual.jpg",
        source_url=str(article.url),
        official=True,
        asset_kind="official_key_visual",
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        rights_evidence_url=article.url,
        rights_checked_at="2026-10-22T00:00:00Z",
        attribution_text="ONE PIECE.com official image",
        download_status="downloaded",
        width=1200,
        height=675,
    )
    luffy = ImageAsset(
        id="luffy-character-art",
        url="https://one-piece.com/assets/luffy-character.jpg",
        source_url=str(character_page.url),
        provenance_source=character_page,
        menu_work_ids=[8],
        official=True,
        asset_kind="character_art",
        depicted_entities=["루피"],
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        rights_evidence_url=character_page.url,
        rights_checked_at="2026-10-22T00:00:00Z",
        attribution_text="ONE PIECE.com official character image",
        download_status="downloaded",
        width=1200,
        height=1200,
    )
    package = ContentPackage(
        request_id="luffy-story-card",
        topic="원피스 루피의 선택",
        content_kind="information",
        content_type="news",
        menu_work_ids=[8],
        sources=[article],
        images=[generic, luffy],
        term_glossary=[TermTranslation(original="ルフィ", korean="루피", category="character")],
    )
    card = CopyCard(
        page=1,
        role="cover",
        eyebrow="원피스 이야기",
        title_lines=["루피의 선택"],
        body_sentences=["루피가 동료를 위해 내린 결정."],
        image_id=generic.id,
    )

    assigned = assign_distinct_images([card], package)

    assert assigned[0].image_id == luffy.id


def test_kitsu_catalog_art_is_same_work_visual_but_never_event_cover() -> None:
    article = SourceEvidence(
        id="editorial_review",
        title="Work review",
        url="https://review.example/work-review",
        publisher="Review Publisher",
        role="editorial",
    )
    kitsu = SourceEvidence(
        id="kitsu_anime_catalog",
        title="Test Anime — Kitsu catalog image reference",
        url="https://kitsu.io/api/edge/anime/17",
        publisher="Kitsu Anime Catalog API",
        role="reference",
        allowed_asset_hosts=["media.kitsu.app"],
    )
    poster = ImageAsset(
        id="kitsu-poster-17",
        url="https://media.kitsu.app/anime/17/poster.jpg",
        source_url=kitsu.url,
        provenance_source=kitsu,
        menu_work_ids=[17],
        asset_kind="catalog_poster",
        source_description="Kitsu catalog poster — Test Anime",
        rights_status=RightsStatus.PUBLIC_EDITORIAL_RISK_ACCEPTED,
        rights_evidence_url=kitsu.url,
        rights_checked_at="2026-10-24T00:00:00Z",
        attribution_text="Kitsu catalog image reference; rights unverified",
        download_status="downloaded",
        width=1200,
        height=1200,
    )
    review_package = ContentPackage(
        request_id="review-with-catalog-poster",
        topic="Test Anime review",
        content_kind="information",
        content_type="review",
        menu_work_ids=[17],
        sources=[article],
    )
    event_package = review_package.model_copy(update={"content_type": "event"})

    assert distinct_editorial_images([poster], review_package.sources, review_package) == [poster]
    assert distinct_editorial_images(
        [poster], event_package.sources, event_package, card_role="cover"
    ) == []
    assert distinct_editorial_images(
        [poster], event_package.sources, event_package, card_role="detail"
    ) == []
