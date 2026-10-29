from pathlib import Path

from ani_menu_agent.generation import _infer_copy_mode
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.storage import AgentRepository

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"


def test_copy_mode_classifier_recognizes_direct_address_and_questions() -> None:
    assert _infer_copy_mode("애니 팬들아, 이번 소식 확인했어?") == "direct_address"
    assert _infer_copy_mode("이 반전, 예상했나요?") == "question"


def test_recent_copy_history_returns_only_compact_cover_and_ending(tmp_path: Path) -> None:
    repository = AgentRepository(path=tmp_path / "history.sqlite3")
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    package.editorial_angle = "팬들이 놓치기 쉬운 공식 전시 일정과 방문 준비를 짚는다"
    package.cover_mode = "surprising_fact"
    package.ending_mode = "fan_empathy"
    repository.persist_generated_content(package, "generated_pending_review")

    examples = repository.recent_copy_examples(
        exclude_request_id="new-request",
        limit=2,
    )

    assert len(examples) == 1
    assert examples[0]["topic"] == package.topic
    assert examples[0]["cover"]
    assert examples[0]["ending"]
    assert examples[0]["editorial_angle"] == package.editorial_angle
    assert examples[0]["cover_mode"] == "surprising_fact"
    assert examples[0]["ending_mode"] == "fan_empathy"
    assert set(examples[0]) == {
        "topic",
        "cover",
        "ending",
        "editorial_angle",
        "cover_mode",
        "ending_mode",
    }
    assert (
        repository.recent_copy_examples(
            exclude_request_id=package.request_id,
            limit=2,
        )
        == []
    )


def test_content_type_is_stored_and_queryable_for_feed_ranking(tmp_path: Path) -> None:
    repository = AgentRepository(path=tmp_path / "content-types.sqlite3")
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    package.content_type = "event"

    available = repository.active_content_types()
    assert {item["code"] for item in available} == {
        "news",
        "event",
        "guide",
        "people",
        "goods",
        "review",
        "humor",
        "character",
        "curation",
        "music",
    }
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO content_types (type_code, label_ko, sort_order, active) "
            "VALUES ('podcast', '팟캐스트', 12, 1)"
        )
    assert "podcast" in {item["code"] for item in repository.active_content_types()}

    repository.persist_generated_content(package, "ready_for_feed")
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT content_type FROM content_drafts WHERE request_id=?",
            (package.request_id,),
        ).fetchone()
    assert row["content_type"] == "event"

    repository.enqueue_for_feed(package)
    feed_item = repository.active_feed_items()[0]
    assert feed_item["content_type"] == "event"
    assert feed_item["package"]["content_type"] == "event"


def test_legacy_review_analysis_alias_normalizes_to_review() -> None:
    payload = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    payload.content_type = "review_analysis"

    normalized = ContentPackage.model_validate(payload.model_dump(mode="json"))

    assert normalized.content_type == "review"


def test_news_does_not_store_event_country() -> None:
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    package.content_type = "news"
    package.event_country = "JP"

    normalized = ContentPackage.model_validate(package.model_dump(mode="json"))

    assert normalized.event_country is None


def test_unclassified_content_cannot_enter_feed(tmp_path: Path) -> None:
    repository = AgentRepository(path=tmp_path / "unclassified.sqlite3")
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    package.content_type = None

    try:
        repository.enqueue_for_feed(package)
    except ValueError as exc:
        assert "분류되지 않은 콘텐츠" in str(exc)
    else:
        raise AssertionError("unclassified content must not be queued")
