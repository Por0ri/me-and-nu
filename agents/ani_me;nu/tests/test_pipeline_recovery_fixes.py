from datetime import UTC, datetime

from ani_menu_agent.classification import infer_strong_content_type
from ani_menu_agent.extraction import FactCandidate, PageFactExtraction, _evidence_backed_facts
from ani_menu_agent.models import ContentKind, ContentPackage, Fact
from ani_menu_agent.storage import prepare_package_lifecycle


def test_event_headline_wins_over_incidental_product_price_mention() -> None:
    package = ContentPackage(
        request_id="event-price",
        topic="Anime exhibition event and ticket information",
        content_kind=ContentKind.INFORMATION,
        content_type="goods",
        event_start="2026-11-10",
        event_end="2026-11-20",
        facts=[
            Fact(
                id="event-fact",
                statement="The exhibition runs from November 10 to 20.",
                source_ids=["source"],
                verified=True,
                fact_type="event_schedule",
            ),
            Fact(
                id="price-fact",
                statement="A commemorative item costs 500 yen.",
                source_ids=["source"],
                verified=True,
                fact_type="price",
            ),
        ],
    )

    assert infer_strong_content_type(package) == "event"


def test_non_event_event_end_does_not_expire_a_content_package() -> None:
    package = ContentPackage(
        request_id="news-with-date",
        topic="Anime news",
        content_kind=ContentKind.INFORMATION,
        content_type="news",
        event_end="2020-01-01",
    )

    prepared = prepare_package_lifecycle(
        package, now=datetime(2026, 11, 2, tzinfo=UTC)
    )

    assert datetime.fromisoformat(prepared.feed_valid_until) > datetime(2026, 11, 2, tzinfo=UTC)


def test_extraction_keeps_verified_fact_cap_and_accepts_short_quote() -> None:
    quotes = [f"detail-{index}" for index in range(35)]
    extracted = PageFactExtraction(
        topic="x",
        facts=[
            FactCandidate(
                statement=f"Fact {index} has useful details.",
                evidence_quote=quote,
                fact_type="general",
            )
            for index, quote in enumerate(quotes)
        ]
        + [FactCandidate(statement="OK", evidence_quote="s", fact_type="announcement")],
    )

    facts = _evidence_backed_facts(
        extracted,
        "source",
        " ".join(quotes) + " s",
        "request",
    )

    assert len(facts) == 30
    assert any(fact.details["source_quote"] == "s" for fact in facts)
