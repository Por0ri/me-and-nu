import pytest

from ani_menu_agent.community import CommunityTrendInput, to_humor_package
from ani_menu_agent.models import ContentKind
from ani_menu_agent.source_policy import validate_generation_sources


def test_automated_community_package_uses_registered_source_id() -> None:
    signal = CommunityTrendInput(
        source_id="mastodon_social_anime",
        source_url="https://mastodon.social/@animefan/123",
        topic="작품 속 라이벌 관계 토론",
        summary="팬들이 주인공과 라이벌 중 누가 더 강한지 근거를 나누며 토론하는 주제입니다.",
    )

    package = to_humor_package(signal)

    assert package.sources[0].id == "mastodon_social_anime"
    assert package.facts[0].details["source_url"] == str(signal.source_url)
    assert validate_generation_sources(
        [source.model_dump(mode="json") for source in package.sources],
        [fact.model_dump(mode="json") for fact in package.facts],
        ContentKind.HUMOR,
    ) == {"mastodon_social_anime"}


def test_unapproved_manual_community_source_is_rejected() -> None:
    signal = CommunityTrendInput(
        source_id="theqoo_anime_trend",
        source_url="https://theqoo.net/123",
        topic="작품 속 라이벌 관계 토론",
        summary="팬들이 주인공과 라이벌 중 누가 더 강한지 근거를 나누며 토론하는 주제입니다.",
    )

    with pytest.raises(ValueError, match="자동 수집 승인"):
        to_humor_package(signal)
