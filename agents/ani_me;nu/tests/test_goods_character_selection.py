import pytest

from ani_menu_agent.image_selection import (
    InsufficientCardImages,
    assign_distinct_images,
    copy_image_pool,
    goods_reference_image_label,
    goods_subject_names_match,
    image_visual_keys,
    is_goods_character_illustration,
)
from ani_menu_agent.models import ContentPackage, CopyCard, Fact, ImageAsset, SourceEvidence
from ani_menu_agent.period_repair import evidence_snapshot, has_repaired_evidence
from ani_menu_agent.layout_engine import build_cards
from ani_menu_agent.quality import check_images, check_layout

ARTICLE = "https://animeanime.jp/article/2026/11/23/105037.html"
CHARACTER = "ロキシー・ミグルディア"


def goods_package(*, restricted=True):
    source = SourceEvidence(
        id="anime_anime_goods",
        title=f"「{CHARACTER}」の新作フィギュアが登場",
        url=ARTICLE,
        publisher="アニメ！アニメ！",
        role="editorial",
    )
    details = {"goods_subject": f"{CHARACTER} 1/7スケール"}
    if restricted:
        details["image_exclusions"] = [
            {"page_url": ARTICLE, "reason": "explicit_no_redistribution_notice"}
        ]
    return ContentPackage(
        request_id="roxy-goods",
        topic=source.title,
        content_type="goods",
        content_kind="information",
        menu_work_ids=[50],
        sources=[source],
        facts=[
            Fact(id="capture", statement=source.title, source_ids=[source.id], details=details),
        ],
    )


def character_art(image_id, *, name=CHARACTER, work_id=50):
    source = SourceEvidence(
        id="official-characters",
        title=name,
        url="https://anime.example/character/roxy",
        publisher="アニメ公式",
        is_official=True,
        role="primary",
        link_relationship_type="character",
        target_work_match=True,
    )
    return ImageAsset(
        id=image_id,
        url=f"https://anime.example/assets/{image_id}.png",
        source_url=source.url,
        official=True,
        provenance_source=source,
        menu_work_ids=[work_id],
        asset_kind="character_art",
        depicted_entities=[name],
        download_status="downloaded",
        width=1440,
        height=1800,
        rights_status="official_public_risk_accepted",
        rights_evidence_url=source.url,
        attribution_text="アニメ公式",
        rights_checked_at="2026-11-24",
        fingerprint=image_id,
    )


@pytest.mark.parametrize("restricted", [False, True])
def test_two_confirmed_character_images_can_publish_without_product_photos(restricted):
    package = goods_package(restricted=restricted)
    original, second = (character_art(key) for key in ("roxy-a", "roxy-b"))
    package.images = [original, second]
    package.prior_image_fingerprints = list(image_visual_keys(original))
    assert is_goods_character_illustration(original, package)
    assert [img.id for img in copy_image_pool(package, limit=2)] == ["roxy-b", "roxy-a"]
    cards = assign_distinct_images(
        [
            CopyCard(page=1, role="cover", title_lines=["예약 소식"], image_id=""),
            CopyCard(page=2, role="ending", title_lines=["발매 일정"], image_id=""),
        ],
        package,
    )
    assert [c.image_explanation for c in cards] == ["작품 일러스트", "작품 일러스트"]


@pytest.mark.parametrize(
    "variation",
    [
        "different_character",
        "different_work",
        "unrelated_product",
        "poster",
        "missing_provenance",
        "unverified_character",
        "blocked_source",
        "unapproved_host",
        "identical_file",
        "unusable_rights",
        "campaign_from_another_article",
    ],
)
def test_random_or_unusable_second_asset_cannot_complete_the_deck(variation):
    package = goods_package()
    first = character_art("roxy-a")
    second = character_art("roxy-b")
    if variation == "different_character":
        second = character_art("eris", name="エリス")
        package.sources[0].title += "　エリスも紹介"
    elif variation == "different_work":
        second.menu_work_ids = [51]
    elif variation == "unrelated_product":
        package.facts[0].details["goods_subject"] = "ロキシー・ミグルディアーナ"
    elif variation == "poster":
        second.asset_kind = "catalog_poster"
    elif variation == "missing_provenance":
        second.provenance_source = None
    elif variation == "unverified_character":
        second.provenance_source.target_work_match = False
    elif variation == "blocked_source":
        second.source_url = second.provenance_source.url = package.sources[0].url
    elif variation == "unapproved_host":
        second = second.model_copy(update={"url": "https://thirdparty.example/unverified.png"})
    elif variation == "identical_file":
        second.fingerprint = first.fingerprint
    elif variation == "unusable_rights":
        second.rights_status = "needs_review"
    elif variation == "campaign_from_another_article":
        second.asset_kind = "event_visual"
        second.provenance_source.link_relationship_type = "product"
        second.provenance_source.linked_from_url = "https://animeanime.jp/article/another-figure"
    package.images = [first, second]
    with pytest.raises(InsufficientCardImages):
        copy_image_pool(package, limit=2)


def test_goods_cover_prefers_product_then_its_campaign_then_target_character():
    package = goods_package()
    article = package.sources[0]
    maker = SourceEvidence(
        id="maker",
        title="コラボ公式",
        url="https://maker.example/items/1",
        publisher="Maker",
        is_official=True,
        linked_from_url=article.url,
        link_relationship_type="product",
        target_work_match=True,
    )
    photos = []
    for key, kind in [("actual", "goods_product"), ("campaign", "official_key_visual")]:
        photos.append(
            ImageAsset(
                id=key,
                url=f"https://maker.example/{key}.png",
                source_url=maker.url,
                provenance_source=maker,
                official=True,
                menu_work_ids=[50],
                asset_kind=kind,
                width=1200,
                height=1500,
                download_status="downloaded",
                rights_status="allowed",
                rights_basis="rights_holder_permission",
                rights_scope=["instagram", "app", "commercial", "adapt"],
                rights_evidence_url=maker.url,
                rights_checked_at="2026-11-24",
                attribution_text="Maker press",
            )
        )
    package.images = [character_art("roxy-a"), character_art("roxy-b"), *photos]
    assert copy_image_pool(package, limit=2)[0].id == "actual"
    photos[0].download_status = "failed"
    assert copy_image_pool(package, limit=2)[0].id == "campaign"
    photos[1].download_status = "failed"
    assert {i.id for i in copy_image_pool(package, limit=2)} == {"roxy-a", "roxy-b"}


def test_repair_can_reopen_only_with_new_eligible_distinct_art(monkeypatch):
    package = goods_package()
    monkeypatch.setattr(
        "ani_menu_agent.period_repair.expiry_evidence", lambda *_: {"status": "active"}
    )
    issue = [{"code": "SOURCE_IMAGE_POOL_INSUFFICIENT"}]
    package.images = [character_art("roxy-a")]
    old = evidence_snapshot(package)
    package.images.append(character_art("eris", name="エリス"))
    assert not has_repaired_evidence(package, old, issue)
    package.images.append(character_art("roxy-b"))
    assert has_repaired_evidence(package, old, issue)


@pytest.mark.parametrize(
    "title,target,other",
    [
        ("록시 피규어 예약 시작, 실피랑 함께 장식해 봐", "록시", "실피"),
        ("「ロキシー」の新作フィギュアが登場！ シルフィと一緒に飾ろう", "ロキシー", "シルフィ"),
        ("Roxy figure unveiled, pictured here with Sylphy", "Roxy", "Sylphy"),
        ("예약 시작! 피규어 '록시', 실피와 나란히", "록시", "실피"),
    ],
)
def test_legacy_images_match_the_named_product_only_with_no_product_sheet(title, target, other):
    package = goods_package()
    del package.facts[0].details["goods_subject"]
    package.sources[0].title = title
    assert is_goods_character_illustration(character_art("target", name=target), package)
    assert not is_goods_character_illustration(character_art("other", name=other), package)


def test_generic_headline_without_a_named_product_cannot_authorize_stored_character_art():
    package = goods_package()
    package.facts[0].details.pop("goods_subject")
    package.sources[0].title = "무직전생 굿즈 공개! 록시랑 실피 누가 떠올라?"
    # The editorial topic is not original product-name evidence.
    package.topic = "록시 피규어"
    assert not is_goods_character_illustration(character_art("roxy", name="록시"), package)


def test_generated_translation_cannot_turn_an_eris_image_into_roxy():
    from ani_menu_agent.models import TermTranslation

    package = goods_package()
    package.term_glossary = [
        TermTranslation(original=CHARACTER, korean="에리스", category="character")
    ]
    assert not is_goods_character_illustration(character_art("eris", name="에리스"), package)


def test_unusable_product_thumbnail_does_not_unlock_two_generic_posters():
    package = goods_package(restricted=False)
    source = package.sources[0]
    thumbnail = character_art("thumb")
    thumbnail.asset_kind = "goods_product"
    thumbnail.source_url = source.url
    thumbnail.width = thumbnail.height = 48
    package.images = [thumbnail]
    for key in ["series-poster", "another-series-poster"]:
        poster = character_art(key)
        poster.asset_kind = "official_key_visual"
        poster.depicted_entities = []
        poster.provenance_source.link_relationship_type = "uncertain"
        package.images.append(poster)
    with pytest.raises(InsufficientCardImages):
        copy_image_pool(package, limit=2)


def kitsu_art(image_id, kind, names=()):
    asset = character_art(image_id)
    path = "/characters?include=character" if kind == "character_art" else ""
    source = SourceEvidence(
        id="kitsu_anime_catalog", title="Kitsu catalog", publisher="Kitsu", role="reference",
        url="https://kitsu.io/api/edge/anime/999" + path, allowed_asset_hosts=["media.kitsu.app"],
    )
    return ImageAsset.model_validate({
        **asset.model_dump(), "asset_kind": kind, "depicted_entities": list(names),
        "source_url": source.url, "provenance_source": source, "official": False,
        "rights_status": "public_editorial_risk_accepted", "rights_evidence_url": source.url,
        "url": f"https://media.kitsu.app/files/{image_id}.png",
    })


def test_kitsu_posters_form_a_goods_deck_when_original_images_are_excluded():
    package = goods_package()
    poster = kitsu_art("poster", "catalog_poster")
    cover = kitsu_art("cover", "catalog_cover")
    package.images = [poster, cover]
    package.prior_image_fingerprints = list(image_visual_keys(poster))
    assert [i.id for i in copy_image_pool(package, limit=2)] == ["cover", "poster"]
    assert [goods_reference_image_label(i, package) for i in package.images] == ["작품 포스터"] * 2


def test_medium_catalog_originals_render_at_the_detail_resolution_limit():
    package = goods_package()
    poster = kitsu_art("poster", "catalog_poster")
    poster.width, poster.height = 594, 888
    cover = kitsu_art("cover", "catalog_cover")
    cover.width, cover.height = 848, 562
    package.images = [poster, cover]
    slots = [
        CopyCard(page=i, role=role, title_lines=["상품 소식"], image_id="")
        for i, role in enumerate(("cover", "ending"), start=1)
    ]
    assigned = assign_distinct_images(slots, package)
    package.cards = build_cards(assigned, package.images)
    assert [card.image_id for card in package.cards] == ["poster", "cover"]
    detail = package.cards[-1].layout
    assert detail.background_style == "blur"
    assert detail.image_box.width < 0.95
    assert detail.image_box.bottom < detail.copy_box.y
    assert "IMAGE_LOW_RESOLUTION" not in {issue.code for issue in check_images(package)}
    assert not check_layout(package)


def test_two_tiny_reference_thumbnails_are_not_enlarged_to_fill_a_deck():
    package = goods_package()
    package.images = [kitsu_art("poster", "catalog_poster"), kitsu_art("cover", "catalog_cover")]
    for image in package.images:
        image.width, image.height = 240, 320
    with pytest.raises(InsufficientCardImages):
        copy_image_pool(package, limit=2)


def test_exact_named_kitsu_subject_has_cover_priority_over_catalog_poster():
    package = goods_package()
    poster = kitsu_art("poster", "catalog_poster")
    target = kitsu_art("roxy", "character_art", [CHARACTER])
    other = kitsu_art("eris", "character_art", ["エリス"])
    package.images = [poster, other, target]
    package.sources[0].title += "！ エリスも紹介"
    assert [i.id for i in copy_image_pool(package, limit=2)] == ["roxy", "poster"]
    assert goods_reference_image_label(target, package) == "작품 일러스트"
    assert goods_reference_image_label(other, package) == ""


@pytest.mark.parametrize("problem", [
    "work", "multiple_work_ids", "provenance_page", "wrong_kitsu_endpoint", "fake_reference",
    "episode_still", "excluded_page", "asset_host", "wrong_cdnanime",
])
def test_catalog_context_alone_cannot_make_an_arbitrary_goods_reference(problem):
    package = goods_package()
    poster = kitsu_art("poster", "catalog_poster")
    other = kitsu_art("cover", "catalog_cover")
    if problem == "work":
        other.menu_work_ids = [51]
    elif problem == "multiple_work_ids":
        other.menu_work_ids = [50, 51]
    elif problem == "provenance_page":
        other.provenance_source.url = "https://kitsu.io/api/edge/anime/1234"
    elif problem == "wrong_kitsu_endpoint":
        other.source_url = other.provenance_source.url = "https://kitsu.io/api/edge/anime/999/episodes"
    elif problem == "fake_reference":
        other.source_url = other.provenance_source.url = "https://unknown.example/api/edge/anime/999"
    elif problem == "episode_still":
        other.asset_kind = "scene_still"
    elif problem == "excluded_page":
        package.facts[0].details["image_exclusions"].append({"page_url": str(other.source_url)})
    elif problem == "asset_host":
        other.url = "https://unregistered.example/art.jpg"
    elif problem == "wrong_cdnanime":
        other.url = "https://media.kitsu.app/anime/cover_images/1234/original.png"
    assert not goods_reference_image_label(other, package)
    package.images = [poster, other]
    with pytest.raises(InsufficientCardImages):
        copy_image_pool(package, limit=2)


def test_work_key_visual_requires_official_work_page_and_not_another_campaign():
    package = goods_package()
    work = character_art("visual")
    work.asset_kind = "official_key_visual"
    work.provenance_source.link_relationship_type = "official_site"
    work.depicted_entities = []
    assert goods_reference_image_label(work, package) == "작품 포스터"
    work.source_description = "Official collaboration campaign visual for a previous product"
    assert not goods_reference_image_label(work, package)


@pytest.mark.parametrize(("title", "target", "incidental"), [
    ("「ロシデレ」アーリャの誕生日をお祝い♪ サンタコスやケモ耳姿のグッズも！オンラインくじ登場", "アーリャ", "有希"),
    ("機動武闘伝Gガンダム：ボルトガンダムがガイアクラッシャー放つ！ 格闘ポージング特化のROBOT魂", "ボルトガンダム", "ガイアクラッシャー"),
])
def test_character_reference_tied_to_the_birthday_goods_or_named_mecha(title, target, incidental):
    package = goods_package()
    package.facts[0].details.pop("goods_subject")
    package.sources[0].title = title
    assert goods_subject_names_match(package, {target})
    assert not goods_subject_names_match(package, {incidental})
