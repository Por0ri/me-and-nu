from ani_menu_agent.storage import AgentRepository
from ani_menu_agent.work_catalog import load_work_catalog


def test_work_catalog_keeps_canonical_work_ids_and_title_fields() -> None:
    catalog = load_work_catalog()

    assert catalog
    assert all(isinstance(row["menu_work_id"], int) for row in catalog)
    assert all("title_ko" in row and "title_romaji" in row for row in catalog)


def test_work_content_counts_report_counts_without_setting_generation_caps(tmp_path) -> None:
    repository = AgentRepository(path=tmp_path / "coverage.sqlite3")
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO content_work_links "
            "(request_id, menu_work_id, sort_order, is_primary) VALUES (?, ?, ?, ?)",
            ("request-a", 123, 1, 1),
        )
        connection.execute(
            "INSERT INTO content_drafts "
            "(request_id, topic, content_kind, content_type, status, package_json, "
            "valid_until, purge_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "request-a",
                "topic",
                "information",
                "news",
                "ready",
                "{}",
                "2030-01-01",
                "2030-02-01",
                "2026-01-01",
                "2026-01-01",
            ),
        )
        connection.execute(
            "INSERT INTO feed_queue "
            "(request_id, topic, content_type, package_json, queued_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("request-a", "topic", "news", "{}", "2026-01-01", "ready"),
        )

    assert repository.get_work_content_counts() == {
        123: {"draft_count": 1, "ready_count": 1}
    }
