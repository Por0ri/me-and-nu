import pytest

from ani_menu_agent.models import ContentPackage, Fact, SourceEvidence
from ani_menu_agent.storage import AgentRepository


def make_package(content_type, schedule):
    source = SourceEvidence(id="news-source", title="Original", publisher="Test", url="https://news.example/item")
    return ContentPackage(
        request_id="period-example", topic="Schedule", content_kind="information", content_type=content_type,
        sources=[source],
        facts=[Fact(id="schedule", statement="Source dates", source_ids=[source.id],
                    fact_type="source_schedule", details=schedule)],
    )


def capture_for(package, **fields):
    source = package.sources[0]
    return {"source_id": source.id, "url": str(source.url), "title": source.title,
            "text": "Source text", "content_type": package.content_type, **fields}


def schedule_rows(connection, name):
    return [tuple(row) for row in connection.execute(
        f"SELECT schedule_kind, starts_at, ends_at FROM {name} ORDER BY schedule_kind"
    )]


def test_sale_schedule_survives_crawl_generation_and_source_correction(tmp_path):
    repo = AgentRepository(path=tmp_path / "sale.sqlite3")
    dates = {"sale_start": "2026-11-12T17:00", "sale_end": "2026-12-17T16:59",
             "reservation_start": "2026-11-01"}
    package = make_package("goods", dates)
    repo.persist_page_capture(capture_for(package, source_schedule=dates), "JP", package=package)
    expected = [("reservation", "2026-11-01", None), ("sale", "2026-11-12T17:00", "2026-12-17T16:59")]
    with repo.connect() as conn:
        assert schedule_rows(conn, "raw_document_schedule") == expected
        assert schedule_rows(conn, "content_schedule") == []

    # Generation adds a second source without a schedule. Only the primary source supplied the dates.
    package.sources.append(SourceEvidence(id="art", title="Art", publisher="Other", url="https://art.example/i"))
    repo.persist_generated_content(package, "generated_pending_review")
    with repo.connect() as conn:
        assert schedule_rows(conn, "content_schedule") == expected
        assert conn.execute("SELECT DISTINCT source_url FROM content_schedule").fetchone()[0] == str(package.sources[0].url)
        assert conn.execute("SELECT event_detail_id FROM content").fetchone()[0] is None

    # The source corrects its release and no longer gives a sale end or reservation window.
    revised = {"sale_start": "2026-11-13"}
    repo.persist_page_capture(capture_for(package, source_schedule=revised), "JP", package=package)
    repo.persist_generated_content(package, "generated_pending_review")
    with repo.connect() as conn:
        assert schedule_rows(conn, "content_schedule") == [("sale", "2026-11-13", None)]
    repo.persist_page_capture(capture_for(package, source_schedule={}), "JP", package=package)
    with repo.connect() as conn:
        assert schedule_rows(conn, "content_schedule") == []


@pytest.mark.parametrize(
    "content_type,details,expected,expected_event",
    [
        ("news", {"premiere_date": "2026-10"}, [("premiere", "2026-10", None)], None),
        ("goods", {"release_date": "2026-12-15"}, [("release", "2026-12-15", None)], None),
        ("event", {"event_start": "2027-03"}, [("event", "2027-03", None)], (None, None)),
        ("event", {"event_start": "2027-03-07T18:30"}, [("event", "2027-03-07T18:30", None)], ("2027-03-07", None)),
        ("event", {"event_end": "2027-03-14"}, [("event", None, "2027-03-14")], (None, "2027-03-14")),
    ],
)
def test_source_schedule_fact_preserves_precision_and_nulls(tmp_path, content_type, details, expected, expected_event):
    repo = AgentRepository(path=tmp_path / "dates.sqlite3")
    package = make_package(content_type, details)
    repo.persist_generated_content(package, "generated_pending_review")
    with repo.connect() as conn:
        assert schedule_rows(conn, "content_schedule") == expected
        row = conn.execute("SELECT starts_on, ends_on FROM event_detail").fetchone()
        assert (tuple(row) if row else None) == expected_event


def test_schema_upgrade_preserves_existing_content_and_new_schedule_writes(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    repo = AgentRepository(path=path)
    package = make_package("goods", {"sale_start": "2026-12-01"})
    repo.persist_generated_content(package, "generated_pending_review")
    with repo.connect() as conn:
        content_id = conn.execute("SELECT content_id FROM content").fetchone()[0]
        conn.execute("DROP VIEW content_schedule")
        conn.execute("DROP TABLE raw_document_schedule")
    upgraded = AgentRepository(path=path)
    upgraded.persist_generated_content(package, "generated_pending_review")
    with upgraded.connect() as conn:
        assert conn.execute("SELECT content_id FROM content_schedule").fetchone()[0] == content_id
        assert schedule_rows(conn, "content_schedule") == [("sale", "2026-12-01", None)]


def test_corrected_event_capture_overrides_an_older_draft_schedule(tmp_path):
    repo = AgentRepository(path=tmp_path / "event.sqlite3")
    package = make_package("event", {"event_start": "2027-03-07", "event_end": "2027-03-14",
                                     "reservation_start": "2027-02-01T10:00"})
    capture = capture_for(package, source_schedule={"event_start": "2027-03-07T10:00"})
    repo.persist_page_capture(capture, "JP", package=package)
    repo.persist_generated_content(package, "generated_pending_review")
    with repo.connect() as conn:
        assert tuple(conn.execute(
            "SELECT starts_on,ends_on,reservation_opens_at FROM event_detail JOIN content USING (event_detail_id)"
        ).fetchone()) == ("2027-03-07", None, None)
        assert schedule_rows(conn, "content_schedule") == [("event", "2027-03-07T10:00", None)]
