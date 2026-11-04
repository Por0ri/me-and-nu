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
_EVENT_HEADLINE = re.compile(
    r"\b(event|exhibition|pop[ -]?up|festival|concert|ticket|screening|collab(?:oration)? cafe)\b"
    r"|イベント|展示会|展覧会|ポップアップ|開催|催事|公演|上映会|チケット|コラボカフェ"
    r"|행사|전시회|전시|팝업|개최|공연|상영회|티켓|콜라보 ?카페",
    re.IGNORECASE,
)


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
