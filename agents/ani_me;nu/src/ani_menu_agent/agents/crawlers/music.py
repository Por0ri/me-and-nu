from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class MusicCrawlerAgent(TypeCrawlerAgent):
    """Collect anime soundtrack, theme-song, album and artist-release leads."""

    profile = CrawlerProfile(
        content_type=ContentType.MUSIC,
        source_roles=frozenset({"primary", "discovery", "reference"}),
        purpose="anime OST/BGM, OP/ED, label release, track/artist credits and release dates",
        asset_page_limit=2,
        required_signals=(
            (
                "music_identity",
                r"music|soundtrack|ost|theme song|opening|ending|音楽|主題歌|楽曲|음악|주제가",
            ),
            (
                "artist_or_release",
                r"artist|singer|album|release|アーティスト|歌手|発売|아티스트|가수|앨범|발매",
            ),
        ),
    )
