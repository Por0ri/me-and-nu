from ani_menu_agent.sources import load_source_registry


def test_source_registry_is_valid() -> None:
    registry = load_source_registry()

    assert len(registry.sources) >= 20
    assert {source.id for source in registry.automatic_sources} == {
        "anime_schedule_rss",
        "prtimes_rss",
        "orb_cafe_official",
        "natsume_official",
        "bs4_anime_official",
        "tv_tokyo_anime_official",
        "bn_pictures_keroro_official",
        "haikyu_official",
        "spy_family_official",
        "ja_kyosai_official",
        "naruto_official",
        "attack_on_titan_official",
    }
    assert {source.id for source in registry.collectable_sources} == {
        "anime_schedule_rss",
        "prtimes_rss",
        "orb_cafe_official",
        "natsume_official",
        "bs4_anime_official",
        "tv_tokyo_anime_official",
        "bn_pictures_keroro_official",
        "haikyu_official",
        "spy_family_official",
        "ja_kyosai_official",
        "naruto_official",
        "attack_on_titan_official",
        "ruliweb_anime_info",
        "reddit_anime",
    }
    aniplus = next(source for source in registry.sources if source.id == "aniplus")
    assert aniplus.status == "terms_review"
    natsume = next(source for source in registry.sources if source.id == "natsume_official")
    assert natsume.work_title_ko == "나츠메 우인장"
    assert natsume.work_title_original == "夏目友人帳"
    assert any(source.role == "primary" for source in registry.sources)
    assert any(source.role == "trend" for source in registry.sources)
    assert len({source.id for source in registry.sources}) == len(registry.sources)
