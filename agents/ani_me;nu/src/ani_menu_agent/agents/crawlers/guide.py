from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class GuideCrawlerAgent(TypeCrawlerAgent):
    """Collect official/reference story, episode, world and viewing-order material."""

    profile = CrawlerProfile(
        content_type=ContentType.GUIDE,
        source_roles=frozenset({"primary", "reference", "discovery"}),
        purpose="work-grounded introductions, episode/arc order, terminology and relationships",
        asset_page_limit=2,
        required_signals=(
            (
                "story_or_episode",
                r"\bepisode\b|\bstory\b|\bseason\b|\bchapter\b|話|編|あらすじ|줄거리|에피소드|화",
            ),
            ("work_context", r"\bcharacter\b|キャラクター|登場人物|인물|작품|物語|世界観"),
        ),
    )
