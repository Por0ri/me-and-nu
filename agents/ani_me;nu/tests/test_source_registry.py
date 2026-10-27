from ani_menu_agent.discovery import supports_discovery_adapter
from ani_menu_agent.models import ContentType
from ani_menu_agent.sources import load_source_registry
from ani_menu_agent.sources.models import Source


def test_source_registry_is_valid() -> None:
    registry = load_source_registry()

    assert len(registry.sources) >= 20
    automatic_ids = {source.id for source in registry.automatic_sources}
    assert {
        "tv_tokyo_anime_official",
        "anime_news_network",
        "anime_corner",
        "anime_feminist_reviews",
        "anime_trending_polls",
        "filmarks_anime",
        "ann_encyclopedia",
        "flyingdog_music",
        "pony_canyon_music",
        "kitsu_anime_catalog",
        "wikipedia_en_anime",
        "wikipedia_ja_anime",
    } <= automatic_ids
    collectable_ids = {source.id for source in registry.collectable_sources}
    assert {"anime_schedule_rss", "prtimes_rss"} <= collectable_ids
    aniplus = next(source for source in registry.sources if source.id == "aniplus")
    assert aniplus.status == "terms_review"
    naruto = next(source for source in registry.sources if source.id == "naruto_official")
    assert "cmsapi-frontend.naruto-official.com" in naruto.allowed_asset_hosts
    natsume = next(source for source in registry.sources if source.id == "natsume_official")
    assert natsume.work_title_ko == "나츠메 우인장"
    assert natsume.work_title_original == "夏目友人帳"
    work_scoped_official_ids = {
        "jujutsu_kaisen_official", "kimetsu_official", "frieren_official",
        "natsume_official", "bn_pictures_keroro_official", "haikyu_official",
        "spy_family_official", "naruto_official", "one_piece_official",
        "bleach_anime_official", "the_fable_anime_official",
        "my_hero_academia_official", "attack_on_titan_official",
    }
    by_id = {source.id: source for source in registry.sources}
    assert work_scoped_official_ids <= set(by_id)
    assert all(not by_id[source_id].discovery_enabled for source_id in work_scoped_official_ids)
    assert all(
        not supports_discovery_adapter(by_id[source_id]) for source_id in work_scoped_official_ids
    )
    # Keep these records for attribution/reference; do not poll each franchise site
    # as a recurring source of new candidates.
    assert all(by_id[source_id].content_types for source_id in work_scoped_official_ids)
    assert any(source.role == "primary" for source in registry.sources)
    assert any(source.role == "trend" for source in registry.sources)
    assert len({source.id for source in registry.sources}) == len(registry.sources)


def test_new_community_sources_are_registered_but_not_yet_auto_collected() -> None:
    registry = load_source_registry()
    expected = {
        "theqoo_anime_trend",
        "dcinside_anime_trend",
        "arca_live_anime_trend",
        "fmkorea_anime_trend",
    }
    by_id = {source.id: source for source in registry.community_sources}

    assert expected <= set(by_id)
    assert all(by_id[source_id].role == "trend" for source_id in expected)
    assert all(by_id[source_id].collection == "manual" for source_id in expected)
    assert all(by_id[source_id].status != "trend_only" for source_id in expected)
    assert not (expected & {source.id for source in registry.collectable_sources})
    assert by_id["dcinside_anime_trend"].status == "permission_required"


def test_three_mastodon_apis_are_approved_trend_only_sources() -> None:
    registry = load_source_registry()
    expected = {
        "mastodon_social_anime",
        "mastodon_online_anime",
        "mstdn_jp_anime",
    }
    by_id = {source.id: source for source in registry.sources}

    assert expected <= set(by_id)
    assert all(by_id[source_id].collection == "api" for source_id in expected)
    assert all(by_id[source_id].status == "trend_only" for source_id in expected)
    assert expected <= {source.id for source in registry.collectable_sources}


def test_all_eleven_types_have_at_least_three_distinct_mapped_sources() -> None:
    registry = load_source_registry()
    assert all(
        sum(content_type in source.content_types for source in registry.sources) >= 3
        for content_type in ContentType
    )


def test_all_eleven_types_have_at_least_three_approved_automatic_generation_sources() -> None:
    registry = load_source_registry()
    for content_type in ContentType:
        eligible = []
        for source in registry.sources:
            if content_type not in source.content_types:
                continue
            if source.status == "active" and source.collection != "manual":
                if source.role == "primary":
                    eligible.append(source)
                elif (
                    source.role == "editorial"
                    and {item.value for item in source.content_types}
                    <= {"review", "curation"}
                    and content_type.value in {"review", "curation"}
                ):
                    eligible.append(source)
                elif (
                    source.role == "reference"
                    and {item.value for item in source.content_types}
                    <= {"guide", "people", "character", "curation"}
                    and content_type.value in {"guide", "people", "character", "curation"}
                ):
                    eligible.append(source)
            elif (
                content_type.value == "humor"
                and source.role == "trend"
                and source.status == "trend_only"
                and source.collection != "manual"
            ):
                eligible.append(source)
        assert len({source.id for source in eligible}) >= 3, (
            content_type.value,
            [source.id for source in eligible],
        )


def test_verified_community_source_can_use_an_automatic_adapter_as_trend_only() -> None:
    source = Source(
        id="community_test",
        name="Community test",
        url="https://community.example/anime",
        category="community_trend",
        role="trend",
        collection="html",
        status="trend_only",
        region="KR",
    )

    assert source.collection == "html"
    assert source.status == "trend_only"


def test_sources_have_typed_capabilities_without_changing_collection_gate() -> None:
    registry = load_source_registry()
    assert all(source.content_types for source in registry.sources)
    assert {item.value for source in registry.sources for item in source.content_types} <= {
        "news", "event", "guide", "people", "goods", "behind", "review",
        "humor", "character", "curation", "music",
    }
    assert len({item.value for source in registry.sources for item in source.content_types}) == 11
    community = {source.id: source for source in registry.community_sources}
    assert "humor" in {item.value for item in community["theqoo_anime_trend"].content_types}
    assert all(source.status != "active" for source in community.values())
