from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class HumorCrawlerAgent(TypeCrawlerAgent):
    """Collect anonymized community trend signals without treating them as facts."""

    profile = CrawlerProfile(
        content_type=ContentType.HUMOR,
        source_roles=frozenset({"trend"}),
        purpose="fan debate, prediction, preference and humor topics; no factual promotion",
        capture_primary_page_assets=False,
        required_signals=(
            ("community_signal", r"#anime|anime|アニメ|애니|팬|fan|vote|poll|토론|떡밥"),
        ),
    )
