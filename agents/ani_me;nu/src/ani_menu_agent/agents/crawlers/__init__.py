"""Ten type-specific crawler roles with shared collection infrastructure."""

from ani_menu_agent.agents.crawlers.character import CharacterCrawlerAgent
from ani_menu_agent.agents.crawlers.curation import CurationCrawlerAgent
from ani_menu_agent.agents.crawlers.event import EventCrawlerAgent
from ani_menu_agent.agents.crawlers.goods import GoodsCrawlerAgent
from ani_menu_agent.agents.crawlers.guide import GuideCrawlerAgent
from ani_menu_agent.agents.crawlers.humor import HumorCrawlerAgent
from ani_menu_agent.agents.crawlers.music import MusicCrawlerAgent
from ani_menu_agent.agents.crawlers.news import NewsCrawlerAgent
from ani_menu_agent.agents.crawlers.people import PeopleCrawlerAgent
from ani_menu_agent.agents.crawlers.review import ReviewCrawlerAgent

TYPE_CRAWLER_AGENTS = {
    agent.profile.content_type.value: agent()
    for agent in (
        NewsCrawlerAgent,
        EventCrawlerAgent,
        GuideCrawlerAgent,
        PeopleCrawlerAgent,
        GoodsCrawlerAgent,
        ReviewCrawlerAgent,
        HumorCrawlerAgent,
        CharacterCrawlerAgent,
        CurationCrawlerAgent,
        MusicCrawlerAgent,
    )
}


def get_type_crawler(content_type: str):
    try:
        return TYPE_CRAWLER_AGENTS[content_type]
    except KeyError as exc:
        valid = ", ".join(TYPE_CRAWLER_AGENTS)
        raise ValueError(f"지원하지 않는 콘텐츠 유형 {content_type!r}; 가능 유형: {valid}") from exc


__all__ = ["TYPE_CRAWLER_AGENTS", "get_type_crawler"]
