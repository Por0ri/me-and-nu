import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from ani_menu_agent.models import ContentPackage, Fact, SourceEvidence
from ani_menu_agent.storage import AgentRepository, ExpiredFeedContentError, prepare_package_lifecycle


NOW = datetime(2026, 11, 23, 6, tzinfo=UTC)
QUOTE = "販売期間：2026年11月12日(木)17:00～2026年12月17日(木)16:59"
END = datetime(2026, 12, 17, 7, 59, tzinfo=UTC)
OLD_LIMIT = "2027-02-20T15:00:00+00:00"


def goods(details, *, stored_limit=None, fact_type="sales_period"):
    return ContentPackage(
        request_id="goods-lottery", topic="아랴 생일 온라인 추첨", content_kind="information",
        content_type="goods", feed_valid_until=stored_limit,
        facts=[Fact(id="sale", statement="온라인 추첨 판매 기간", source_ids=["official"],
                    fact_type=fact_type, details=details)],
    )


@pytest.mark.parametrize(("details", "expected"), [
    ({"sale_end": "2026-12-17", "source_quote": QUOTE}, END.isoformat()),
    ({"sales_end": "2026-12-17", "source_quote": "販売期間は12月17日16時59分まで。"}, END.isoformat()),
    ({"reservation_end": "2026-12-17"}, "2026-12-17T15:00:00+00:00"),
    ({"preorder_end": "2026-12-17T02:59:00-05:00"}, END.isoformat()),
    ({"sale_end": "2026-12-17", "source_quote": "12月17日(木)10:00発売"}, "2026-12-17T15:00:00+00:00"),
    ({"sale_end": "2026-12-17", "source_quote": "2025年12月17日16時59分まで"}, "2026-12-17T15:00:00+00:00"),
])
def test_goods_expiry_only_blocks_new_work_at_verified_source_close(details, expected):
    stop = datetime.fromisoformat(expected)
    for stored_limit in (None, OLD_LIMIT):
        package = goods(details, stored_limit=stored_limit)
        prepare_package_lifecycle(package, stop - timedelta(seconds=1), require_open_sale=True)
        assert package.feed_valid_until is None
        with pytest.raises(ExpiredFeedContentError):
            prepare_package_lifecycle(package, stop, require_open_sale=True)


def test_release_without_sale_end_has_no_exposure_deadline():
    package = goods({"release_date": "2026-12-17", "sale_start": "2026-11-12"})
    for day in (NOW, datetime(2028, 1, 1, tzinfo=UTC)):
        prepare_package_lifecycle(package, day, require_open_sale=True)
        assert package.feed_valid_until is package.purge_at is None


def test_agent_handoff_does_not_overwrite_apply_or_purge_backend_deadlines(tmp_path, monkeypatch):
    monkeypatch.setattr("ani_menu_agent.storage._utc_now", lambda: NOW)
    repo = AgentRepository(path=tmp_path / "goods.sqlite3")
    package = goods({"sale_end": "2026-12-17", "source_quote": QUOTE}, stored_limit=OLD_LIMIT)
    repo.persist_generated_content(package, "generated_pending_review")
    repo.enqueue_for_feed(package)
    deadline = "2010-01-01T00:00:00+00:00"
    with repo.connect() as connection:
        for table in ("content", "content_drafts", "feed_queue"):
            assert connection.execute(f"SELECT valid_until FROM {table}").fetchone()[0] is None
            connection.execute(f"UPDATE {table} SET valid_until=?", (deadline,))
        for table in ("content_drafts", "feed_queue"):
            connection.execute(f"UPDATE {table} SET purge_at=?", (deadline,))
    repo.initialize()
    draft = repo.load_generated_draft(package.request_id)
    assert draft.feed_valid_until is None
    repo.persist_generated_content(draft, "generated_pending_review")
    repo.enqueue_for_feed(draft)
    repo.cleanup_expired(now=END + timedelta(days=365))
    with repo.connect() as connection:
        for table in ("content", "content_drafts", "feed_queue"):
            assert connection.execute(f"SELECT valid_until FROM {table}").fetchone()[0] == deadline
    assert [row["request_id"] for row in repo.active_feed_items(now=END)] == [package.request_id]
    monkeypatch.setattr("ani_menu_agent.storage._utc_now", lambda: END)
    with pytest.raises(ExpiredFeedContentError):
        repo.load_generated_draft(package.request_id)
    with pytest.raises(ExpiredFeedContentError):
        repo.enqueue_for_feed(package)


def test_existing_required_draft_dates_accept_undated_output(tmp_path):
    reference = AgentRepository(path=tmp_path / "reference.sqlite3")
    with reference.connect() as connection:
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='content_drafts'").fetchone()[0]
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(schema.replace("valid_until TEXT,", "valid_until TEXT NOT NULL,")
                                 .replace("purge_at TEXT,", "purge_at TEXT NOT NULL,"))
    repo = AgentRepository(path=path)
    package = goods({"sale_start": "2026-12-01"})
    repo.persist_generated_content(package, "generated_pending_review")
    with repo.connect() as connection:
        row = connection.execute("SELECT valid_until,purge_at,package_json FROM content_drafts").fetchone()
        assert not row["valid_until"] and not row["purge_at"]
        assert json.loads(row["package_json"])["feed_valid_until"] is None
        assert connection.execute("SELECT valid_until FROM content").fetchone()[0] is None


def test_expired_legacy_crawl_cannot_reach_generation_even_with_old_90_day_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("ani_menu_agent.storage._utc_now", lambda: NOW)
    repo = AgentRepository(path=tmp_path / "legacy-crawl.sqlite3")
    package = goods({"reservation_end": "2026-12-17", "source_quote": QUOTE})
    source = SourceEvidence(
        id="one_piece_official", title="ONE PIECE 예약 판매", publisher="ONE PIECE.com",
        url="https://one-piece.com/news/limited", is_official=True, role="primary",
    )
    package.sources = [source]
    package.facts[0].source_ids = [source.id]
    repo.persist_information(package)
    with repo.connect() as connection:
        connection.execute("UPDATE agent_records SET valid_until=?", (OLD_LIMIT,))
    monkeypatch.setattr("ani_menu_agent.storage._utc_now", lambda: END)
    with pytest.raises(ExpiredFeedContentError):
        repo.load_content_package(package.request_id, "information", content_type="goods")
