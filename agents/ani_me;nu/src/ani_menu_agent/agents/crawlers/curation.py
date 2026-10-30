from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class CurationCrawlerAgent(TypeCrawlerAgent):
    """Collect editorial polls/rankings with attribution; preserve method and sample context."""

    profile = CrawlerProfile(
        content_type=ContentType.CURATION,
        source_roles=frozenset({"editorial", "reference", "discovery"}),
        purpose="rankings, polls and recommendation sets with publisher/method context",
        capture_primary_page_assets=False,
        required_signals=(
            ("ranking_or_poll", r"ranking|rank|poll|vote|top \d|ランキング|投票|순위|랭킹|투표"),
            ("method_or_sample", r"vote|votes|respondents|sample|投票|回答|응답|표본|집계|조사"),
        ),
    )
