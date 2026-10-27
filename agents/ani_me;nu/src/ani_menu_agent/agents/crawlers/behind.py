from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class BehindCrawlerAgent(TypeCrawlerAgent):
    """Collect official making-of, production, recording and staff interview leads."""

    profile = CrawlerProfile(
        content_type=ContentType.BEHIND,
        source_roles=frozenset({"primary", "editorial", "discovery"}),
        purpose="production notes, storyboarding, recording, staff interviews and creation process",
        asset_page_limit=1,
        required_signals=(
            ("production_process", r"making|production|制作|舞台裏|制作秘話|제작|비하인드|메이킹"),
            ("creator_or_staff", r"interview|staff|監督|制作陣|インタビュー|인터뷰|제작진"),
        ),
    )
