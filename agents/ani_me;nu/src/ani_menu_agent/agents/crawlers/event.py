from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class EventCrawlerAgent(TypeCrawlerAgent):
    """Collect exhibitions, pop-ups, collaboration cafes, screenings and events."""

    profile = CrawlerProfile(
        content_type=ContentType.EVENT,
        source_roles=frozenset({"primary", "editorial", "discovery"}),
        purpose="event title, host, venue, country and explicit start/end period",
        asset_page_limit=3,
        required_signals=(
            ("event_period", r"20\d{2}|\d{1,2}月|\d{1,2}월|\d{1,2}日|\d{1,2}일"),
            ("venue", r"venue|location|住所|会場|開催地|매장|장소|층|점포|개최"),
        ),
    )
