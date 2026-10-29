from ani_menu_agent.agents.crawlers.base import CrawlerProfile, TypeCrawlerAgent
from ani_menu_agent.models import ContentType


class GoodsCrawlerAgent(TypeCrawlerAgent):
    """Collect product, preorder, release, price and official collaboration goods leads."""

    profile = CrawlerProfile(
        content_type=ContentType.GOODS,
        source_roles=frozenset({"primary", "editorial", "discovery"}),
        purpose="product-specific identity, official release/preorder dates and price",
        asset_page_limit=3,
        required_signals=(
            ("product_identity", r"goods|merch|product|商品|グッズ|フィギュア|상품|굿즈|피규어"),
            ("release_or_sale", r"release|発売|販売|예약|발매|출시|판매|price|価格|가격"),
        ),
    )
