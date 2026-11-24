"""Carry the primary article's schedule from collection to authoring and storage."""

from ani_menu_agent.models import ContentPackage, Fact
from ani_menu_agent.source_freshness import extract_source_schedule


_DATE_LABELS = {
    "event_start": "행사 시작", "event_end": "행사 종료",
    "sale_start": "판매 시작", "sale_end": "판매 종료",
    "reservation_start": "예약 시작", "reservation_end": "예약 마감",
    "release_date": "발매일", "premiere_date": "첫 방송일",
}


def primary_source_schedule(package: ContentPackage, content_type: str) -> dict[str, str]:
    """Recover schedules in queued packages written before typed dates were stored."""
    if not package.sources or content_type not in {"event", "goods", "news"}:
        return {}
    primary = package.sources[0]
    facts = [f for f in package.facts if primary.id in f.source_ids]
    for fact in facts:
        if fact.fact_type == "source_schedule":
            dates = extract_source_schedule({"source_schedule": fact.details}, content_type)
            if dates:
                return dates
    capture = next((f for f in facts if f.fact_type == "source_page_capture"), None)
    record = {"title": primary.title, "published_at": primary.published_at}
    if capture:
        record["text"] = capture.statement
        if content_type == "event" and capture.details.get("event_date_evidence"):
            record.update(event_start=package.event_start, event_end=package.event_end)
    else:
        record["facts"] = [f.model_dump(mode="json") for f in facts]
    return extract_source_schedule(record, content_type)


def attach_source_schedule(package: ContentPackage, dates: dict[str, str]) -> None:
    if not package.sources or package.content_type not in {"event", "goods", "news"}:
        return
    source_id = package.sources[0].id
    if package.content_type == "event":
        # The legacy event fields carry calendar dates; the fact retains full precision.
        for key in ("event_start", "event_end"):
            value = dates.get(key)
            setattr(package, key, value[:10] if value and len(value) >= 10 else None)
    else:
        package.event_start = package.event_end = None
    for fact in package.facts:
        if (fact.id == f"{package.request_id}-sales-period"
                and fact.fact_type == "sales_period" and source_id in fact.source_ids):
            # Old drafts can cite this ID. Preserve the excerpt, replacing the
            # old generated claim that a lone sale date marked the deadline.
            quote = fact.details.get("source_quote")
            if isinstance(quote, str) and quote.strip():
                fact.statement = quote
                fact.details = {"source_quote": quote}
                fact.fact_type = "source_excerpt"
    package.facts = [
        f for f in package.facts
        if not (f.fact_type == "source_schedule" and source_id in f.source_ids)
    ]
    if dates:
        package.facts.append(Fact(
            id=f"{package.request_id}-source-schedule",
            statement=" · ".join(f"{_DATE_LABELS[k]} {v}" for k, v in dates.items()),
            source_ids=[source_id], fact_type="source_schedule",
            details=dict(dates), time_sensitive=True,
        ))
