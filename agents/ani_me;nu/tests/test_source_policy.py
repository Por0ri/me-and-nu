import pytest

from ani_menu_agent.models import ContentKind
from ani_menu_agent.source_policy import (
    validate_content_type_source_support,
    validate_generation_sources,
)
from ani_menu_agent.sources.models import Source, SourceRegistry


def _fact(source_id: str, *, verified: bool = True) -> dict:
    return {"id": "fact-1", "source_ids": [source_id], "verified": verified}


def test_active_discovery_feed_cannot_be_used_as_official_factual_evidence() -> None:
    source = {
        "id": "prtimes_rss",
        "url": "https://prtimes.jp/main/html/rd/p/000000001.000000001.html",
        "role": "discovery",
        "is_official": False,
    }

    with pytest.raises(ValueError):
        validate_generation_sources([source], [_fact("prtimes_rss")], ContentKind.INFORMATION)


def test_paused_primary_source_invalidates_old_database_facts() -> None:
    source = {
        "id": "naruto_official",
        "url": "https://naruto-official.com/news/123",
        "role": "primary",
        "is_official": True,
    }

    with pytest.raises(ValueError):
        validate_generation_sources([source], [_fact("naruto_official")], "information")


def test_humor_requires_an_approved_automated_trend_source(monkeypatch) -> None:
    source_config = Source(
        id="theqoo_anime_trend",
        name="Community trend test",
        url="https://theqoo.net/",
        category="community_trend",
        role="trend",
        collection="html",
        status="trend_only",
        region="KR",
    )
    monkeypatch.setattr(
        "ani_menu_agent.source_policy.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[source_config]),
    )
    source = {
        "id": "theqoo_anime_trend",
        "url": "https://theqoo.net/anime/123",
        "role": "trend",
        "is_official": False,
    }

    eligible = validate_generation_sources(
        [source], [_fact("theqoo_anime_trend", verified=False)], ContentKind.HUMOR
    )

    assert eligible == {"theqoo_anime_trend"}
    validate_content_type_source_support("humor", eligible)


def test_manual_trend_source_cannot_be_used_for_automatic_generation(monkeypatch) -> None:
    source_config = Source(
        id="theqoo_anime_trend",
        name="Community trend test",
        url="https://theqoo.net/",
        category="community_trend",
        role="trend",
        collection="manual",
        status="trend_only",
        region="KR",
    )
    monkeypatch.setattr(
        "ani_menu_agent.source_policy.load_source_registry",
        lambda: SourceRegistry(version=1, sources=[source_config]),
    )
    source = {
        "id": "theqoo_anime_trend",
        "url": "https://theqoo.net/anime/123",
        "role": "trend",
        "is_official": False,
    }

    with pytest.raises(ValueError):
        validate_generation_sources(
            [source], [_fact("theqoo_anime_trend", verified=False)], ContentKind.HUMOR
        )


def test_content_type_must_be_supported_by_one_of_its_sources() -> None:
    with pytest.raises(ValueError):
        validate_content_type_source_support("event", {"anime_schedule_rss"})


def test_active_reference_database_is_valid_for_supported_catalog_facts() -> None:
    source = {
        "id": "kitsu_anime_catalog",
        "url": "https://kitsu.io/anime/20",
        "role": "reference",
        "is_official": False,
    }

    eligible = validate_generation_sources(
        [source], [_fact("kitsu_anime_catalog")], ContentKind.INFORMATION
    )

    assert eligible == {"kitsu_anime_catalog"}
    validate_content_type_source_support("guide", eligible)
    with pytest.raises(ValueError):
        validate_content_type_source_support("event", eligible)


@pytest.mark.parametrize(
    ("source_id", "url", "content_type"),
    [
        ("anime_hack_events", "https://anime.eiga.com/news/127701", "event"),
        ("eeo_events", "https://eeo.today/media/2026/10/28/407578/", "event"),
        ("anime_anime_goods", "https://animeanime.jp/article/2026/10/29/104305.html", "goods"),
        ("inside_goods", "https://www.inside-games.jp/article/2026/10/29/190017.html", "goods"),
    ],
)
def test_credible_editorial_event_and_goods_articles_can_support_facts(
    source_id: str, url: str, content_type: str
) -> None:
    source = {
        "id": source_id,
        "url": url,
        "role": "editorial",
        "is_official": False,
    }

    eligible = validate_generation_sources(
        [source], [_fact(source_id)], ContentKind.INFORMATION
    )

    assert eligible == {source_id}
    validate_content_type_source_support(content_type, eligible)
