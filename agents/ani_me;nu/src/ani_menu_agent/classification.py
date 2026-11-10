"""Deterministic high-confidence content type hints from verified evidence."""

from __future__ import annotations

import re

from ani_menu_agent.models import ContentPackage

_GOODS_FACT_TYPES = {
    "product",
    "product_description",
    "product_release",
    "goods",
    "merchandise",
    "manufacturer",
    "price",
    "dimensions",
    "sales_start",
    "sale_period",
    "product_name",
    "item",
}
_EVENT_FACT_TYPES = {
    "event",
    "event_schedule",
    "event_date",
    "event_venue",
    "ticket",
    "exhibition",
    "concert",
    "screening",
    "program",
}
_GOODS_TERMS = re.compile(
    r"\b(goods|merch(?:andise)?|product|manufacturer|price|retail|item|release)\b"
    r"|グッズ|商品|販売|価格|発売|製造元|特典"
    r"|굿즈|상품|가격|판매|출시|제조사|구성품|예약",
    re.IGNORECASE,
)
_EVENT_TERMS = re.compile(
    r"\b(event|exhibition|festival|concert|ticket|venue|screening)\b"
    r"|イベント|展示|会場|公演|上映|チケット"
    r"|행사|전시|축제|공연|상영|티켓|입장|개최",
    re.IGNORECASE,
)
_GOODS_HEADLINE = re.compile(
    r"\b(goods|merch(?:andise)?|product(?:s)?|figure|price|item lineup)\b"
    r"|グッズ|商品|新商品|商品化|発売|販売|フィギュア|굿즈|상품|상품화|신상품|피규어|판매|가격",
    re.IGNORECASE,
)
_PERIOD_GOODS_HEADLINE = re.compile(
    r"\b(goods|merch(?:andise)?|product(?:s)?|figure|price|item lineup)\b"
    r"|グッズ|商品|新商品|商品化|販売|フィギュア|굿즈|상품|신상품|피규어|판매|가격",
    re.IGNORECASE,
)
_EVENT_HEADLINE = re.compile(
    r"\b(event|exhibition|pop[ -]?up|festival|concert|ticket|screening|collab(?:oration)? cafe)\b"
    r"|イベント|展示会|展覧会|ポップアップ|開催|催事|公演|上映会|チケット|コラボカフェ"
    r"|행사|전시회|전시|팝업|개최|공연|상영회|티켓|콜라보 ?카페",
    re.IGNORECASE,
)
_TOEI_EVENT_HEADLINE = re.compile(
    r"開催|イベント|展覧会|展示会|出張店|ポップアップ|"
    r"コラボ(?:イベント|カフェ|ショップ|ストア)|上映(?:会|決定|実施)|公演|"
    r"フェス|フェスティバル|感謝祭|スペシャルステージ|舞台|ツアー|"
    r"ウォーターアドベンチャー|WATER SPECTACLE|ドラマティックステージ|"
    r"(?:映画祭|国際映画祭).{0,20}(?:上映|出展|招待|開催)|"
    r"(?:ショップ|ストア|店舗|店).{0,60}(?:グランド)?オープン|"
    r"(?:グランド)?オープン.{0,60}(?:ショップ|ストア|店舗|店)|"
    r"(?:KTS|テレビ局).{0,8}の日"
)
_TOEI_GOODS_HEADLINE = re.compile(
    r"グッズ|商品(?!化権)|販売|受注|予約|フィギュア|購入特典|限定商品|キャンペーン|"
    r"Blu[ -]?ray|ぬいぐるみ|アクリルスタンド|トレーディング"
)


def infer_toei_press_type(text: str) -> str:
    """Route Toei's mixed corporate press archive by the article's main subject."""
    event_hit = bool(_TOEI_EVENT_HEADLINE.search(text))
    goods_hit = bool(_TOEI_GOODS_HEADLINE.search(text))
    if event_hit:
        return "event"
    if goods_hit:
        return "goods"
    return "news"


def infer_period_content_type(package: ContentPackage) -> str | None:
    """Recover event/goods routing from a package that entered as generic news.

    Headline signals identify the article's main subject; explicit schedule or
    verified fact types break ties. Publication dates are never used here.
    """
    headline = " ".join([package.topic, *(source.title for source in package.sources)])
    event_hit = bool(_EVENT_HEADLINE.search(headline))
    goods_hit = bool(_PERIOD_GOODS_HEADLINE.search(headline))
    verified_types = {fact.fact_type.strip().lower() for fact in package.facts if fact.verified}
    event_hit = event_hit or bool(
        verified_types & _EVENT_FACT_TYPES
    ) or bool(package.event_start or package.event_end or package.event_location)
    goods_hit = goods_hit or bool(verified_types & _GOODS_FACT_TYPES)
    if event_hit and not goods_hit:
        return "event"
    if goods_hit and not event_hit:
        return "goods"
    if event_hit and goods_hit:
        event_score = 2 * bool(verified_types & _EVENT_FACT_TYPES) + 2 * bool(
            package.event_start or package.event_end or package.event_location
        ) + 3 * bool(_EVENT_HEADLINE.search(headline))
        goods_score = 2 * bool(verified_types & _GOODS_FACT_TYPES) + 3 * bool(
            _PERIOD_GOODS_HEADLINE.search(headline)
        )
        if event_score > goods_score:
            return "event"
        if goods_score > event_score:
            return "goods"
        # Both signals still make this period-sensitive. Prefer the event gate
        # on a tie so an ambiguous package cannot pass through as evergreen news.
        return "event"
    return None


def infer_strong_content_type(package: ContentPackage) -> str | None:
    """Infer event/goods from the article's main subject, not incidental mentions."""
    fact_types = {fact.fact_type.strip().lower() for fact in package.facts if fact.verified}
    verified_facts = [fact for fact in package.facts if fact.verified]
    headline = " ".join([package.topic, *(source.title for source in package.sources)])
    body = " ".join(fact.statement for fact in verified_facts)
    goods_type_hits = len(fact_types & _GOODS_FACT_TYPES)
    event_type_hits = len(fact_types & _EVENT_FACT_TYPES)
    goods_headline_hits = len(set(_GOODS_HEADLINE.findall(headline)))
    event_headline_hits = len(set(_EVENT_HEADLINE.findall(headline)))
    goods_body_hits = len(set(_GOODS_TERMS.findall(body)))
    event_body_hits = len(set(_EVENT_TERMS.findall(body)))

    goods_score = 2 * goods_type_hits + min(goods_body_hits, 3) + 3 * bool(goods_headline_hits)
    event_score = (
        2 * event_type_hits
        + min(event_body_hits, 3)
        + 3 * bool(event_headline_hits)
        + 2 * bool(package.event_start or package.event_end)
        + 2 * bool(package.event_location)
    )
    if event_score >= 4 and event_score >= goods_score + 1:
        return "event"
    if goods_score >= 4 and goods_score >= event_score + 1:
        return "goods"
    return None
