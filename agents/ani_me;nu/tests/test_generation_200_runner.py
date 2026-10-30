import json
import sqlite3
from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

from ani_menu_agent.models import CONTENT_TYPE_LABELS

_SPEC = spec_from_file_location(
    "generation_200_runner",
    Path(__file__).parents[1] / "scripts" / "run_generation_200.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_RUNNER = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RUNNER)


def _registry(url="https://prtimes.jp/"):
    source = SimpleNamespace(
        status="active",
        role="primary",
        collection="html",
        url=url,
        content_types=[
            SimpleNamespace(value="news"),
            SimpleNamespace(value="event"),
            SimpleNamespace(value="goods"),
            SimpleNamespace(value="music"),
        ],
    )
    return SimpleNamespace(sources=[source])


def test_registry_types_are_not_limited_by_discovery_route_hint():
    candidate = {
        "url": "https://prtimes.jp/main/html/rd/p/000000001.html",
        "content_types": ["news"],
    }

    assert _RUNNER.candidate_source_is_eligible(candidate, "goods", _registry())
    assert _RUNNER.candidate_source_is_eligible(candidate, "event", _registry())


def test_candidate_type_uses_topic_when_discovery_route_only_says_news():
    candidate = {
        "title": "TVアニメ 新作グッズ ぬいぐるみ発売決定",
        "url": "https://prtimes.jp/main/html/rd/p/000000001.html",
        "content_types": ["news"],
    }

    type_codes = [item.value for item in CONTENT_TYPE_LABELS]
    assert _RUNNER.select_candidate_type(candidate, type_codes, _registry()) == "goods"


@pytest.mark.parametrize(
    ("status", "bucket", "verdict"),
    [
        ("ready_for_feed", "passed", "pass"),
        ("rejected_archived", "rejected", "reject"),
        ("review_returned", "rejected", "reject"),
    ],
)
def test_final_artifacts_are_separated_with_decision_logs(
    tmp_path: Path, monkeypatch, status: str, bucket: str, verdict: str
) -> None:
    monkeypatch.setattr(_RUNNER, "OUTPUT", tmp_path)
    monkeypatch.setattr(_RUNNER, "LOG", tmp_path / "worker.log")
    result_path = tmp_path / "runs" / "candidate-attempt-01.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "status": status,
                "request_id": "request-1",
                "quality_report": {"total": 92, "decision": verdict},
                "qa_report": [],
                "package": {"request_id": "request-1"},
            }
        ),
        encoding="utf-8",
    )
    cards = result_path.parent / "cards" / "request-1"
    cards.mkdir(parents=True)
    (cards / "page_1.png").write_bytes(b"png")

    outcome = _RUNNER.organize_candidate_artifacts(
        "candidate-1", "news", 1, result_path, json.loads(result_path.read_text())
    )

    folder = tmp_path / bucket / "news-candidate-1-attempt-01"
    assert (folder / "result.json").is_file()
    assert (folder / "cards" / "page_1.png").is_file()
    assert json.loads((folder / "outcome.json").read_text(encoding="utf-8"))["verdict"] == verdict
    assert outcome["card_png_count"] == 1
    assert len((tmp_path / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_reviewed_target_counts_unique_final_passes_and_rejections(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(_RUNNER, "OUTPUT", tmp_path)
    monkeypatch.setenv("ANI_MENU_TARGET_MODE", "reviewed")
    (tmp_path / "outcomes.jsonl").write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {"verdict": "pass", "request_id": "one"},
                {"verdict": "reject", "request_id": "two"},
                {"verdict": "not_reviewed", "request_id": "three"},
                {"verdict": "pass", "request_id": "one"},
            )
        ),
        encoding="utf-8",
    )

    assert _RUNNER.completed_review_count() == 2
    assert _RUNNER.target_count() == 2


def test_jump_festa_program_routes_to_event_not_news():
    candidate = {
        "title": "Jump Festa 2027: My Hero Academia stage lineup announced",
        "url": "https://prtimes.jp/main/html/rd/p/000000002.html",
        "content_types": ["news"],
    }

    type_codes = [item.value for item in CONTENT_TYPE_LABELS]
    assert _RUNNER.select_candidate_type(candidate, type_codes, _registry()) == "event"


def test_japanese_jump_festa_program_routes_to_event_not_news():
    candidate = {
        "title": (
            "2026.10.19 ジャンプフェスタ2027『僕のヒーローアカデミア』"
            "ジャンプスーパーステージ詳細発表！"
        ),
        "url": "https://heroaca.com/news/28709",
        "content_types": ["news"],
    }

    type_codes = [item.value for item in CONTENT_TYPE_LABELS]
    assert (
        _RUNNER.select_candidate_type(candidate, type_codes, _registry("https://heroaca.com/"))
        == "event"
    )


def test_new_song_release_routes_to_music_not_news():
    candidate = {
        "title": "SUPER★DRAGON 新曲『本能』がドラマのオープニングテーマに決定",
        "url": "https://prtimes.jp/main/html/rd/p/000000003.html",
        "content_types": ["news"],
    }

    type_codes = [item.value for item in CONTENT_TYPE_LABELS]
    assert _RUNNER.select_candidate_type(candidate, type_codes, _registry()) == "music"


def test_news_section_url_does_not_turn_a_navigation_page_into_news():
    candidate = {
        "title": "Late-night anime schedule 02:00-02:30",
        "url": "https://prtimes.jp/news/late-night-anime/",
        "content_types": ["news"],
        "published_at": None,
    }

    type_codes = [item.value for item in CONTENT_TYPE_LABELS]
    assert _RUNNER.select_candidate_type(candidate, type_codes, _registry()) is None


def test_fresh_announcement_title_still_routes_to_news():
    candidate = {
        "title": "New anime season announced",
        "url": "https://prtimes.jp/news/season/",
        "content_types": ["news"],
        "published_at": "2026-10-22",
    }

    type_codes = [item.value for item in CONTENT_TYPE_LABELS]
    assert _RUNNER.select_candidate_type(candidate, type_codes, _registry()) == "news"


def test_dated_official_news_candidate_without_publication_metadata_routes_to_news():
    candidate = {
        "title": "2026.10.23 TV Anime Jujutsu Kaisen announcement",
        "url": "https://jujutsukaisen.jp/news/20261023_01.php",
        "content_types": ["news"],
    }

    assert _RUNNER.select_candidate_type(
        candidate,
        [item.value for item in CONTENT_TYPE_LABELS],
        _registry("https://jujutsukaisen.jp/news/"),
    ) == "news"


def test_japanese_game_preregistration_announcement_routes_to_news():
    candidate = {
        "title": "新作スマホ向け育成ゲーム 本日よりストア事前登録開始",
        "url": "https://prtimes.jp/news/game/",
        "content_types": ["news"],
        "published_at": None,
    }

    type_codes = [item.value for item in CONTENT_TYPE_LABELS]
    assert _RUNNER.select_candidate_type(candidate, type_codes, _registry()) == "news"


def test_dated_old_headline_is_skipped_for_time_sensitive_types():
    candidate = {"title": "2024.08.05 TVer配信決定", "published_at": None}

    assert _RUNNER.candidate_publication_date(candidate) == date(2024, 8, 5)
    assert _RUNNER.candidate_is_stale(candidate, "news", today=date(2026, 10, 23))
    assert _RUNNER.candidate_is_stale(candidate, "goods", today=date(2026, 10, 23))


def test_fresh_headlines_and_evergreen_types_are_not_skipped():
    candidate = {"title": "2026.09.25 Pop-up announcement", "published_at": None}

    assert not _RUNNER.candidate_is_stale(candidate, "event", today=date(2026, 10, 23))
    assert not _RUNNER.candidate_is_stale(candidate, "guide", today=date(2026, 10, 23))


def test_published_at_metadata_takes_precedence_over_title_date():
    candidate = {
        "title": "2024.08.05 old title prefix",
        "published_at": "2026-10-22T15:00:00Z",
    }

    assert _RUNNER.candidate_publication_date(candidate) == date(2026, 10, 22)
    assert not _RUNNER.candidate_is_stale(candidate, "news", today=date(2026, 10, 23))


def test_used_source_urls_normalize_tracking_parameters(tmp_path: Path):
    database = tmp_path / "used.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE content_drafts (status TEXT NOT NULL, package_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO content_drafts VALUES (?, ?)",
            (
                "ready_for_feed",
                json.dumps(
                    {
                        "sources": [
                            {"url": "https://example.com/news/123/?utm_source=rss"}
                        ]
                    }
                ),
            ),
        )

    assert _RUNNER.used_source_urls([database]) == {"https://example.com/news/123"}


def test_used_source_url_cache_does_not_hide_rejected_candidates(tmp_path: Path):
    database = tmp_path / "rejected.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE content_drafts (status TEXT NOT NULL, package_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO content_drafts VALUES (?, ?)",
            (
                "rejected_archived",
                json.dumps({"sources": [{"url": "https://example.com/news/123"}]}),
            ),
        )

    assert _RUNNER.used_source_urls([database]) == set()


def test_failed_candidate_url_can_be_retried_in_a_later_cycle():
    url = "https://example.com/news/123"
    accepted = set()
    attempted_this_cycle = {url}

    assert _RUNNER.candidate_url_skip_reason(url, accepted, attempted_this_cycle) == (
        "duplicate_candidate_url_in_cycle"
    )
    assert _RUNNER.candidate_url_skip_reason(url, accepted, set()) is None


def test_accepted_candidate_url_stays_a_permanent_duplicate():
    url = "https://example.com/news/123"

    assert _RUNNER.candidate_url_skip_reason(url, {url}, set()) == (
        "already_used_source_url"
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("실제 핵심은 허용 유형 ‘goods(굿즈)’에 맞춥니다.", "goods"),
        ("검증된 자료 유형은 event에 가깝지만 현재 분류는 news입니다.", "event"),
        ("유형이 부정확합니다. 다시 분류하세요.", None),
    ],
)
def test_content_type_recommendation_reads_only_explicit_taxonomy_codes(message, expected):
    payload = {
        "qa_report": [
            {"code": "CONTENT_TYPE_MISMATCH", "message": message},
        ]
    }

    assert _RUNNER.suggested_content_type_from_review(payload) == expected


@pytest.mark.parametrize(
    "status",
    [
        "needs_crawler_update",
        "already_used_source_url",
        "duplicate_skipped",
        "stale_candidate_skipped",
        "work_id_unresolved_skipped",
    ],
)
def test_permanent_candidate_skips_receive_long_cooldown(status: str):
    assert _RUNNER.candidate_cooldown_seconds({"result_status": status}) == 30 * 24 * 60 * 60


def test_transient_candidate_failure_keeps_short_retry_cooldown():
    assert _RUNNER.candidate_cooldown_seconds({"exit_code": 2}) == 300


def test_candidate_reopens_when_its_source_is_activated():
    candidate = {
        "title": "Jujutsu Kaisen new season announced",
        "url": "https://jujutsukaisen.jp/news/20261023_01.php",
        "published_at": "2026-10-23",
    }
    record = {
        "attempts": 1,
        "last_attempt": 1_800_000_000,
        "reason": "no_active_registered_source_for_candidate_host_and_type",
    }

    assert _RUNNER.reopen_candidate_if_source_became_eligible(
        candidate,
        [item.value for item in CONTENT_TYPE_LABELS],
        record,
        _registry("https://jujutsukaisen.jp/news/"),
    )
    assert record["attempts"] == 0
    assert record["last_attempt"] == 0
    assert "reason" not in record


def test_candidate_keeps_cooldown_if_source_is_still_unavailable():
    candidate = {
        "title": "Jujutsu Kaisen new season announced",
        "url": "https://jujutsukaisen.jp/news/20261023_01.php",
        "published_at": "2026-10-23",
    }
    record = {
        "attempts": 1,
        "last_attempt": 1_800_000_000,
        "reason": "no_active_registered_source_for_candidate_host_and_type",
    }

    assert not _RUNNER.reopen_candidate_if_source_became_eligible(
        candidate,
        [item.value for item in CONTENT_TYPE_LABELS],
        record,
        _registry("https://other.example.com/"),
    )
    assert record["attempts"] == 1
    assert record["last_attempt"] == 1_800_000_000


def test_agent_revision_reopens_only_recoverable_failed_candidates():
    checkpoint = {
        "agent_revision": "2026-10-24-source-led-type-review-parse-v20",
        "attempts": {
            "image-pool": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "needs_crawler_update",
                "blocker_codes": ["SOURCE_IMAGE_POOL_INSUFFICIENT"],
            },
            "stale": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "needs_crawler_update",
                "blocker_codes": ["SOURCE_STALE"],
            },
            "work-unresolved": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "work_id_unresolved_skipped",
            },
            "layout-and-language": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "rejected_archived",
                "blocker_codes": ["IMAGE_COMPOSITION_REJECTED", "LANGUAGE_CARD_FLOW"],
            },
            "language-only": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "rejected_archived",
                "blocker_codes": ["LANGUAGE_CARD_FLOW"],
            },
            "content-type": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "rejected_archived",
                "blocker_codes": ["CONTENT_TYPE_MISMATCH"],
            },
        }
    }

    assert _RUNNER.reopen_candidates_after_agent_revision(checkpoint) == 1
    assert checkpoint["attempts"]["image-pool"]["attempts"] == 1
    assert checkpoint["attempts"]["image-pool"]["result_status"] == "needs_crawler_update"
    assert checkpoint["attempts"]["stale"]["result_status"] == "needs_crawler_update"
    assert checkpoint["attempts"]["work-unresolved"]["result_status"] == (
        "work_id_unresolved_skipped"
    )
    assert checkpoint["attempts"]["layout-and-language"]["attempts"] == 1
    assert checkpoint["attempts"]["language-only"]["attempts"] == 1
    assert checkpoint["attempts"]["content-type"]["attempts"] == 0
    assert "result_status" not in checkpoint["attempts"]["content-type"]
    assert _RUNNER.reopen_candidates_after_agent_revision(checkpoint) == 0


def test_better_source_revision_retries_failed_duplicates_but_keeps_strict_review():
    checkpoint = {
        "agent_revision": "2026-10-24-preserve-requested-type-through-db-load-v37",
        "attempts": {
            "failed-duplicate": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "duplicate_skipped",
                "blocker_codes": [],
            },
            "image-shortage": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "needs_crawler_update",
                "blocker_codes": ["SOURCE_IMAGE_POOL_INSUFFICIENT"],
            },
            "accepted-duplicate-url": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "already_used_source_url",
            },
        },
    }

    assert _RUNNER.reopen_candidates_after_agent_revision(checkpoint) == 2
    assert checkpoint["attempts"]["failed-duplicate"]["attempts"] == 0
    assert checkpoint["attempts"]["image-shortage"]["attempts"] == 0
    assert checkpoint["attempts"]["accepted-duplicate-url"]["result_status"] == (
        "already_used_source_url"
    )


def test_source_art_eligibility_revision_retries_only_image_pool_failures():
    checkpoint = {
        "agent_revision": "2026-10-24-no-placeholder-card-art-v22",
        "attempts": {
            "missing-same-work-art": {
                "attempts": 2,
                "last_attempt": 10,
                "result_status": "needs_crawler_update",
                "blocker_codes": ["SOURCE_IMAGE_MISSING"],
            },
            "insufficient-image-pool": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "needs_crawler_update",
                "blocker_codes": ["SOURCE_IMAGE_POOL_INSUFFICIENT"],
            },
            "translation": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "rejected_archived",
                "blocker_codes": ["LANGUAGE_CARD_FLOW"],
            },
        },
    }

    assert _RUNNER.reopen_candidates_after_agent_revision(checkpoint) == 2
    assert checkpoint["attempts"]["missing-same-work-art"]["attempts"] == 0
    assert checkpoint["attempts"]["insufficient-image-pool"]["attempts"] == 0
    assert checkpoint["attempts"]["translation"]["attempts"] == 1


def test_reference_image_download_revision_retries_missing_image_candidates_only():
    checkpoint = {
        "agent_revision": "2026-10-24-source-art-detail-eligibility-v23",
        "attempts": {
            "missing-reference-art": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "needs_crawler_update",
                "blocker_codes": ["SOURCE_IMAGE_MISSING"],
            },
            "other-pool-shortage": {
                "attempts": 1,
                "last_attempt": 10,
                "result_status": "needs_crawler_update",
                "blocker_codes": ["SOURCE_IMAGE_POOL_INSUFFICIENT"],
            },
        },
    }

    assert _RUNNER.reopen_candidates_after_agent_revision(checkpoint) == 1
    assert checkpoint["attempts"]["missing-reference-art"]["attempts"] == 0
    assert checkpoint["attempts"]["other-pool-shortage"]["attempts"] == 1


def test_generation_worker_allows_three_targeted_revision_passes(monkeypatch):
    observed = {}

    def fake_run(command, **kwargs):
        observed.update(kwargs["env"])
        return _RUNNER.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("ANI_MENU_DUPLICATE_BASELINE_PATH", "baseline.sqlite3")
    monkeypatch.setattr(_RUNNER.subprocess, "run", fake_run)

    _RUNNER.run_cli("status")

    assert observed["ANI_MENU_MAX_REVISION"] == "4"


def test_review_can_reclassify_a_source_to_an_explicit_supported_type():
    assert _RUNNER.suggested_content_type_from_review(
        {
            "qa_report": [
                {
                    "code": "CONTENT_TYPE_MISMATCH",
                    "message": "허용 유형 ‘goods(굿즈)’에 맞추세요.",
                }
            ]
        }
    ) == "goods"
    assert _RUNNER.suggested_content_type_from_review(
        {
            "qa_report": [
                {"code": "CONTENT_TYPE_MISMATCH", "message": "추천은 unknown 입니다."}
            ]
        }
    ) is None
