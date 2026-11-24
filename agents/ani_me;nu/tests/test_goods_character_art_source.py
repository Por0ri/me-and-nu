from types import SimpleNamespace

import pytest

from ani_menu_agent import cli, work_aliases
from ani_menu_agent.discovery import supports_discovery_adapter
from ani_menu_agent.models import ContentPackage, Fact, ImageAsset, SourceEvidence
from ani_menu_agent.source_policy import validate_generation_sources
from ani_menu_agent.sources import goods_character_art as art, kitsu_images
from ani_menu_agent.sources.models import CharacterArtProfile, Source
from ani_menu_agent.sources.page_collector import _redistribution_notice_text
from ani_menu_agent.sources.period_images import verified_goods_product_url
from ani_menu_agent.sources.registry import load_source_registry

PROFILE = CharacterArtProfile(
    names=["ロキシー", "Roxy Migurdia"], nameplates=["/art/roxy-name.png"]
)
SOURCE = Source(
    id="official_characters",
    name="Official",
    url="https://work.example/character/",
    work_title_ko="무직전생",
    role="primary",
    status="active",
    collection="html",
    category="official_character_art",
    region="JP",
    character_art_profiles=[PROFILE],
)
ARTICLE = "https://animeanime.jp/article/2026/11/23/105037.html"
PRODUCT = "https://www.kotobukiya.co.jp/product/detail/p4934054079544/"


def package():
    publisher = SourceEvidence(
        id="anime_anime_goods",
        title="Roxy Migurdia figure",
        url=ARTICLE,
        role="editorial",
        publisher="AnimeAnime",
    )
    fact = Fact(
        id="sheet",
        statement="Figure",
        source_ids=[publisher.id],
        details={"goods_subject": "ロキシー"},
    )
    return ContentPackage(
        request_id="goods-art",
        topic="Figure",
        content_type="goods",
        content_kind="information",
        menu_work_ids=[50],
        facts=[fact],
        sources=[publisher],
    )


GALLERY = """
<main>
  <figure><img alt="Roxy Migurdia" src="/art/portrait.png"></figure>
  <aside><img alt="Roxy Migurdia" src="/promos/recommended.png"></aside>
  <img alt="Roxy Migurdia" src="https://ads.example/campaign.png">
  <ul id="js_character">
    <li class="charaslide"><div class="charaslide_img" data-imgload="/art/roxy.png"></div>
      <div class="charaslide_data">
      <div class="charaslide_data_ttl" data-imgload="/art/roxy-name.png"></div>
      <div class="sub" data-imgload="/art/side-character.png"></div></div></li>
    <li class="charaslide"><div class="charaslide_img" data-imgload="/art/other.png"></div>
      <div class="charaslide_data">
      <div class="charaslide_data_ttl" data-imgload="/art/other-name.png"></div>
      <p>Roxy Migurdia's friend</p></div></li>
  </ul>
</main>
"""


def read_gallery(html=GALLERY, page=str(SOURCE.url)):
    return art.collect_goods_character_art(
        package(),
        (SOURCE, PROFILE),
        3,
        fetch_html=lambda *_: (html, page),
        redistribution_notice=_redistribution_notice_text,
    )


def test_only_exact_alt_or_same_slide_primary_art_is_collected():
    images = read_gallery()
    assert {str(i.url) for i in images} == {
        "https://work.example/art/portrait.png",
        "https://work.example/art/roxy.png",
    }
    for image in images:
        assert image.depicted_entities == PROFILE.names and image.menu_work_ids == [50]
        assert image.provenance_source.link_relationship_type == "character"
        assert image.provenance_source.title == PROFILE.names[0]
        assert image.provenance_source.publisher == "Official"
        assert image.source_url == SOURCE.url
        assert image.rights_status.value == "needs_review"


def test_registered_character_gallery_only_supplies_assets():
    gallery = next(
        s for s in load_source_registry().sources if s.id == "mushoku_official_characters"
    )
    assert [t.value for t in gallery.content_types] == ["goods"]
    assert not supports_discovery_adapter(gallery)
    evidence = SourceEvidence(
        id=gallery.id,
        title="Gallery",
        url=gallery.url,
        publisher=gallery.name,
        role="primary",
        is_official=True,
    )
    with pytest.raises(ValueError):
        validate_generation_sources(
            [evidence], [{"id": "not-product", "source_ids": [gallery.id]}], "information"
        )


def test_explicit_prohibition_and_redirect_to_other_official_page_are_not_candidates():
    assert read_gallery(GALLERY + "<footer>掲載画像の無断転載を禁じます。</footer>") == []
    assert read_gallery(page="https://work.example/character/other") == []


def test_profile_is_selected_by_original_product_and_exact_work(monkeypatch):
    monkeypatch.setattr(art, "exact_catalog_work_id", lambda _: 50)
    p = package()
    assert art.matched_character_profile(p, [SOURCE]) == (SOURCE, PROFILE)
    p.menu_work_ids = [19]
    assert art.matched_character_profile(p, [SOURCE]) is None
    p.menu_work_ids = [50]
    p.facts[0].details["goods_subject"] = "Another character"
    source, other = art.matched_character_profile(p, [SOURCE])
    assert other.names == ["Another character"] and not other.nameplates
    assert not art.collect_goods_character_art(
        p,
        (source, other),
        3,
        fetch_html=lambda *_: (GALLERY, str(source.url)),
        redistribution_notice=_redistribution_notice_text,
    )
    p.facts[0].details = {}
    p.sources[0].title = "Goods roundup with commentary by Roxy Migurdia"
    assert art.matched_character_profile(p, [SOURCE]) is None


def test_verified_link_on_named_work_host_can_resolve_an_unconfigured_character(monkeypatch):
    monkeypatch.setattr(art, "exact_catalog_work_id", lambda _: 50)
    p = package()
    source = SOURCE.model_copy(update={"category": "anime", "character_art_profiles": []})
    linked = SourceEvidence(
        id="linked",
        title="Character",
        url="https://work.example/characters/roxy/",
        publisher="Official",
        linked_from_url=ARTICLE,
        link_classification="official_verified",
        target_work_match=True,
    )
    p.sources.append(linked)
    result = art.matched_character_profile(p, [source])
    assert result and result[0].url == linked.url and result[1].names == ["ロキシー"]
    linked.link_classification = "unverified"
    assert art.matched_character_profile(p, [source]) is None


@pytest.mark.parametrize("failed_image_count", [0, 2, 8])
def test_failed_images_do_not_fill_the_fallback_budget(monkeypatch, failed_image_count):
    p = package()
    p.images = [
        ImageAsset(
            id=f"failed-{i}",
            url=f"https://example.org/{i}.jpg",
            source_url=ARTICLE,
            download_status="failed",
        )
        for i in range(failed_image_count)
    ]
    monkeypatch.setattr(art, "exact_catalog_work_id", lambda _: 50)
    monkeypatch.setattr(cli.page_collector, "_get_html", lambda *_: (GALLERY, str(SOURCE.url)))

    def curate(extra, *_args, **_kwargs):
        for i in extra.images:
            i.download_status = "available"

    monkeypatch.setattr(cli, "curate_source_images", curate)
    monkeypatch.setattr(
        cli,
        "distinct_editorial_images",
        lambda images, *_args, **_kwargs: [i for i in images if i.download_status == "available"],
    )
    saved = []
    repo = SimpleNamespace(
        prior_cover_image_fingerprints=lambda **_: [], persist_images=saved.append
    )
    cli._supplement_goods_art_after_download(p, [SOURCE], repo, 2, persist=True)
    assert len(p.images) == 2 and saved[0].images == p.images
    assert all(image.asset_kind == "character_art" for image in p.images)


def test_verified_product_url_requires_single_matched_detail():
    p = package()
    p.facts[0].details.update(verified_product_url=PRODUCT)
    assert verified_goods_product_url(p) == PRODUCT
    p.facts[0].details["image_exclusions"] = [
        {
            "reason": "manufacturer_no_redistribution",
            "page_url": "https://www.kotobukiya.co.jp/product/detail/p9999999999999/",
        }
    ]
    assert verified_goods_product_url(p) is None
    del p.facts[0].details["verified_product_url"]
    p.facts[0].details["image_exclusions"][0] = {
        "reason": "manufacturer_product_unverified",
        "page_url": PRODUCT,
    }
    assert verified_goods_product_url(p) is None


def test_goods_character_dictionary_requires_one_entity_in_the_matched_work(tmp_path, monkeypatch):
    (tmp_path / "work_characters.csv").write_text(
        "menu_work_id,wikidata_id,name\n50,Q1,ロキシー\n50,Q1,Roxy Migurdia\n19,Q2,ロキシー\n19,Q3,ロキシー\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(work_aliases, "_REFERENCE", tmp_path)
    p = package()
    assert set(art.goods_character_names(p)) == {"ロキシー", "Roxy Migurdia"}
    p.menu_work_ids = [19]
    assert art.goods_character_names(p) == []
    p.menu_work_ids = [50, 19]
    assert art.goods_character_names(p) == []


def test_restricted_goods_without_gallery_can_use_two_independent_catalog_images(monkeypatch):
    p = package()
    p.facts[0].details["image_exclusions"] = [
        {"reason": "explicit_no_redistribution_notice", "page_url": ARTICLE}
    ]
    p.images = [
        ImageAsset(id=f"failed-{kind}", url=f"https://media.kitsu.app/anime/17/{kind}.jpg",
                   source_url="https://kitsu.io/api/edge/anime/17", download_status="failed")
        for kind in ("catalog_poster", "catalog_cover")
    ]
    catalog = next(s for s in load_source_registry().sources if s.id == "kitsu_anime_catalog")
    evidence = SourceEvidence(
        id=catalog.id, title="Anime", url="https://kitsu.io/api/edge/anime/17",
        role="reference", publisher=catalog.name, allowed_asset_hosts=catalog.allowed_asset_hosts,
    )
    calls = []

    def lookup(work_ids, source, **options):
        calls.append((work_ids, source.id, options))
        return [
            kitsu_images._asset(f"https://media.kitsu.app/anime/17/{kind}.jpg", evidence,
                                50, "Anime", kind, [], 0.98)
            for kind in ("catalog_poster", "catalog_cover")
        ]

    def curate(images, *_args, **_kwargs):
        for image in images.images:
            image.width, image.height = 800, 1200
            image.download_status = "downloaded"
            image.rights_status = "public_editorial_risk_accepted"
            image.rights_evidence_url = image.source_url
            image.rights_checked_at = "2026-11-24"

    monkeypatch.setattr(cli, "resolve_menu_work_ids", lambda p: p.menu_work_ids)
    monkeypatch.setattr(cli, "collect_kitsu_work_images", lookup)
    monkeypatch.setattr(cli, "curate_source_images", curate)
    saved = []
    repo = SimpleNamespace(prior_cover_image_fingerprints=lambda **_: [], persist_images=saved.append)
    for _ in range(2):
        cli._supplement_goods_art_after_download(p, [catalog], repo, 2, persist=True)
    assert len(calls) == 1 and calls[0][0] == [50] and calls[0][2]["goods_only"]
    assert len(saved) == 1 and {i.asset_kind for i in saved[0].images} == {"catalog_poster", "catalog_cover"}
    assert len(cli.copy_image_pool(p, limit=2)) == 2
