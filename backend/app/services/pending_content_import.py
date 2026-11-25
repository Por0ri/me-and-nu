"""Consume immutable export deliveries into operator-review waiting content.

An external reviewed daily scope authorizes future deliveries, not publication.
This backend-only boundary never connects to a producer ledger/broker or calls
an agent/LLM. The inbox stays read-only; durable receipts use existing AgentRun
JSON, so retries, process restarts and approved/rejected replays need no migration.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AgentRun, Content, ContentEntityLink, ContentSource, Draft, Entity, EntityExternalId, Topic
from app.seeds import carousel_contents as carousel

POLICY_FIELDS = {"schema_version", "purpose", "run_id", "policy_date", "source_id", "content_type",
                 "max_items", "bundle_root", "day_policy_sha256", "target"}
POLICY_FIELDS_V2 = (POLICY_FIELDS - {"source_id", "content_type"}) | {"routes"}
ROUTE_FIELDS = {"source_id", "content_type", "menu_work_id"}
TARGET_FIELDS = {"host", "port", "database", "username", "schema", "environment"}
ENVELOPE_FIELDS = {"schema_version", "job_id", "generation_attempt_id", "result_sha256", "bundle_path",
                   "bundle_sha256", "source_id", "canonical_url", "content_type", "policy_date", "run_id",
                   "day_policy_sha256"}
ENVELOPE_FIELDS_V2 = ENVELOPE_FIELDS | {"menu_work_id"}
RECEIPT = "pending_import_receipt"
MAX_JSON = 16 * 1024 * 1024


class PendingImportError(ValueError):
    """Fixed codes only: never include credentials or raw producer documents."""


def require(condition, code):
    if not condition:
        raise PendingImportError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError):
        return False


def _json(data):
    def pairs(items):
        result = dict(items)
        require(len(result) == len(items), "duplicate_json_key")
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise PendingImportError("invalid_json") from None


def read_file(path, *, limit=MAX_JSON):
    path = Path(path)
    try:
        require(path.is_absolute() and path.resolve(strict=True) == path and path.is_file(), "unsafe_input_file")
        current = path
        while current != current.parent:
            require(not current.is_symlink() and not (hasattr(current, "is_junction") and current.is_junction()),
                    "unsafe_input_file")
            current = current.parent
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
        require(len(data) <= limit, "input_too_large")
        return data
    except OSError:
        raise PendingImportError("input_unavailable") from None


def _route_identity(route):
    require(isinstance(route, dict) and set(route) == ROUTE_FIELDS
            and isinstance(route["source_id"], str) and re.fullmatch(r"[a-z0-9_]{1,100}", route["source_id"])
            and isinstance(route["content_type"], str) and route["content_type"] in {"news", "event", "goods"}
            and type(route["menu_work_id"]) is int and 1 <= route["menu_work_id"] <= 2**63 - 1,
            "invalid_import_route")
    return route["source_id"], route["content_type"], route["menu_work_id"]


def validate_policy(policy, *, reviewed_sha256=None, apply=False, now=None):
    require(isinstance(policy, dict), "invalid_import_policy")
    version = policy.get("schema_version")
    require(type(version) is int and version in {1, 2}
            and set(policy) == (POLICY_FIELDS if version == 1 else POLICY_FIELDS_V2)
            and policy["purpose"] == "pending_content_import", "invalid_import_policy")
    if version == 1:
        require(isinstance(policy["source_id"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", policy["source_id"])
                and policy["content_type"] in {"news", "event", "goods"}, "invalid_import_scope")
    else:
        routes = policy["routes"]
        require(isinstance(routes, list) and 1 <= len(routes) <= 1000, "invalid_import_routes")
        identities = [_route_identity(route) for route in routes]
        require(len({identity[:2] for identity in identities}) == len(identities), "duplicate_import_route")
    require(isinstance(policy["run_id"], str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", policy["run_id"])
            and type(policy["max_items"]) is int and 1 <= policy["max_items"] <= (40 if version == 1 else 20)
            and _hash(policy["day_policy_sha256"]), "invalid_import_scope")
    try:
        date = datetime.strptime(policy["policy_date"], "%Y-%m-%d").date()
        require(date.isoformat() == policy["policy_date"], "invalid_policy_date")
    except (ValueError, TypeError):
        raise PendingImportError("invalid_policy_date") from None
    root = Path(policy["bundle_root"])
    require(root.is_absolute() and root.resolve() == root and root.is_dir(), "unsafe_bundle_root")
    target = policy["target"]
    require(isinstance(target, dict) and set(target) == TARGET_FIELDS, "invalid_import_target")
    require(type(target["port"]) is int and 1 <= target["port"] <= 65535
            and isinstance(target["username"], str) and re.fullmatch(r"[A-Za-z0-9_]{1,63}", target["username"]),
            "invalid_import_target")
    environment = target["environment"]
    if environment == "test":
        require(target["database"] == "carousel_test" and target["host"] in {"127.0.0.1", "localhost"}
                and re.fullmatch(r"carousel_[0-9a-f]{32}", target["schema"]) is not None, "invalid_test_target")
    else:
        # compose.main.yaml deliberately retains the existing Checking database.
        database = {"production": "me_nu_checking", "checking": "me_nu_checking"}.get(environment)
        require(database is not None and target["database"] == target["username"] == database
                and target["host"] == "postgres" and target["port"] == 5432 and target["schema"] == "public",
                "invalid_service_target")
    digest = fingerprint(policy)
    if reviewed_sha256 is not None or apply:
        require(_hash(reviewed_sha256) and reviewed_sha256 == digest, "reviewed_policy_digest_required")
    if apply:
        today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Seoul")).date()
        require(today == date, "policy_date_inactive")
    return digest


def validate_target(database_url, policy, deployment_env):
    try:
        url = make_url(database_url)
        target = policy["target"]
        require(url.drivername in {"postgresql+psycopg", "postgresql+asyncpg"}
                and url.database == target["database"] and url.host == target["host"]
                and (url.port or 5432) == target["port"] and url.username == target["username"]
                and deployment_env == target["environment"], "database_target_mismatch")
        allowed_query = {} if target["environment"] != "test" else {"options": f"-csearch_path={target['schema']},public"}
        require(dict(url.query) == allowed_query, "database_overrides_forbidden")
    except PendingImportError:
        raise
    except Exception:
        raise PendingImportError("invalid_database_configuration") from None


def _no_publication_flags(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"preview", "checking_test_preview", "local_test_preview", "operator_review",
                       "operatorReview", "operatorApproval", "simulated", "isTestData", "fixtureOnly",
                       "fixtureNamespace", "review_fixture", "offline_cards"}:
                require(item is None or item is False, "publication_or_fixture_input_forbidden")
            if key == "operatorReviewCreated":
                require(item is False, "operator_approval_forbidden")
            _no_publication_flags(item)
    elif isinstance(value, list):
        for item in value:
            _no_publication_flags(item)


def _article(url):
    parts = urlsplit(url)
    require(parts.scheme == "https" and parts.hostname and parts.username is None and parts.password is None
            and parts.port in {None, 443} and not parts.fragment, "invalid_article_url")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
             and not (parts.hostname == "one-piece.com" and key == "ref")]
    return urlunsplit(("https", parts.hostname.lower(), parts.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


@dataclass(frozen=True)
class Delivery:
    envelope: dict
    bundle: carousel.PreparedBundle


def load_delivery(envelope, policy, *, now=None):
    version = policy["schema_version"]
    require(type(version) is int and version in {1, 2}, "invalid_import_policy")
    require(isinstance(envelope, dict)
            and set(envelope) == (ENVELOPE_FIELDS if version == 1 else ENVELOPE_FIELDS_V2), "invalid_delivery_envelope")
    require(type(envelope["schema_version"]) is int and envelope["schema_version"] == version
            and _uuid(envelope["job_id"]) and _uuid(envelope["generation_attempt_id"])
            and _hash(envelope["result_sha256"]) and _hash(envelope["bundle_sha256"]), "invalid_delivery_identity")
    for key in ("run_id", "policy_date", "day_policy_sha256"):
        require(envelope[key] == policy[key], "delivery_outside_reviewed_scope")
    if version == 1:
        require(all(envelope[key] == policy[key] for key in ("source_id", "content_type")),
                "delivery_outside_reviewed_scope")
    else:
        identity = _route_identity({key: envelope[key] for key in ROUTE_FIELDS})
        require(identity in {_route_identity(route) for route in policy["routes"]}, "delivery_outside_reviewed_scope")
    path = envelope["bundle_path"]
    require(isinstance(path, str) and path == f"{envelope['job_id']}/bundle.json"
            and PurePosixPath(path).as_posix() == path, "unsafe_bundle_path")
    bundle_file = Path(policy["bundle_root"]) / path
    binary = read_file(bundle_file)
    require(hashlib.sha256(binary).hexdigest() == envelope["bundle_sha256"], "bundle_digest_mismatch")
    raw = _json(binary)
    _no_publication_flags(raw)
    try:
        bundle = carousel.load_bundle(bundle_file, now=now)
        require(bundle.document.model_dump() == carousel.Bundle.model_validate(raw).model_dump(), "bundle_changed_during_read")
    except carousel.CarouselImportError:
        raise PendingImportError("bundle_validation_failed") from None
    require(hashlib.sha256(read_file(bundle_file)).hexdigest() == envelope["bundle_sha256"], "bundle_changed_during_read")
    require(len(bundle.items) == 1 and not bundle.reviews, "one_unapproved_item_required")
    item, = bundle.items
    evidence = item.raw.reviewEvidence or {}
    quality, qa = evidence.get("quality_report"), evidence.get("qa_report")
    require(evidence.get("jobId") == envelope["job_id"]
            and evidence.get("generationAttemptId") == envelope["generation_attempt_id"]
            and evidence.get("resultSha256") == envelope["result_sha256"], "delivery_evidence_mismatch")
    require(evidence.get("status") in {"ready_for_feed", "review_passed"}
            and evidence.get("review_mode") == "llm" and evidence.get("editorial_decision") == "approved_for_feed"
            and evidence.get("operatorReviewCreated") is False
            and isinstance(quality, dict) and quality.get("decision") == "pass_candidate"
            and quality.get("hard_blockers") == [] and isinstance(qa, list)
            and all(isinstance(issue, dict) and isinstance(issue.get("severity"), str)
                    and issue["severity"] not in {"blocker", "critical", "error"} for issue in qa)
            and isinstance(evidence.get("language_review"), dict) and evidence["language_review"], "producer_review_not_passed")
    usage = evidence.get("usage_records")
    require(isinstance(usage, list) and any(isinstance(record, dict) and record.get("stage") == "review"
            and isinstance(record.get("model"), str) and record["model"].strip()
            and type(record.get("total_tokens")) is int and record["total_tokens"] > 0 for record in usage),
            "review_usage_evidence_missing")
    if evidence["status"] == "review_passed":
        original = evidence.get("originalGeneration")
        require(_uuid(evidence.get("reviewId")) and _hash(evidence.get("reviewResultSha256"))
                and _hash(evidence.get("reviewArtifactSha256")) and isinstance(original, dict)
                and original.get("status") == "rejected_archived" and original.get("outcome") == "rejected"
                and original.get("resultSha256") == envelope["result_sha256"], "original_rejection_evidence_missing")
    require(item.package.content_type == envelope["content_type"] and item.package.menu_work_ids,
            "content_type_or_work_missing")
    if version == 2:
        require(item.package.menu_work_ids == [envelope["menu_work_id"]], "delivery_work_mismatch")
    primary = [source for source in item.package.sources if source.role == "primary"]
    require(len(primary) == 1 and primary[0].id == envelope["source_id"]
            and _article(primary[0].url) == _article(envelope["canonical_url"]), "delivery_source_mismatch")
    return Delivery(envelope, bundle)


def load_deliveries(policy, *, inbox=None, manifest=None, now=None):
    require((inbox is None) != (manifest is None), "choose_inbox_or_manifest")
    if manifest is not None:
        raw = _json(read_file(Path(manifest)))
        require(isinstance(raw, dict) and set(raw) == {"schema_version", "envelopes"}
                and type(raw["schema_version"]) is int and raw["schema_version"] == 1
                and isinstance(raw["envelopes"], list), "invalid_delivery_manifest")
        envelopes = raw["envelopes"]
    else:
        root = Path(inbox)
        require(root.is_absolute() and root.resolve() == root and root.is_dir(), "unsafe_inbox")
        paths = sorted(root.glob("*.json"))
        require(len(paths) <= policy["max_items"], "delivery_limit_exceeded")
        envelopes = []
        for path in paths:
            raw = _json(read_file(path, limit=65536))
            require(isinstance(raw, dict) and path.name == str(raw.get("job_id")) + ".json", "delivery_filename_mismatch")
            envelopes.append(raw)
    require(len(envelopes) <= policy["max_items"], "delivery_limit_exceeded")
    deliveries = [load_delivery(item, policy, now=now) for item in envelopes]
    require(len({d.envelope["job_id"] for d in deliveries}) == len(deliveries)
            and len({d.bundle.items[0].queue_task_id for d in deliveries}) == len(deliveries)
            and len({_article(d.envelope["canonical_url"]) for d in deliveries}) == len(deliveries), "duplicate_delivery_identity")
    return deliveries


async def import_deliveries(db, policy, deliveries, *, reviewed_sha256=None, apply=False, now=None):
    """Caller owns one transaction; existing imports never receive state writes."""
    policy_digest = validate_policy(policy, reviewed_sha256=reviewed_sha256, apply=apply, now=now)
    if apply:
        require(db.get_bind().dialect.name == "postgresql", "apply_requires_postgresql")
        # Share the existing carousel importer lock, including manual imports.
        await db.execute(text("SELECT pg_advisory_xact_lock(20261028, 2)"))
    topic = await db.scalar(select(Topic).where(Topic.topic_code == "anime", Topic.is_active.is_(True)))
    require(topic is not None, "active_anime_topic_required")
    already = await db.scalar(select(func.count()).select_from(AgentRun).where(
        AgentRun.output_payload[RECEIPT]["day_policy_sha256"].as_string() == policy["day_policy_sha256"]))
    report = {"mode": "apply" if apply else "dry_run", "policy_sha256": policy_digest,
              "created": 0, "unchanged": 0, "review_updated": 0, "links_added": 0, "items": []}
    new_count = 0
    for delivery in deliveries:
        envelope, bundle = delivery.envelope, delivery.bundle
        require(not bundle.reviews, "operator_approval_forbidden")
        item, = bundle.items
        run = await db.scalar(select(AgentRun).where(AgentRun.queue_task_id == item.queue_task_id))
        planned = await carousel.import_bundle(db, bundle, apply=False, now=now)
        if run is not None:
            output = run.output_payload or {}
            require(output.get("producer_review_evidence") == item.raw.reviewEvidence, "existing_review_evidence_changed")
            receipt = output.get(RECEIPT)
            require(receipt is None or (receipt.get("envelope_sha256") == fingerprint(envelope)
                    and receipt.get("day_policy_sha256") == policy["day_policy_sha256"]), "existing_delivery_changed")
            report["unchanged"] += 1
            report["items"].append({**planned["items"][0], "job_id": envelope["job_id"], "action": "unchanged"})
            continue
        matching_job = await db.scalar(select(AgentRun.agent_run_id).where(
            AgentRun.output_payload["producer_review_evidence"]["jobId"].as_string() == envelope["job_id"]).limit(1))
        require(matching_job is None, "job_already_bound_to_other_request")
        canonical_url = _article(envelope["canonical_url"])
        base = urlsplit(canonical_url)._replace(query="").geturl()
        sources = (await db.execute(select(ContentSource.content_id, ContentSource.source_url).where(
            ContentSource.source_role == "primary", ContentSource.source_url.startswith(base, autoescape=True)))).all()
        require(not any(_article(url) == canonical_url for _, url in sources), "article_already_imported")
        works = {}
        for work_id in item.package.menu_work_ids:
            rows = (await db.execute(select(Entity.entity_id, Entity.topic_id).join(
                EntityExternalId, EntityExternalId.entity_id == Entity.entity_id).where(
                EntityExternalId.provider == "menu_work", EntityExternalId.external_id == str(work_id)))).all()
            require(len(rows) == 1 and rows[0].topic_id == topic.topic_id, "existing_work_mapping_required")
            works[work_id] = rows[0].entity_id
        new_count += 1
        require(already + new_count <= policy["max_items"], "durable_daily_import_limit_exceeded")
        if apply:
            imported = await carousel.import_bundle(db, bundle, apply=True, now=now)
            require(imported["created"] == 1 and imported["review_updated"] == 0, "import_state_changed")
            result, = imported["items"]
            content = await db.scalar(select(Content).where(Content.content_id == result["content_id"]))
            draft = await db.scalar(select(Draft).where(Draft.draft_id == content.source_draft_id))
            run = await db.scalar(select(AgentRun).where(AgentRun.agent_run_id == draft.agent_run_id))
            require((content.status, content.judgment_status, draft.status) == ("pending_review", "needs_review", "checking")
                    and content.published_at is None, "new_content_not_pending")
            run.output_payload = {**run.output_payload, RECEIPT: {
                "schema_version": 1, "policy_sha256": policy_digest, "day_policy_sha256": policy["day_policy_sha256"],
                "envelope_sha256": fingerprint(envelope), "envelope": envelope}}
            for order, work_id in enumerate(item.package.menu_work_ids, 1):
                db.add(ContentEntityLink(content_id=content.content_id, entity_id=works[work_id],
                                        topic_id=topic.topic_id, link_method="carousel", sort_order=order))
            await db.flush()
        else:
            result, = planned["items"]
        report["created"] += 1
        report["links_added"] += len(works)
        report["items"].append({**result, "job_id": envelope["job_id"], "action": "pending_review"})
    report.update(publication_enabled=False, operator_approvals_created=0, llm_calls=0)
    return report


async def run(*, policy, database_url, deployment_env, reviewed_sha256=None, inbox=None,
              manifest=None, apply=False, now=None):
    validate_policy(policy, reviewed_sha256=reviewed_sha256, apply=apply, now=now)
    validate_target(database_url, policy, deployment_env)
    deliveries = load_deliveries(policy, inbox=inbox, manifest=manifest, now=now)
    engine = create_async_engine(database_url, echo=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            if not apply:
                await db.execute(text("SET TRANSACTION READ ONLY"))
            actual = (await db.execute(text("SELECT current_database() AS database, current_user AS username, current_schema() AS schema"))).mappings().one()
            require(all(actual[key] == policy["target"][key] for key in ("database", "username", "schema")),
                    "actual_database_target_mismatch")
            return await import_deliveries(db, policy, deliveries, reviewed_sha256=reviewed_sha256, apply=apply, now=now)
    finally:
        await engine.dispose()


def main(argv=None):
    """Installed-image entry point: python -m app.services.pending_content_import."""
    import argparse
    import asyncio
    import os
    import sys
    import time

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--reviewed-policy-sha256")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--inbox", type=Path)
    source.add_argument("--manifest", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Default: inspect current deliveries once")
    mode.add_argument("--watch", action="store_true", help="Consume within this one approved policy date")
    parser.add_argument("--apply", action="store_true", help="Create operator-review waiting content only")
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("--max-watch-seconds", type=float, default=86400)
    args = parser.parse_args(argv)
    try:
        require(not any(os.environ.get(key) for key in ("LLM_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANIME_LLM_KEY_FILE")),
                "importer_must_not_receive_llm_credentials")
        require(1 <= args.poll_seconds <= 60 and 1 <= args.max_watch_seconds <= 86400, "invalid_watch_bounds")
        require(not args.watch or args.inbox is not None, "watch_requires_inbox")
        policy_bytes = read_file(args.policy)
        policy = _json(policy_bytes)
        digest = validate_policy(policy, reviewed_sha256=args.reviewed_policy_sha256, apply=args.apply)
        deadline, previous = time.monotonic()+args.max_watch_seconds, None
        while True:
            require(read_file(args.policy) == policy_bytes, "reviewed_policy_changed")
            report = asyncio.run(run(policy=policy, database_url=os.environ.get("DATABASE_URL", ""),
                deployment_env=os.environ.get("MENU_DEPLOYMENT_ENV", ""), reviewed_sha256=args.reviewed_policy_sha256,
                inbox=args.inbox, manifest=args.manifest, apply=args.apply))
            report.update(watch=args.watch, reviewed_policy_sha256=digest)
            encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
            if encoded != previous:
                print(encoded, flush=True)
                previous = encoded
            if not args.watch or time.monotonic() >= deadline:
                return 0
            time.sleep(min(args.poll_seconds, max(0, deadline-time.monotonic())))
    except KeyboardInterrupt:
        print(json.dumps({"stopped":True, "reason":"interrupted", "publication_enabled":False}), flush=True)
        return 0
    except Exception as error:
        print(json.dumps({"error":str(error) if isinstance(error, PendingImportError) else "pending_import_failed",
                          "error_type":type(error).__name__, "publication_enabled":False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
