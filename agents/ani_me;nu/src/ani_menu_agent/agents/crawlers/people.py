from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class PeopleCrawlerAgent(TypeCrawlerAgent):
    """Collect cast, staff, creator and artist information from mapped sources."""

    profile = CrawlerProfile(
        content_type=ContentType.PEOPLE,
        source_roles=frozenset({"primary", "reference", "editorial", "discovery"}),
        purpose="voice cast, director/writer/studio profiles, OP/ED artist credits",
        asset_page_limit=1,
        required_signals=(
            (
                "person_name_or_role",
                r"\bcast\b|\bstaff\b|\bvoice actor\b|\bdirector\b|監督|声優|出演|성우|감독|작가",
            ),
            (
                "credit_or_profile",
                r"\bcredit\b|\bprofile\b|\bcast\b|\bstaff\b|出演者|출연|프로필|참여",
            ),
        ),
    )
