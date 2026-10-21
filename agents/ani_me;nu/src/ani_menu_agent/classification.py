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


def infer_strong_content_type(package: ContentPackage) -> str | None:
    """Return a type only when multiple independent cues agree; otherwise defer to LLM."""
    fact_types = {fact.fact_type.strip().lower() for fact in package.facts if fact.verified}
    text = " ".join(
        [package.topic, *(fact.statement for fact in package.facts if fact.verified)]
    )
    goods_type_hits = len(fact_types & _GOODS_FACT_TYPES)
    event_type_hits = len(fact_types & _EVENT_FACT_TYPES)
    goods_term_hits = len(set(_GOODS_TERMS.findall(text)))
    event_term_hits = len(set(_EVENT_TERMS.findall(text)))

    if goods_type_hits >= 2 or (goods_type_hits >= 1 and goods_term_hits >= 2):
        return "goods"
    if event_type_hits >= 2 or (event_type_hits >= 1 and event_term_hits >= 2):
        return "event"
    return None
