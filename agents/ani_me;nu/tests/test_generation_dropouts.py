"""Regressions for the ways candidates were lost before reaching a finished deck."""

from types import SimpleNamespace

import pytest

from ani_menu_agent import (
    cli,
    discovery,
    generation,
    nodes,
    work_aliases,
    work_catalog,
    work_catalog_registration,
    work_match_fallback,
)
from ani_menu_agent.generation import _link_card_works
from ani_menu_agent.image_selection import (
    _fits_card_resolution,
    allowed_image_upscale,
    assign_distinct_images,
    image_belongs_to_package_sources,
    placeable_image_count,
)
from ani_menu_agent.models import (
    ContentKind,
    ContentPackage,
    CopyCard,
    ImageAsset,
    RightsStatus,
    SourceEvidence,
)
from ani_menu_agent.policy import POLICY, min_cards_for
from ani_menu_agent.review_rules import get_rule


class _Item(SimpleNamespace):
    def model_dump(self, mode="json"):
        return {"title": self.title}


def _patch_selection(monkeypatch, work_ids_by_title):
    registry = SimpleNamespace(sources=[SimpleNamespace(id="feed")])
    monkeypatch.setattr(cli, "load_source_registry", lambda: registry)
    monkeypatch.setattr(
        cli,
        "_candidate_menu_work_ids",
        lambda candidate, _source: work_ids_by_title.get(candidate["title"], []),
    )


def _feed() -> list[_Item]:
    return [
        _Item(title="unmatched-a", source_id="feed"),
        _Item(title="unmatched-b", source_id="feed"),
        _Item(title="first-of-work-1", source_id="feed"),
        _Item(title="second-of-work-1", source_id="feed"),
        _Item(title="only-of-work-2", source_id="feed"),
    ]


WORK_IDS = {"first-of-work-1": [1], "second-of-work-1": [1], "only-of-work-2": [2]}


def test_matched_candidates_take_the_batch_before_unmatched_ones(monkeypatch) -> None:
    _patch_selection(monkeypatch, WORK_IDS)

    selected = cli._diversify_candidate_items(_feed(), 3)

    assert [item.title for item in selected] == [
        "first-of-work-1",
        "only-of-work-2",
        "second-of-work-1",
    ]


def test_unmatched_candidates_still_fill_leftover_slots(monkeypatch) -> None:
    _patch_selection(monkeypatch, WORK_IDS)

    selected = cli._diversify_candidate_items(_feed(), 4)

    assert selected[-1].title == "unmatched-a"


def test_trend_without_a_thread_cannot_take_a_generation_slot() -> None:
    assert cli._is_discussionless_trend(SimpleNamespace(source_role="trend", community_posts=[]))
    assert cli._is_discussionless_trend(SimpleNamespace(source_role="trend", community_posts=[{}]))
    assert not cli._is_discussionless_trend(
        SimpleNamespace(source_role="trend", community_posts=[{}, {}])
    )
    assert not cli._is_discussionless_trend(
        SimpleNamespace(source_role="primary", community_posts=[])
    )


def test_thread_lookup_needs_replies_a_known_work_and_a_human(monkeypatch) -> None:
    monkeypatch.setattr(
        discovery,
        "_catalog_work_titles_for_tags",
        lambda tags: ["Naruto"] if "Naruto" in tags else [],
    )
    monkeypatch.setattr(discovery, "_catalog_work_titles_in_text", lambda _body: [])
    post = {"content": "<p>hello</p>", "tags": [{"name": "Naruto"}], "replies_count": 2}

    assert discovery._thread_worth_fetching(post)
    assert not discovery._thread_worth_fetching({**post, "replies_count": 0})
    assert not discovery._thread_worth_fetching({**post, "tags": []})
    assert not discovery._thread_worth_fetching({**post, "in_reply_to_id": "9"})
    assert not discovery._thread_worth_fetching({**post, "account": {"bot": True}})


def test_one_word_latin_title_must_be_a_whole_word_in_a_post(monkeypatch) -> None:
    monkeypatch.setattr(
        discovery,
        "load_work_catalog",
        lambda: [
            {"menu_work_id": 1, "title_english": "DIVE!!", "title_ko": "다이브"},
            {"menu_work_id": 2, "title_english": "BLOOD+", "title_ko": "블러드 플러스"},
            {"menu_work_id": 3, "title_english": "Spy x Family", "title_ko": "스파이 패밀리"},
        ],
    )
    titles_in = discovery._catalog_work_titles_in_text

    assert titles_in("What's on the Crunchyroll & HIDIVE streaming calendar") == []
    assert titles_in("Bloodborne with the DLC tonight") == []
    assert titles_in("DIVE!! finally made me care about diving") == ["다이브"]
    assert titles_in("Spy x Family has the best family dinners") == ["스파이 패밀리"]


def test_two_syllable_korean_name_needs_a_particle_or_quotes(monkeypatch) -> None:
    monkeypatch.setattr(
        work_aliases,
        "_character_index",
        lambda: {
            "미정": (305, "미정"),
            "록리": (11, "록리"),
            "우즈마키나루토": (11, "우즈마키 나루토"),
        },
    )
    monkeypatch.setattr(work_aliases, "_song_indexes", lambda: ({}, {}))

    def found(text: str) -> list[int]:
        return [item.menu_work_id for item in work_aliases.find_named_works(text)]

    assert found("부천국제애니메이션페스티벌 일정 미정 안내") == []
    assert found("록리의 굿즈가 나왔다") == [11]
    assert found("‘록리’ 피규어 공개") == [11]
    assert found("우즈마키 나루토 피규어 공개") == [11]


def _image(width: int, height: int) -> ImageAsset:
    return ImageAsset(
        id="art",
        url="https://example.com/a.jpg",
        source_url="https://example.com/",
        width=width,
        height=height,
    )


def _card(page: int, role: str, **updates) -> CopyCard:
    return CopyCard(
        page=page,
        role=role,
        title_lines=["제목"],
        body_sentences=["본문입니다."],
        image_id="art",
        **updates,
    )


def test_detail_cards_accept_ordinary_web_images_but_not_tiny_ones() -> None:
    assert _fits_card_resolution(_image(640, 640), _card(2, "detail"))
    assert _fits_card_resolution(_image(400, 225), _card(2, "detail"))
    assert not _fits_card_resolution(_image(225, 350), _card(2, "detail"))


def test_placement_and_review_share_one_stretch_limit() -> None:
    assert allowed_image_upscale("cover_immersive") == POLICY.max_card_image_upscale
    assert allowed_image_upscale("low_res_spotlight") == POLICY.max_low_res_spotlight_upscale


def test_deck_size_counts_only_images_that_can_be_placed() -> None:
    assert placeable_image_count([_image(640, 640), _image(240, 240), _image(240, 240)]) == 1


def _source() -> SourceEvidence:
    return SourceEvidence(
        id="official",
        title="Official news",
        url="https://example.com/news/1",
        publisher="Official",
        is_official=True,
    )


def _curation_package(*images: ImageAsset) -> ContentPackage:
    return ContentPackage(
        request_id="curation-1",
        topic="Autumn picks",
        content_kind=ContentKind.INFORMATION,
        content_type="curation",
        menu_work_ids=[1, 2],
        sources=[_source()],
        images=list(images),
    )


def test_wrong_curation_links_are_rebuilt_instead_of_failing_the_deck(monkeypatch) -> None:
    names = {"Alpha": 1, "Beta": 2}
    monkeypatch.setattr(
        generation,
        "resolve_title_work_ids",
        lambda text: [work_id for name, work_id in names.items() if name in text],
    )
    # 2 is not backed by this card, 99 is outside the package, and Alpha (1) is named but unlinked.
    card = _card(2, "detail", menu_work_ids=[2, 99]).model_copy(update={"title_lines": ["Alpha"]})

    linked = _link_card_works([card], "curation", _curation_package())

    assert linked[0].menu_work_ids == [1]


def test_only_curation_cards_carry_work_links() -> None:
    card = _card(2, "detail", menu_work_ids=[1])

    assert _link_card_works([card], "news", _curation_package())[0].menu_work_ids == []


def _official_art(image_id: str, work_id: int, width: int) -> ImageAsset:
    source = _source()
    return ImageAsset(
        id=image_id,
        url=f"https://example.com/images/{image_id}.jpg",
        source_url=source.url,
        storage_key=image_id,
        official=True,
        rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
        width=width,
        height=width,
        rights_evidence_url=source.url,
        rights_checked_at="2026-10-21T00:00:00Z",
        attribution_text="Official source image",
        download_status="downloaded",
        menu_work_ids=[work_id],
    )


def test_curation_card_only_receives_art_from_its_own_work() -> None:
    package = _curation_package(
        _official_art("bigger-other-work", 2, 1600),
        _official_art("smaller-own-work", 1, 900),
    )
    card = _card(2, "detail", menu_work_ids=[1])

    assigned = assign_distinct_images([card], package)

    assert assigned[0].image_id == "smaller-own-work"


def _event_package(content_type: str, *images: ImageAsset) -> ContentPackage:
    return _curation_package(*images).model_copy(update={"content_type": content_type})


def test_cover_takes_the_sharpest_image_before_a_low_res_one() -> None:
    package = _event_package(
        "event",
        _official_art("low-res", 1, 500),
        _official_art("sharp", 1, 1400),
    )
    cards = [_card(1, "cover"), _card(2, "ending")]

    assigned = assign_distinct_images(cards, package)

    assert assigned[0].image_id == "sharp"
    assert assigned[1].image_id == "low-res"


def test_cover_and_ending_keep_images_when_details_run_out() -> None:
    package = _event_package("event", _official_art("one", 1, 900), _official_art("two", 1, 800))
    cards = [_card(1, "cover"), _card(2, "detail"), _card(3, "ending")]

    assigned = assign_distinct_images(cards, package)

    assert assigned[1].image_id == "missing-source-image-2"
    assert not assigned[0].image_id.startswith("missing-source-image-")
    assert not assigned[2].image_id.startswith("missing-source-image-")


def test_detail_cards_without_images_are_cut_instead_of_failing_the_deck() -> None:
    package = _event_package("goods")
    cards = [
        _card(1, "cover").model_copy(update={"image_id": "a"}),
        _card(2, "detail").model_copy(update={"image_id": "missing-source-image-2"}),
        _card(3, "ending").model_copy(update={"image_id": "b"}),
    ]

    kept = nodes._drop_detail_cards_without_images(cards, package)

    assert [(card.page, card.role, card.image_id) for card in kept] == [
        (1, "cover", "a"),
        (2, "ending", "b"),
    ]


def test_overlong_body_loses_its_last_sentence_not_the_deck() -> None:
    first, second = "가" * 40 + ".", "나" * 40 + "."
    card = _card(2, "detail").model_copy(update={"body_sentences": [first, second]})

    assert nodes._fit_body_length(card).body_sentences == [first]
    alone = "다" * 80
    single = _card(2, "detail").model_copy(update={"body_sentences": [alone]})
    assert nodes._fit_body_length(single).body_sentences == [alone]


def test_a_card_repeated_by_a_revision_appears_only_once() -> None:
    cards = [
        _card(1, "cover").model_copy(update={"title_lines": ["표지"]}),
        _card(2, "detail").model_copy(update={"title_lines": ["본문"]}),
        _card(3, "ending").model_copy(update={"title_lines": ["마지막"]}),
        _card(4, "ending").model_copy(update={"title_lines": ["마지막"]}),
    ]

    kept = nodes._drop_repeated_cards(cards)

    assert [(card.page, card.role, card.title_lines[0]) for card in kept] == [
        (1, "cover", "표지"),
        (2, "detail", "본문"),
        (3, "ending", "마지막"),
    ]


def test_three_card_minimum_still_applies_outside_timely_types() -> None:
    package = _event_package("guide")
    cards = [
        _card(1, "cover").model_copy(update={"image_id": "a"}),
        _card(2, "detail").model_copy(update={"image_id": "missing-source-image-2"}),
        _card(3, "ending").model_copy(update={"image_id": "b"}),
    ]

    assert nodes._drop_detail_cards_without_images(cards, package) == cards
    assert min_cards_for("guide") == 3
    assert min_cards_for("humor") == 3
    assert {min_cards_for(item) for item in ("news", "event", "goods", "review")} == {2}


def test_article_images_survive_an_event_goods_reroute() -> None:
    art = _official_art("collab-visual", 1, 1200).model_copy(update={"asset_kind": "event_visual"})

    assert image_belongs_to_package_sources(art, [_source()], _event_package("goods"))
    assert not image_belongs_to_package_sources(art, [_source()], _event_package("review"))


@pytest.fixture
def restore_catalog_caches():
    yield
    work_catalog_registration._clear_catalog_caches()


def _catalog_with(monkeypatch, tmp_path, *titles: tuple[str, str]) -> None:
    header = (
        "menu_work_id,title_ko,title_ko_source,title_ko_check,title_romaji,title_english,"
        "title_native,first_year,last_year,n_entries,popularity,anilist_ids\n"
    )
    rows = "".join(
        f"{index},{korean},,,,,{native},2020,2020,1,1,\n"
        for index, (korean, native) in enumerate(titles, start=1)
    )
    (tmp_path / "menu_works.csv").write_text(header + rows, encoding="utf-8")
    monkeypatch.setattr(work_catalog, "_CATALOG_DIRECTORY", tmp_path)
    work_catalog_registration._clear_catalog_caches()


@pytest.mark.usefixtures("restore_catalog_caches")
def test_headlines_find_works_despite_real_world_spelling(monkeypatch, tmp_path) -> None:
    _catalog_with(
        monkeypatch,
        tmp_path,
        ("체인소 맨", "チェンソーマン"),
        ("은혼", "銀魂"),
        ("치이카와", "ちいかわ"),
        ("", "魔法騎士（マジックナイト）レイアース"),
    )

    assert work_catalog.resolve_title_work_ids("국내 최초 체인소맨 단독 팝업스토어") == [1]
    assert work_catalog.resolve_title_work_ids("アニメ『銀魂』×「極楽湯」がコラボ") == [2]
    assert work_catalog.resolve_title_work_ids("「チョコサプ ちいかわ2」がキュート") == [3]
    assert work_catalog.resolve_title_work_ids("魔法騎士レイアース 光のフィギュア") == [4]


@pytest.mark.usefixtures("restore_catalog_caches")
def test_two_character_titles_need_quotes_and_one_character_titles_never_match(
    monkeypatch, tmp_path
) -> None:
    _catalog_with(monkeypatch, tmp_path, ("은혼", "銀魂"), ("괴", "怪"))

    assert work_catalog.resolve_title_work_ids("銀魂のグッズが登場") == []
    assert work_catalog.resolve_title_work_ids("『怪』のグッズが登場") == []


@pytest.mark.usefixtures("restore_catalog_caches")
def test_spelling_variant_cannot_take_over_a_real_title(monkeypatch, tmp_path) -> None:
    _catalog_with(monkeypatch, tmp_path, ("체인소맨", ""), ("체인소 맨", ""))

    assert work_catalog.resolve_title_work_ids("체인소맨 팝업스토어") == [1]


def test_shortened_or_untranslated_headline_names_can_link_a_known_work() -> None:
    long_title = {"ウマ娘 プリティーダービー"}

    assert work_match_fallback._is_shortened_or_untranslated_name("ウマ娘", long_title)
    assert work_match_fallback._is_shortened_or_untranslated_name("스쿨럼블", {"School Rumble"})
    assert not work_match_fallback._is_shortened_or_untranslated_name(
        "Blood on the Tracks", {"Flowers of Evil"}
    )
    assert not work_match_fallback._is_shortened_or_untranslated_name(
        "엉뚱한 작품", {"악의 꽃", "Flowers of Evil"}
    )


def test_copy_errors_get_a_rewrite_instead_of_ending_the_candidate() -> None:
    for code in ("UNSUPPORTED_DATE_CLAIM", "COPY_UNGROUNDED", "EVENT_PUBLICATION_DATE_SHOWN"):
        rule = get_rule(code)
        assert (rule.severity, rule.owner, rule.on_fail) == (
            "blocker",
            "copy_writer",
            "revise_owner",
        )
