from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class CharacterCrawlerAgent(TypeCrawlerAgent):
    """Resolve a named work before collecting character profile/relationship data."""

    profile = CrawlerProfile(
        content_type=ContentType.CHARACTER,
        source_roles=frozenset({"primary", "reference", "discovery"}),
        purpose="character identity, profile, birthday, relationships and sourced trivia",
        require_work_title=True,
        asset_page_limit=2,
        required_signals=(
            (
                "character_profile",
                r"character|profile|birthday|誕生日|キャラクター|인물|캐릭터|생일",
            ),
            ("work_name", r"anime|アニメ|작품|登場人物|人物"),
        ),
    )
