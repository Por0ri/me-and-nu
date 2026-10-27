"""Continuously discover and generate unique reviewed content into an isolated SQLite DB."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from ani_menu_agent.duplicate_detection import _canonical_url
from ani_menu_agent.models import CONTENT_TYPE_LABELS
from ani_menu_agent.source_freshness import candidate_is_stale, candidate_publication_date
from ani_menu_agent.sources.registry import load_source_registry

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = os.environ.get("ANI_MENU_GENERATION_RUN_ID", "generation_200")
WORKSPACE = Path(
    os.environ.get("ANI_MENU_GENERATION_WORKSPACE", str(ROOT / "data" / RUN_ID))
).resolve()
DATABASE = Path(
    os.environ.get(
        "ANI_MENU_GENERATION_DATABASE", str(WORKSPACE / f"ani_menu_{RUN_ID}.sqlite3")
    )
).resolve()
OUTPUT = Path(
    os.environ.get("ANI_MENU_GENERATION_OUTPUT", str(ROOT / "output" / RUN_ID))
).resolve()
CHECKPOINT = WORKSPACE / "checkpoint.json"
LOG = OUTPUT / "worker.log"
TARGET = int(os.environ.get("ANI_MENU_GENERATION_TARGET", "200"))
AGENT_REVISION = "2026-10-24-retry-failed-story-with-better-source-v38"
CANDIDATE_TIMEOUT_SECONDS = 15 * 60
REGION_POLICY = os.environ.get("ANI_MENU_REGION_POLICY", "mixed")
HARD_SKIP_STATUSES = {
    "needs_crawler_update",
    "already_used_source_url",
    "duplicate_skipped",
    "stale_candidate_skipped",
    "work_id_unresolved_skipped",
}
REVISION_REPAIR_BLOCKERS = {
    # Failed drafts are not successful duplicates: allow a new source URL to
    # supply stronger evidence/assets for the same topic. The duplicate checker
    # still blocks accepted/feed-ready content, so retrying never weakens QA.
    "2026-10-24-preserve-requested-type-through-db-load-v37": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
        "IMAGE_COMPOSITION_REJECTED",
    },
    # Related-page image titles/source URLs are now preserved through extraction
    # and persistence, allowing event/product assets from other pages to be
    # correctly excluded or matched. Revisit only candidates whose visual pool
    # may change under that provenance fix.
    "2026-10-24-reference-image-download-v24": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
        "IMAGE_COMPOSITION_REJECTED",
    },
    "2026-10-24-image-page-provenance-v25": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
        "IMAGE_COMPOSITION_REJECTED",
    },
    "2026-10-24-nested-facts-and-fresh-run-output-v26": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
    },
    "2026-10-24-kitsu-work-art-fallback-v27": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
    },
    "2026-10-24-resolve-work-before-kitsu-fallback-v28": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
    },
    "2026-10-24-kitsu-cover-and-episode-visuals-v29": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
    },
    "2026-10-24-exact-work-title-boundaries-v30": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
    },
    "2026-10-24-supplement-until-visual-pool-is-eligible-v31": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
    },
    "2026-10-24-prioritize-high-resolution-episode-stills-v32": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
    },
    "2026-10-24-reference-art-context-not-fact-proof-v33": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
    },
    "2026-10-24-bounded-candidate-timeout-v34": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
    },
    "2026-10-24-exclude-generic-work-art-from-event-goods-v35": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
    },
    "2026-10-24-event-goods-source-page-only-v36": {
        "SOURCE_IMAGE_MISSING",
        "COVER_SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
        "IMAGE_VISION_MISMATCH",
    },
    # v20 expanded parsing of explicit type recommendations; retry type
    # mismatches only, not image/source blockers unrelated to that change.
    "2026-10-24-source-led-type-review-parse-v20": {"CONTENT_TYPE_MISMATCH"},
    # Generic same-work official art is now eligible on detail pages after
    # conservative classification; retry only image-pool blockers.
    "2026-10-24-no-placeholder-card-art-v22": {
        "SOURCE_IMAGE_MISSING",
        "SOURCE_IMAGE_POOL_INSUFFICIENT",
    },
    # Reference-work images copied from the read-only baseline must be fetched
    # into the isolated generation DB before card rendering.
    "2026-10-24-source-art-detail-eligibility-v23": {"SOURCE_IMAGE_MISSING"},
}


def log(message: str) -> None:
    line = f"{datetime.now(UTC).isoformat()} {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ANI_MENU_DATABASE_BACKEND"] = "sqlite"
    env["ANI_MENU_DATABASE_PATH"] = str(DATABASE)
    env["ANI_MENU_DUPLICATE_BASELINE_PATH"] = str(
        Path(env["ANI_MENU_DUPLICATE_BASELINE_PATH"]).resolve()
    )
    # max_revision counts reviewer failures including the initial review; 4
    # therefore permits three focused correction passes without lowering gates.
    env["ANI_MENU_MAX_REVISION"] = "4"
    command = [sys.executable, "-m", "ani_menu_agent.cli", *args]
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            # A hung source, image CDN, or model call must not pin the whole
            # unattended batch for an hour; timeouts are recorded as candidate
            # failures and the worker continues without relaxing QA gates.
            timeout=CANDIDATE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode("utf-8", errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        return subprocess.CompletedProcess(
            command, 124, stdout, stderr + "\nworker command timed out"
        )


def passing_count() -> int:
    import sqlite3

    if not DATABASE.is_file():
        return 0
    with sqlite3.connect(DATABASE) as connection:
        try:
            return int(
                connection.execute(
                    "SELECT COUNT(DISTINCT request_id) FROM feed_queue WHERE status='ready'"
                ).fetchone()[0]
            )
        except sqlite3.OperationalError:
            return 0


def completed_review_count() -> int:
    """Count unique final pass/reject decisions for a bounded visual trial."""
    outcomes_path = OUTPUT / "outcomes.jsonl"
    if not outcomes_path.is_file():
        return 0
    completed: set[str] = set()
    for line in outcomes_path.read_text(encoding="utf-8").splitlines():
        try:
            outcome = json.loads(line)
        except json.JSONDecodeError:
            continue
        if outcome.get("verdict") in {"pass", "reject"}:
            completed.add(str(outcome.get("request_id") or outcome.get("candidate_id")))
    return len(completed)


def target_count() -> int:
    """By default target passed items; experiments may target reviewed outputs."""
    if os.environ.get("ANI_MENU_TARGET_MODE", "passed").casefold() == "reviewed":
        return completed_review_count()
    return passing_count()


def load_checkpoint() -> dict:
    if not CHECKPOINT.is_file():
        return {"attempts": {}, "reviewed_milestones": 0}
    try:
        return json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"attempts": {}, "reviewed_milestones": 0}


def save_checkpoint(checkpoint: dict) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT.with_suffix(".tmp")
    temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CHECKPOINT)


def used_source_urls(database_paths: list[Path]) -> set[str]:
    """Collect URLs from accepted content so reworkable candidates can be retried."""
    import sqlite3

    urls: set[str] = set()
    for path in database_paths:
        if not path.is_file():
            continue
        try:
            connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                for table, statuses in (
                    ("content_drafts", ("ready_for_feed", "ready", "passed", "accepted")),
                    ("feed_queue", ("ready", "passed", "accepted")),
                ):
                    if table not in tables:
                        continue
                    for (raw_package,) in connection.execute(
                        f"SELECT package_json FROM {table} WHERE status IN "
                        f"({','.join('?' for _ in statuses)})",
                        statuses,
                    ):
                        try:
                            package = json.loads(raw_package or "{}")
                        except json.JSONDecodeError:
                            continue
                        for source in package.get("sources", []):
                            url = _canonical_url(str(source.get("url") or ""))
                            if url:
                                urls.add(url)
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise RuntimeError(f"Could not scan duplicate-source history in {path}") from exc
    return urls


def candidate_url_skip_reason(
    candidate_url: str,
    accepted_urls: set[str],
    attempted_urls_this_cycle: set[str],
) -> str | None:
    """Only accepted stories are permanent URL duplicates; failed ones may be retried."""
    if not candidate_url:
        return None
    if candidate_url in accepted_urls:
        return "already_used_source_url"
    if candidate_url in attempted_urls_this_cycle:
        return "duplicate_candidate_url_in_cycle"
    return None


def export_review_batch(batch_number: int) -> None:
    import sqlite3

    review_dir = OUTPUT / "review_batches"
    review_dir.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(DATABASE)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute(
            "SELECT request_id, topic, content_type, package_json, quality_score, "
            "quality_report_json, created_at FROM content_drafts "
            "WHERE status='ready_for_feed' ORDER BY updated_at DESC LIMIT 10"
        ).fetchall()
        items = []
        for row in reversed(rows):
            package = json.loads(row["package_json"])
            quality = json.loads(row["quality_report_json"] or "{}")
            items.append(
                {
                    "request_id": row["request_id"],
                    "topic": row["topic"],
                    "content_type": row["content_type"],
                    "quality_score": row["quality_score"],
                    "quality_report": quality,
                    "package": package,
                }
            )
        path = review_dir / f"batch_{batch_number:02d}.json"
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"review checkpoint {batch_number}: {len(items)} passing packages -> {path}")
    finally:
        database.close()


def organize_candidate_artifacts(
    candidate_id: str,
    content_type: str,
    attempt_number: int,
    result_path: Path,
    payload: dict,
) -> dict:
    """Separate final pass/rejection PNGs and save an inspectable per-attempt decision log."""
    status = str(payload.get("status") or "unknown")
    if status in {"ready_for_feed", "approved_for_feed", "passed"}:
        bucket = "passed"
        verdict = "pass"
    elif status in {"rejected_archived", "rejected", "failed_review", "review_returned"}:
        bucket = "rejected"
        verdict = "reject"
    elif status in {"needs_crawler_update", "source_insufficient"}:
        bucket = "blocked"
        verdict = "not_reviewed"
    else:
        bucket = "skipped"
        verdict = "not_generated"

    package = payload.get("package") or {}
    request_id = str(payload.get("request_id") or package.get("request_id") or candidate_id)
    folder_name = f"{content_type}-{candidate_id}-attempt-{attempt_number:02d}"
    destination = OUTPUT / bucket / folder_name
    destination.mkdir(parents=True, exist_ok=True)

    stored_result = None
    if result_path.is_file():
        stored_result = destination / "result.json"
        if result_path.resolve() != stored_result.resolve():
            shutil.move(str(result_path), str(stored_result))

    source_cards = result_path.parent / "cards" / request_id
    stored_cards = None
    if source_cards.is_dir():
        stored_cards = destination / "cards"
        suffix = 2
        while stored_cards.exists():
            stored_cards = destination / f"cards_{suffix:02d}"
            suffix += 1
        shutil.move(str(source_cards), str(stored_cards))

    quality = payload.get("quality_report") or {}
    qa_report = payload.get("qa_report") or []
    outcome = {
        "candidate_id": candidate_id,
        "request_id": request_id,
        "content_type": content_type,
        "status": status,
        "verdict": verdict,
        "quality_score": quality.get("total"),
        "quality_decision": quality.get("decision"),
        "hard_blockers": quality.get("hard_blockers", []),
        "qa_report": qa_report,
        "revision_count": payload.get("revision_count"),
        "review_failures": payload.get("review_failures"),
        "trace": payload.get("trace", []),
        "result_json": str(stored_result) if stored_result else None,
        "card_png_dir": str(stored_cards) if stored_cards else None,
        "card_png_count": len(list(stored_cards.glob("*.png"))) if stored_cards else 0,
    }
    (destination / "outcome.json").write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (OUTPUT / "outcomes.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(outcome, ensure_ascii=False) + "\n")
    reasons = [str(issue.get("code", "")) for issue in qa_report if issue.get("code")]
    log(
        f"result routed verdict={verdict} status={status} type={content_type} "
        f"request_id={request_id} score={outcome['quality_score']} "
        f"blockers={outcome['hard_blockers']} issues={reasons} "
        f"png_count={outcome['card_png_count']} folder={destination}"
    )
    return outcome


def candidate_source_is_eligible(candidate: dict, type_code: str, registry) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(str(candidate.get("url", ""))).hostname or "").lower().rstrip(".")
    # Discovery may be invoked for one routing type and persist only that type
    # on the candidate (e.g. a PR Times goods release discovered via the news
    # route). The registry is authoritative for what a host supports, so do
    # not treat the route-specific candidate hint as an allow-list.
    return any(
        source.status == "active"
        and source.role in {"primary", "editorial", "reference"}
        and source.collection in {"html", "api"}
        and (urlparse(str(source.url)).hostname or "").lower().rstrip(".") == host
        and type_code in {item.value for item in source.content_types}
        for source in registry.sources
    )


def candidate_cooldown_seconds(record: dict) -> int:
    """Avoid repeatedly processing unchanged skips while allowing transient retries."""
    hard_skip = record.get("result_status") in HARD_SKIP_STATUSES
    too_many_failures = int(record.get("attempts", 0)) >= 3
    if hard_skip or too_many_failures:
        return 30 * 24 * 60 * 60
    if "exit_code" in record:
        return 300
    return 7 * 24 * 60 * 60


def reopen_candidates_after_agent_revision(checkpoint: dict) -> int:
    """Retry recoverable failures once after a concrete pipeline improvement."""
    if checkpoint.get("agent_revision") == AGENT_REVISION:
        return 0
    reopened = 0
    previous_revision = checkpoint.get("agent_revision")
    revision_repair_blockers = REVISION_REPAIR_BLOCKERS.get(previous_revision, set())
    for record in checkpoint.setdefault("attempts", {}).values():
        status = record.get("result_status")
        blockers = set(record.get("blocker_codes", []))
        recoverable = (
            (
                status in {"rejected_archived", "needs_crawler_update"}
                and blockers & revision_repair_blockers
            )
            # Older checkpoints stored duplicate_skipped as terminal even when
            # the duplicate was a failed draft. Revisit once with the corrected
            # retry rule; ready/accepted duplicates remain blocked by storage.
            or status == "duplicate_skipped"
            or (
                status not in HARD_SKIP_STATUSES
                and int(record.get("exit_code", 0)) != 0
            )
        )
        if not recoverable:
            continue
        record["attempts_before_revision"] = int(record.get("attempts", 0))
        record["attempts"] = 0
        record["last_attempt"] = 0
        for key in ("result_status", "blocker_codes", "exit_code"):
            record.pop(key, None)
        record["reopened_by_agent_revision"] = AGENT_REVISION
        reopened += 1
    checkpoint["agent_revision"] = AGENT_REVISION
    return reopened


def select_candidate_type(candidate: dict, type_codes: list[str], registry) -> str | None:
    # Classify the article title, not its URL: a `/news/` path is a section
    # name, not evidence that the linked page is a news article.
    title = str(candidate.get("title", "")).casefold()
    priority_patterns = (
        (
            "event",
            r"pop[ -]?up|exhibition|collab caf[eé]|cafe|jump\s*festa|"
            r"jump\s*(?:super\s*)?stage|ジャンプフェスタ|ジャンプスーパーステージ|"
            r"スーパーステージ|anime\s*expo|comic[-\s]?market|"
            r"フェア|開催|展覧|展示|催事|公演|舞台|イベント",
        ),
        (
            "goods",
            r"goods|merch|figure|product|グッズ|フィギュア|くじ|ぬい|アクスタ|発売|予約受付|販売|商品",
        ),
        (
            "music",
            r"music|soundtrack|\bost\b|theme song|song|album|single|"
            r"主題歌|サントラ|楽曲|アルバム|シングル|新曲|音楽|サウンドトラック|配信リリース",
        ),
        (
            "people",
            r"interview|voice actor|cast|staff|声優|キャスト|出演者|スタッフ|対談|インタビュー",
        ),
        ("character", r"character profile|birthday|誕生日|キャラクター紹介|人物紹介|キャラクター"),
        ("behind", r"making|production|制作秘話|舞台裏|メイキング|収録レポート"),
        ("review", r"review|レビュー|批評|感想"),
        ("curation", r"ranking|ratings?|ランキング|投票|おすすめ|top anime"),
        ("guide", r"episode|story|season|guide|arc|各話|あらすじ|ストーリー|シーズン|編"),
        ("news", r"announce|announcement|announced|発表|解禁|決定|ニュース|news"),
    )
    eligible = [
        code for code in type_codes if candidate_source_is_eligible(candidate, code, registry)
    ]
    for code, pattern in priority_patterns:
        if code in eligible and re.search(pattern, title):
            return code
    if "news" in eligible and any(
        marker in title
        for marker in ("\u65b0\u4f5c", "\u4e8b\u524d\u767b\u9332", "\u914d\u4fe1\u958b\u59cb")
    ):
        return "news"
    hints = set(candidate.get("content_types", []))
    non_news_hints = [code for code in eligible if code in hints and code != "news"]
    if non_news_hints:
        return non_news_hints[0]
    if "news" in eligible and (
        candidate.get("published_at") or candidate_publication_date(candidate)
    ):
        return "news"
    return None


def suggested_content_type_from_review(payload: dict) -> str | None:
    """Read only an explicit, taxonomy-valid type recommendation from QA feedback."""
    allowed = {item.value for item in CONTENT_TYPE_LABELS}
    recommendation_patterns = (
        r"(?:허용\s*유형|적합\s*유형|권장\s*유형)\s*[‘'\"`]?([a-z_]+)",
        r"(?:검증된\s*)?자료\s*유형은?\s*[‘'\"`]?([a-z_]+)",
    )
    for issue in payload.get("qa_report", []):
        if issue.get("code") != "CONTENT_TYPE_MISMATCH":
            continue
        message = str(issue.get("message") or "")
        for pattern in recommendation_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match and match.group(1).lower() in allowed:
                return match.group(1).lower()
    return None


def reopen_candidate_if_source_became_eligible(
    candidate: dict,
    type_codes: list[str],
    record: dict,
    registry,
) -> bool:
    """Clear cooldown when a previously unavailable host is now active for its type."""
    if record.get("reason") != "no_active_registered_source_for_candidate_host_and_type":
        return False
    if not select_candidate_type(candidate, type_codes, registry):
        return False
    record["attempts"] = 0
    record["last_attempt"] = 0
    record.pop("reason", None)
    return True


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    baseline = os.environ.get("ANI_MENU_DUPLICATE_BASELINE_PATH")
    if not baseline:
        raise RuntimeError("기존 SQLite 중복 기준선 경로가 설정되지 않았습니다.")
    baseline_path = Path(baseline).resolve()
    if not baseline_path.is_file():
        raise RuntimeError(f"기존 SQLite 중복 기준선을 찾을 수 없습니다: {baseline_path}")
    checkpoint = load_checkpoint()
    reopened_candidates = reopen_candidates_after_agent_revision(checkpoint)
    if reopened_candidates:
        log(
            f"agent revision {AGENT_REVISION} reopened "
            f"{reopened_candidates} recoverable candidates"
        )
        save_checkpoint(checkpoint)
    elif checkpoint.get("agent_revision") == AGENT_REVISION:
        pass
    else:
        save_checkpoint(checkpoint)
    registry = load_source_registry()
    baseline = Path(baseline).resolve()
    extra_baselines = [
        Path(item).resolve()
        for item in os.environ.get("ANI_MENU_PRIOR_RUN_DATABASES", "").split(os.pathsep)
        if item.strip()
    ]
    known_source_urls = used_source_urls([DATABASE, baseline, *extra_baselines])
    type_codes = [code.value for code in CONTENT_TYPE_LABELS]
    seen_at_start = passing_count()
    log(
        f"worker started target={TARGET} passed={seen_at_start} db={DATABASE} "
        f"baseline_read_only={baseline_path} content_types={len(type_codes)} "
        f"review_profile={os.environ.get('ANI_MENU_REVIEW_PROFILE', 'standard')} "
        f"target_mode={os.environ.get('ANI_MENU_TARGET_MODE', 'passed')}"
    )
    cycle = 0
    while target_count() < TARGET:
        cycle += 1
        discovery_count = 0
        attempted_urls_this_cycle: set[str] = set()
        for type_code in type_codes:
            snapshot = OUTPUT / "discovery" / f"latest_{type_code}.json"
            result = run_cli(
                "discover",
                "--limit",
                "50",
                "--region-policy",
                REGION_POLICY,
                "--content-type",
                type_code,
                "--output",
                str(snapshot.relative_to(ROOT)),
            )
            if result.returncode:
                log(
                    f"discover failed type={type_code}: "
                    f"{result.stderr[-1000:]} {result.stdout[-400:]}"
                )
                continue
            try:
                payload = json.loads(snapshot.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                log(f"discover output invalid type={type_code}: {exc}")
                continue
            discovery_count += len(payload.get("items", []))
            for candidate in payload.get("items", []):
                candidate_id = str(candidate.get("id") or "")
                if not candidate_id:
                    continue
                attempts = checkpoint.setdefault("attempts", {})
                record = attempts.get(candidate_id, {})
                candidate_url = _canonical_url(str(candidate.get("url") or ""))
                skip_reason = candidate_url_skip_reason(
                    candidate_url, known_source_urls, attempted_urls_this_cycle
                )
                if skip_reason == "already_used_source_url":
                    attempts[candidate_id] = {
                        "last_attempt": time.time(),
                        "attempts": int(record.get("attempts", 0)) + 1,
                        "result_status": "already_used_source_url",
                    }
                    save_checkpoint(checkpoint)
                    log(f"candidate skipped as previously used source URL: {candidate_id}")
                    continue
                if skip_reason == "duplicate_candidate_url_in_cycle":
                    log(f"candidate skipped as duplicate URL within cycle: {candidate_id}")
                    continue
                selected_type = select_candidate_type(candidate, type_codes, registry)
                if reopen_candidate_if_source_became_eligible(
                    candidate, type_codes, record, registry
                ):
                    save_checkpoint(checkpoint)
                    log(f"candidate reopened after source activation: {candidate_id}")
                last_attempt = float(record.get("last_attempt", 0))
                cooldown = candidate_cooldown_seconds(record)
                if time.time() - last_attempt < cooldown:
                    continue
                if not selected_type:
                    attempts[candidate_id] = {
                        "last_attempt": time.time(),
                        "attempts": int(record.get("attempts", 0)) + 1,
                        "reason": "no_active_registered_source_for_candidate_host_and_type",
                    }
                    save_checkpoint(checkpoint)
                    continue
                if candidate_is_stale(candidate, selected_type):
                    attempts[candidate_id] = {
                        "last_attempt": time.time(),
                        "attempts": int(record.get("attempts", 0)) + 1,
                        "type": selected_type,
                        "result_status": "stale_candidate_skipped",
                    }
                    save_checkpoint(checkpoint)
                    log(
                        f"candidate skipped as stale id={candidate_id} type={selected_type} "
                        f"published={candidate_publication_date(candidate)}"
                    )
                    continue
                count_before = passing_count()
                attempt_number = int(record.get("attempts", 0)) + 1
                output_file = (
                    OUTPUT
                    / "runs"
                    / f"{candidate_id}-attempt-{attempt_number:02d}.json"
                )
                command_args = [
                    "crawl-generate",
                    "--candidate-id",
                    candidate_id,
                    "--content-type",
                    selected_type,
                    "--max-images",
                    "20",
                    "--output",
                    str(output_file.relative_to(ROOT)),
                ]
                # An article may already have a rejected draft in the baseline
                # DB; permit the agent to rework that failed source while the
                # duplicate checker still blocks any accepted/queued story.
                command_args.append("--retry-failed")
                try:
                    generated = run_cli(*command_args)
                except Exception as exc:  # one candidate must never stop the batch worker
                    generated = subprocess.CompletedProcess(
                        command_args,
                        125,
                        "",
                        f"worker caught candidate exception: {exc!r}",
                    )
                if generated.returncode == 0 and output_file.is_file():
                    try:
                        first_payload = json.loads(output_file.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        first_payload = {}
                    corrected_type = suggested_content_type_from_review(first_payload)
                    if (
                        corrected_type
                        and corrected_type != selected_type
                        and candidate_source_is_eligible(candidate, corrected_type, registry)
                    ):
                        selected_type = corrected_type
                        command_args[
                            command_args.index("--content-type") + 1
                        ] = corrected_type
                        corrected_output = output_file.with_name(
                            f"{candidate_id}-attempt-{attempt_number:02d}-"
                            f"type-{corrected_type}.json"
                        )
                        command_args[command_args.index("--output") + 1] = str(
                            corrected_output.relative_to(ROOT)
                        )
                        output_file = corrected_output
                        log(
                            f"candidate type correction id={candidate_id} "
                            f"review={corrected_type}; retrying same source"
                        )
                        try:
                            generated = run_cli(*command_args)
                        except Exception as exc:  # preserve checkpoint even after retry failures
                            generated = subprocess.CompletedProcess(
                                command_args,
                                125,
                                "",
                                f"worker caught type-correction retry exception: {exc!r}",
                            )
                count_after = passing_count()
                result_status = None
                blocker_codes: list[str] = []
                generated_payload: dict = {}
                if output_file.is_file():
                    try:
                        generated_payload = json.loads(output_file.read_text(encoding="utf-8"))
                        result_status = generated_payload.get("status")
                        blocker_codes = [
                            str(issue.get("code"))
                            for issue in generated_payload.get("qa_report", [])
                            if issue.get("code")
                        ]
                    except (OSError, json.JSONDecodeError):
                        pass
                if generated_payload:
                    organize_candidate_artifacts(
                        candidate_id,
                        selected_type,
                        attempt_number,
                        output_file,
                        generated_payload,
                    )
                elif generated.returncode:
                    error_dir = OUTPUT / "errors" / (
                        f"{selected_type}-{candidate_id}-attempt-{attempt_number:02d}"
                    )
                    error_dir.mkdir(parents=True, exist_ok=True)
                    error_record = {
                        "candidate_id": candidate_id,
                        "content_type": selected_type,
                        "exit_code": generated.returncode,
                        "stderr": generated.stderr[-6000:],
                        "stdout": generated.stdout[-3000:],
                        "status": "execution_error",
                    }
                    (error_dir / "error.json").write_text(
                        json.dumps(error_record, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    log(
                        f"execution error recorded id={candidate_id} type={selected_type} "
                        f"exit={generated.returncode} folder={error_dir}"
                    )
                attempts[candidate_id] = {
                    "last_attempt": time.time(),
                    "attempts": int(record.get("attempts", 0)) + 1,
                    "type": selected_type,
                    "exit_code": generated.returncode,
                    "result_status": result_status,
                    "blocker_codes": blocker_codes,
                }
                if candidate_url:
                    attempted_urls_this_cycle.add(candidate_url)
                save_checkpoint(checkpoint)
                if generated.returncode:
                    log(
                        f"candidate not passed id={candidate_id} type={selected_type} "
                        f"db_passed={count_after}: "
                        f"{generated.stderr[-900:]} {generated.stdout[-500:]}"
                    )
                else:
                    log(
                        f"candidate processed id={candidate_id} type={selected_type} "
                        f"status={result_status or 'unknown'} blockers={blocker_codes} "
                        f"passed={count_after}/{TARGET}"
                    )
                milestone = count_after // 10
                if milestone > int(checkpoint.get("reviewed_milestones", 0)):
                    export_review_batch(milestone)
                    checkpoint["reviewed_milestones"] = milestone
                    save_checkpoint(checkpoint)
                if target_count() >= TARGET:
                    break
                if count_after == count_before and generated.returncode == 0:
                    log(f"candidate completed without a new pass: {candidate_id}")
        current = target_count()
        current_label = (
            "reviewed" if os.environ.get("ANI_MENU_TARGET_MODE", "passed").casefold() == "reviewed"
            else "passed"
        )
        log(
            f"cycle={cycle} discovery_items={discovery_count} {current_label}={current}/{TARGET}; "
            "continuing discovery"
        )
        if current < TARGET:
            time.sleep(300 if discovery_count else 900)

    label = (
        "reviewed" if os.environ.get("ANI_MENU_TARGET_MODE", "passed").casefold() == "reviewed"
        else "passed"
    )
    log(f"target reached: {target_count()}/{TARGET} {label}; worker exiting")


if __name__ == "__main__":
    main()
