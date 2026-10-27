from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class NewsCrawlerAgent(TypeCrawlerAgent):
    """Collect official anime announcements and press-release discovery leads."""

    profile = CrawlerProfile(
        content_type=ContentType.NEWS,
        source_roles=frozenset({"primary", "discovery"}),
        purpose="announcement, new season, broadcast/streaming schedule, PV and production news",
        required_signals=(
            ("announcement", r"announce|announcement|발표|공개|결정|解禁|発表"),
            ("date", r"20\d{2}|\d{1,2}月|\d{1,2}월|\d{1,2}日|\d{1,2}일|配信|放送|방영"),
        ),
    )
