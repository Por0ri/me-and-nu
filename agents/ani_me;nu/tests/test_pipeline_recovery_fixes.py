import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ani_menu_agent import source_freshness
from ani_menu_agent.classification import infer_strong_content_type
from ani_menu_agent.extraction import FactCandidate, PageFactExtraction, _evidence_backed_facts
from ani_menu_agent.models import ContentKind, ContentPackage, Fact, SourceEvidence
from ani_menu_agent.pipelines.common.jobs import PipelineJobStatus
from ani_menu_agent.pipelines.workers import runtime
from ani_menu_agent.storage import AgentRepository, prepare_package_lifecycle


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

    assert prepared.feed_valid_until is None


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


class _ReleaseCheckDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 16, 12, tzinfo=tz or UTC)


def _release_window_package(quote="2027年2月中旬頃発売予定") -> ContentPackage:
    return ContentPackage(
        request_id="release-window",
        topic="ONE PIECE withCAT goods",
        content_kind=ContentKind.INFORMATION,
        content_type="goods",
        menu_work_ids=[8],
        sources=[SourceEvidence(
            id="one_piece_official", title="ONE PIECE withCAT goods",
            url="https://one-piece.com/news/83393/index.html", publisher="ONE PIECE.com",
            is_official=True,
        )],
        facts=[Fact(
            id="release-period", statement=quote, source_ids=["one_piece_official"],
            fact_type="product_availability", verified=False, time_sensitive=True,
            details={
                "availability_status": "upcoming_release", "release_precision": "mid_month",
                "release_text": quote, "source_quote": quote, "evidence_kind": "source_release_window",
            },
        )],
    )


def test_goods_release_window_survives_sqlite_reload_without_invented_dates(monkeypatch):
    monkeypatch.setattr(source_freshness, "datetime", _ReleaseCheckDateTime)
    repository = AgentRepository()
    package = _release_window_package()
    repository.persist_information(package)

    loaded = repository.load_content_package(package.request_id, "information", content_type="goods")

    assert runtime._hold_period_package(
        repository, loaded.model_dump(mode="json"), "goods", loaded.request_id,
    ) is None
    assert loaded.event_start is None and loaded.event_end is None
    assert loaded.facts[0].details == package.facts[0].details
    assert "release_date" not in loaded.facts[0].details
    assert "sale_end" not in loaded.facts[0].details
    assert not any(key.startswith("release_window_") for key in loaded.facts[0].details)


@pytest.mark.parametrize(("quote", "additional_fact", "expected_status"), [
    ("2026年10月中旬頃発売予定", None, "expiry_unresolved_held"),
    (
        "2027年2月中旬頃発売予定", {"availability_status": "sold_out"},
        "stale_candidate_skipped",
    ),
    ("2027年2月中旬頃発売予定", {"sale_end": "2026-11-15"}, "stale_candidate_skipped"),
])
def test_release_window_pipeline_still_holds_past_or_explicitly_expired_goods(
    monkeypatch, quote, additional_fact, expected_status,
):
    monkeypatch.setattr(source_freshness, "datetime", _ReleaseCheckDateTime)
    repository = AgentRepository()
    package = _release_window_package(quote)
    if additional_fact:
        package.facts.append(Fact(
            id="availability-end", statement="The source reports an ended sale.",
            source_ids=["one_piece_official"], fact_type="product_availability",
            details=additional_fact,
        ))
    assert runtime._hold_period_package(
        repository, package.model_dump(mode="json"), "goods", package.request_id,
    ) == expected_status
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT status, payload_json FROM agent_records WHERE record_id=?",
            (f"temporal-gate:{package.request_id}",),
        ).fetchone()
    assert row["status"] == expected_status
    assert json.loads(row["payload_json"])["status"] == expected_status


@pytest.mark.parametrize("already_present", [False, True])
def test_generation_worker_preserves_period_text_without_promoting_bounds_to_dates(
    monkeypatch, already_present,
):
    package = _release_window_package()
    quote = package.facts[0].statement
    if not already_present:
        package.facts = []
    persisted = []
    repository = SimpleNamespace(
        load_content_package=lambda *_args, **_kwargs: package,
        persist_information=lambda saved: persisted.append(saved.model_copy(deep=True)),
    )
    temporal = {
        "status": "active", "end_date": None, "availability_status": "upcoming_release",
        "release_date": None, "release_precision": "mid_month", "release_text": quote,
        "release_window_start": "2027-02-11", "release_window_end": "2027-02-20",
        "evidence": quote,
    }
    monkeypatch.setattr(runtime, "expiry_evidence", lambda *_args: dict(temporal))
    monkeypatch.setattr(runtime, "intake", lambda _state: {})
    monkeypatch.setattr(runtime, "rights_preflight", lambda _state: {})
    monkeypatch.setattr(runtime, "build_content_generation_agent", lambda: SimpleNamespace(
        invoke=lambda _state: {"status": "generation_failed"},
    ))
    job = SimpleNamespace(request_id=package.request_id, payload={"content_type": "goods"})

    outcome = runtime._process_generation(repository, job)

    assert outcome.status == PipelineJobStatus.FAILED  # The graph is an offline stop stub.
    assert len(persisted) == 1
    saved = persisted[0]
    assert len(saved.facts) == 1
    fact = saved.facts[0]
    assert fact.statement == quote
    assert fact.details == {
        "availability_status": "upcoming_release", "release_precision": "mid_month",
        "release_text": quote, "source_quote": quote, "evidence_kind": "source_release_window",
    }
    assert not fact.verified
    assert saved.event_start is None and saved.event_end is None
    assert saved.feed_valid_until is None
