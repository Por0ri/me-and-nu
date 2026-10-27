from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class ReviewCrawlerAgent(TypeCrawlerAgent):
    """Collect attributed editorial criticism and comparative review leads."""

    profile = CrawlerProfile(
        content_type=ContentType.REVIEW,
        source_roles=frozenset({"editorial", "discovery"}),
        purpose="reviewer-attributed criticism and interpretation, never objective fact claims",
        capture_primary_page_assets=False,
        required_signals=(
            ("review_or_criticism", r"review|評|批評|感想|리뷰|평론|평가|감상"),
            ("attributed_author", r"reviewer|author|writer|by |評者|著者|작성자|기자"),
        ),
    )
