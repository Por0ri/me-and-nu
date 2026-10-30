"""Bounded, resumable generation -> local test import, without publication approval.

The existing agent runs unchanged in one subprocess per candidate. There is no
daemon or schedule: a batch stops at its target, attempt, estimated-cost or input
limit. Ordinary publication still requires its independent operator review.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_reviewed_carousel as generator
from app.seeds import carousel_contents as importer
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

TYPES = {"news", "event", "guide", "people", "goods", "behind", "review", "humor", "character", "curation", "music"}
ATTEMPT_RESERVE_USD = 0.04  # Existing producer policy; an estimate, not a billing guarantee.


class BatchError(ValueError):
    """Only fixed, credential-free operator messages are used here."""


def read_json(path: Path) -> dict | list:
    if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise BatchError("Input must be a bounded regular JSON file.")
    value = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(value, (dict, list)):
        raise BatchError("Expected a JSON object or list.")
    return value


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or any(ord(c) < 32 for c in value)):
        raise BatchError("Candidates need HTTPS URLs without credentials or fragments.")
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path or "/", urlencode(sorted(query)), ""))


def load_candidates(path: Path) -> list[dict]:
    document = read_json(path)
    items = document if isinstance(document, list) else document.get("items", document.get("candidates", []))
    excluded = set() if isinstance(document, list) else {
        canonical_url(url) for url in document.get("excludeUrls", document.get("excludedSourceUrls", []))}
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise BatchError("Supply between 1 and 200 explicit candidate records.")
    result, seen = [], set()
    for raw in items:
        if not isinstance(raw, dict):
            raise BatchError("Invalid candidate record.")
        source, url, kind = raw.get("source"), raw.get("url"), raw.get("content_type")
        if (not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source)
                or not isinstance(url, str) or kind not in TYPES):
            raise BatchError("Each candidate needs source, url and content_type.")
        canonical = canonical_url(url)
        if canonical in seen or canonical in excluded:
            continue
        seen.add(canonical)
        result.append({"source": source, "url": url, "content_type": kind,
                       "canonical_url": canonical,
                       "id": hashlib.sha256(canonical.encode()).hexdigest()[:24]})
    if not result:
        raise BatchError("No unique non-excluded candidates remain.")
    return result


@contextmanager
def batch_lock(root: Path):
    """OS locks release even after a crash; no unsafe stale-PID lock deletion."""
    with (root / "batch.lock").open("a+b") as stream:
        try:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BatchError("Another worker owns this batch output directory.") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


async def preflight(database_url: str, expected_database: str) -> set[str]:
    importer.validate_target(database_url, expected_database, local_test_preview=True)
    engine = create_async_engine(database_url, echo=False)
    try:
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            actual = await connection.scalar(text("SELECT current_database()"))
            if actual != expected_database:
                raise BatchError("Connected database does not match the explicit test target.")
            if not await connection.scalar(text("SELECT count(*) FROM topic WHERE topic_code='anime' AND is_active")):
                raise BatchError("The local test database needs an active anime topic.")
            urls = (await connection.execute(text("SELECT source_url FROM content_source"))).scalars().all()
            return {canonical_url(url) for url in urls if isinstance(url, str) and url.startswith("https://")}
    finally:
        await engine.dispose()


def stage_result(result_path: Path, destination: Path):
    """Preserve original producer results. Structural validation is not QC approval."""
    result = read_json(result_path)
    if not isinstance(result, dict) or not isinstance(result.get("package"), dict):
        raise BatchError("Agent output has no complete package.")
    package = result["package"]
    request_id = package.get("request_id", "")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,300}", request_id):
        raise BatchError("Invalid producer request identity.")
    if package.get("isTestData") or result.get("review_fixture") is not None or result.get("review_mode") == "fixture":
        raise BatchError("Synthetic review fixtures are not real generated batch output.")
    result_hash = hashlib.sha256(result_path.read_bytes()).hexdigest()
    if destination.exists():
        prepared = importer.load_bundle(destination / "bundle.json")
        if len(prepared.items) != 1 or (prepared.items[0].raw.reviewEvidence or {}).get("resultSha256") != result_hash:
            raise BatchError("Existing staged bundle differs from the original producer output.")
        return prepared
    cards_root = result_path.parent / "cards" / request_id
    assets, binaries = [], {}
    for card in package.get("cards", []):
        page = card.get("page")
        if type(page) is not int or not 1 <= page <= 10 or page in binaries:
            raise BatchError("Invalid producer card page.")
        image = cards_root / f"card_{page:02d}.png"
        if (image.is_symlink() or not image.is_file() or image.stat().st_size > importer.MAX_PNG_BYTES
                or not image.resolve().is_relative_to(result_path.parent.resolve())):
            raise BatchError("Producer PNG files are missing or unsafe.")
        binary = image.read_bytes()
        digest = hashlib.sha256(binary).hexdigest()
        if card.get("rendered_image_key") != digest:
            raise BatchError("Producer PNG hash does not match the generated card.")
        assets.append({"page": page, "path": f"cards/card_{page:02d}.png", "sha256": digest})
        binaries[page] = binary
    evidence = {key: result.get(key) for key in (
        "status", "review_mode", "editorial_decision", "quality_report", "qa_report", "language_review",
        "usage_records", "cost_accounting", "stage_timings", "operator_correction") if key in result}
    evidence.update({"resultSha256": result_hash, "batchPurpose": "local_test_preview",
                     "operatorReviewCreated": False})
    document = {"schemaVersion": 1, "producer": "ani_menu_agent", "topicCode": "anime",
                "items": [{"package": package, "assets": assets, "reviewEvidence": evidence}]}
    with TemporaryDirectory(prefix="staging-", dir=destination.parent) as temporary:
        temporary_root = Path(temporary)
        (temporary_root / "cards").mkdir()
        for page, binary in binaries.items():
            (temporary_root / "cards" / f"card_{page:02d}.png").write_bytes(binary)
        write_json(temporary_root / "bundle.json", document)
        importer.load_bundle(temporary_root / "bundle.json")
        temporary_root.rename(destination)
    return importer.load_bundle(destination / "bundle.json")


def usage_cost(result: dict) -> tuple[float, bool]:
    summary = result.get("usage_summary")
    if isinstance(summary, dict):
        total = summary.get("estimated_cost_usd")
        if type(total) in {int, float} and math.isfinite(total) and total >= 0:
            return round(total, 8), True
    records = result.get("usage_records") or []
    if not isinstance(records, list) or not records:
        return 0.0, False
    cost, complete = 0.0, True
    for record in records:
        value = record.get("estimated_cost_usd") if isinstance(record, dict) else None
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            complete = False
        else:
            cost += value
    return round(cost, 8), complete


def budget_used(checkpoint: dict) -> float:
    return sum(max(record.get("knownEstimatedCostUsd", 0), ATTEMPT_RESERVE_USD)
               if not record.get("costComplete") else record.get("knownEstimatedCostUsd", 0)
               for record in checkpoint["attempts"].values() if record.get("paidAttemptStarted"))


def source_hash(agent_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((agent_root / "src" / "ani_menu_agent").rglob("*.py")):
        digest.update(path.relative_to(agent_root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def sync_await(coroutine):
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)


def run_batch(*, agent_root: Path, agent_python: Path, credentials_file: Path,
              candidates_file: Path, output: Path, database_url: str, expected_database: str,
              target: int = 5, max_attempts: int = 10, max_estimated_cost_usd: float = 1,
              timeout_seconds: int = 1200, max_images: int = 6, apply: bool = False) -> dict:
    if not (1 <= target <= 50 and target <= max_attempts <= 200 and 1 <= timeout_seconds <= 3600
            and 1 <= max_images <= 20 and math.isfinite(max_estimated_cost_usd) and max_estimated_cost_usd > 0):
        raise BatchError("Invalid target, attempt, time, image or estimated-cost limits.")
    # Do this before credential reads, directory creation or any paid subprocess.
    importer.validate_target(database_url, expected_database, local_test_preview=True)
    candidates = load_candidates(candidates_file)
    agent_root, agent_python = agent_root.resolve(strict=True), agent_python.resolve(strict=True)
    if not (agent_root / "src" / "ani_menu_agent" / "cli.py").is_file() or (agent_root / ".env").exists():
        raise BatchError("Select an unchanged agent source copy without an implicit .env file.")
    output = output.absolute()
    if output.is_symlink() or output.resolve() != output or output.is_relative_to(agent_root):
        raise BatchError("Batch output must be an unlinked directory outside the agent checkout.")
    used_urls = sync_await(preflight(database_url, expected_database))
    if not apply:
        return {"mode": "plan", "target": target, "candidates": len(candidates),
                "existingSourceUrlsSkipped": sum(item["canonical_url"] in used_urls for item in candidates),
                "maxAttempts": max_attempts, "estimatedCostLimitUsd": max_estimated_cost_usd,
                "paidCallsMade": False, "databaseChanged": False}
    generator.credential(credentials_file, {})  # Explicit agent credentials only; never backend fallback.
    output.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output / "checkpoint.json"
    parsed_db = make_url(database_url)
    configuration = {"candidates": candidates, "target": target, "maxAttempts": max_attempts,
                     "maxEstimatedCostUsd": max_estimated_cost_usd, "timeoutSeconds": timeout_seconds,
                     "maxImages": max_images, "agentRoot": str(agent_root), "agentPython": str(agent_python),
                     "agentSourceSha256": source_hash(agent_root),
                     "database": expected_database, "databaseHost": parsed_db.host, "databasePort": parsed_db.port}
    with batch_lock(output):
        if checkpoint_path.exists():
            checkpoint = read_json(checkpoint_path)
            if checkpoint.get("schemaVersion") != 1 or checkpoint.get("configuration") != configuration:
                raise BatchError("Existing batch configuration differs; inspect it before starting another batch.")
        else:
            checkpoint = {"schemaVersion": 1, "configuration": configuration, "attempts": {}, "createdAt": datetime.now(UTC).isoformat()}
            write_json(checkpoint_path, checkpoint)
        for candidate in candidates:
            records = checkpoint["attempts"]
            completed = [record for record in records.values() if record.get("state") == "imported"]
            if len(completed) >= target:
                break
            identity = candidate["id"]
            record = records.get(identity)
            item_root = output / identity
            result_path = item_root / "generation" / "result.json"
            if record and record.get("state") in {"imported", "skipped_existing", "incomplete", "invalid_output", "failed"}:
                continue
            if record is None:
                if candidate["canonical_url"] in used_urls:
                    records[identity] = {"state": "skipped_existing", "sourceUrl": candidate["url"], "paidAttemptStarted": False}
                    write_json(checkpoint_path, checkpoint)
                    continue
                attempts_started = sum(bool(item.get("paidAttemptStarted")) for item in records.values())
                if attempts_started >= max_attempts or budget_used(checkpoint) + ATTEMPT_RESERVE_USD > max_estimated_cost_usd:
                    break
                if item_root.exists():
                    raise BatchError("Untracked candidate output exists; inspect it before any paid run.")
                item_root.mkdir()
                record = records[identity] = {"state": "generating", "sourceUrl": candidate["url"], "paidAttemptStarted": True,
                                               "startedAt": datetime.now(UTC).isoformat(), "knownEstimatedCostUsd": 0, "costComplete": False}
                write_json(checkpoint_path, checkpoint)
                print(json.dumps({"phase": "generating", "candidate": identity, "source": candidate["source"],
                                  "imported": len(completed), "target": target}), flush=True)
                try:
                    generator.run(agent_root=agent_root, agent_python=agent_python, credentials_file=credentials_file,
                                  output=result_path.parent, source=candidate["source"], url=candidate["url"],
                                  content_type=candidate["content_type"], max_images=max_images,
                                  timeout_seconds=timeout_seconds, inherited=dict(os.environ))
                except (generator.GenerationError, OSError):
                    # Some held outputs still include complete real cards. Stage only
                    # what actually exists; never call the paid CLI again on resume.
                    record["generatorStopped"] = True
            if not result_path.is_file():
                record["state"] = "incomplete"
                record["reason"] = "no_result_preserved_no_automatic_paid_retry"
                write_json(checkpoint_path, checkpoint)
                continue
            try:
                result = read_json(result_path)
                record["knownEstimatedCostUsd"], record["costComplete"] = usage_cost(result)
                record["agentStatus"] = result.get("status")
                bundle = stage_result(result_path, item_root / "staged")
            except (BatchError, importer.CarouselImportError, ValueError, OSError, KeyError, TypeError):
                record["state"] = "invalid_output"
                record["reason"] = "incomplete_or_invalid_package_pngs_preserved"
                write_json(checkpoint_path, checkpoint)
                continue
            record["state"] = "importing"
            record["digest"] = bundle.items[0].digest
            write_json(checkpoint_path, checkpoint)
            # Database failures leave 'importing': a later invocation resumes this
            # exact bundle rather than charging for another generation.
            preview = sync_await(importer.run(bundle=bundle, expected_database=expected_database,
                                             apply=False, database_url=database_url, local_test_preview=True))
            imported = sync_await(importer.run(bundle=bundle, expected_database=expected_database,
                                              apply=True, database_url=database_url, local_test_preview=True))
            record.update({"state": "imported", "contentId": imported["items"][0]["content_id"],
                           "slideCount": len(bundle.items[0].images), "importPlan": preview, "importResult": imported,
                           "finishedAt": datetime.now(UTC).isoformat()})
            used_urls.add(candidate["canonical_url"])
            write_json(checkpoint_path, checkpoint)
            print(json.dumps({"phase": "imported", "candidate": identity, "contentId": record["contentId"],
                              "slides": record["slideCount"], "operatorApprovalCreated": False}), flush=True)
        records = list(checkpoint["attempts"].values())
        imported_records = [record for record in records if record["state"] == "imported"]
        summary = {"mode": "local_test_preview", "status": "complete" if len(imported_records) == target else "bounded_stop",
                   "target": target, "imported": len(imported_records), "contentIds": [r["contentId"] for r in imported_records],
                   "slideCount": sum(r["slideCount"] for r in imported_records),
                   "attempts": sum(bool(r.get("paidAttemptStarted")) for r in records),
                   "knownEstimatedCostUsd": round(sum(r.get("knownEstimatedCostUsd", 0) for r in records), 8),
                   "budgetUsedIncludingUnknownReservesUsd": round(budget_used(checkpoint), 8),
                   "billingTotalKnown": False, "operatorApprovalCreated": False,
                   "stoppedItems": [{"sourceUrl": r["sourceUrl"], "state": r["state"], "reason": r.get("reason")}
                                    for r in records if r["state"] not in {"imported", "skipped_existing"}]}
        write_json(output / "summary.json", summary)
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-root", type=Path, required=True)
    parser.add_argument("--agent-python", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database-config", type=Path, help="Private JSON with databaseUrl and optional database; never printed.")
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--target", type=int, default=5)
    parser.add_argument("--max-attempts", type=int, default=10)
    parser.add_argument("--max-estimated-cost-usd", type=float, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--max-images", type=int, default=6)
    parser.add_argument("--apply", action="store_true", help="Enable paid generation and local test writes; omitted means read-only plan.")
    args = vars(parser.parse_args())
    config_path = args.pop("database_config")
    try:
        config = read_json(config_path) if config_path else {}
        if config.get("database") not in {None, args["expected_database"]}:
            raise BatchError("Database configuration name does not match the explicit target.")
        args["database_url"] = config.get("databaseUrl") if config_path else os.environ.get("DATABASE_URL")
        args["candidates_file"] = args.pop("candidates")
        args["output"] = args.pop("output_dir")
        report = run_batch(**args)
    except (BatchError, importer.CarouselImportError) as exc:
        print(f"Batch stopped: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # DB/provider exceptions can include credentials. Artifacts/checkpoints
        # retain progress; never serialize arbitrary exception messages.
        print("Batch stopped; inspect configuration and preserved checkpoint. No paid operation is automatically retried.", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("status") in {None, "complete"} else 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
