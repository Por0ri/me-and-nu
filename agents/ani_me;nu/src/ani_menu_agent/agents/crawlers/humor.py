from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class HumorCrawlerAgent(TypeCrawlerAgent):
    """Collect community humor and sourced production-behind stories as one type."""

    profile = CrawlerProfile(
        content_type=ContentType.HUMOR,
        source_roles=frozenset({"trend", "primary", "editorial", "discovery"}),
        purpose=(
            "fan debate, prediction and humor signals plus source-backed making-of, "
            "production interviews and fandom-culture stories"
        ),
        capture_primary_page_assets=True,
        asset_page_limit=3,
        required_signals=(
            (
                "community_signal",
                r"#anime|anime|fan|vote|poll|discussion|meme|humor|"
                r"애니|팬덤|투표|토론|밈|유머",
            ),
            (
                "production_behind",
                r"behind[ -]?the[ -]?scenes|making(?:[ -]?of)?|production notes|"
                r"production interview|staff interview|production report|recording report|"
                r"制作秘話|舞台裏|メイキング|制作過程|製作陣|비하인드|메이킹|"
                r"제작진 인터뷰|제작 과정",
            ),
        ),
    )
