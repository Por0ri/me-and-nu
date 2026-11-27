"""Consume sealed native RAG deliveries into operator-review waiting content.

The default is a file-only dry-run. Applying requires a reviewed, date-scoped
target policy and its exact expiry override (null preserves native expiry).
This consumer never opens a producer DB,
loads credentials for a model, publishes content, or changes the period inbox.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AgentRun, Content, ContentEntityLink, Draft, Entity, EntityExternalId, Topic
from app.seeds import carousel_contents as carousel
from app.services import pending_content_import as pending
from app.services.rag_staging import MAX_RESULT_BYTES, export_rag_bundle

RAG_TYPES = frozenset({"character", "guide", "people", "music", "humor", "curation", "review"})
POLICY_FIELDS = {"schema_version", "purpose", "policy_date", "day_policy_sha256", "artifact_root",
                 "routes", "max_items", "target", "feed_valid_until"}
ENVELOPE_FIELDS = {"schema_version", "producer", "job_id", "generation_attempt_id", "result_sha256",
                   "native_result_path", "native_result_sha256", "manifest_path", "manifest_sha256",
                   "launch_path", "launch_sha256", "input_sha256", "admission_sha256", "policy_date", "policy_sha256",
                   "day_policy_sha256", "daily_policy", "rag_scope"}
IDENTITY_FIELDS = ("job_id", "generation_attempt_id", "input_sha256", "admission_sha256")
REQUEST_FIELDS = {"namespace", "work_id", "menu_work_id", "content_type", "query", "seed_evidence", "allowed_source_ids"}
ADMISSION_FIELDS = {"schema_version", "reservation_id", "policy_date", "policy_sha256", "input_sha256",
                    "counts_slot", "attempt_ordinal"}
REVISION_FIELDS = {"schema_version", "purpose", "policy_date", "day_policy_sha256",
    "effective_record_index", "prior_records_sha256", "prior_seen_count", "prior_seen_sha256",
    "prior_daily_attempts", "prior_daily_successes", "max_attempts", "success_target", "approved_at", "reason"}
ROUTE_FIELDS = {"namespace", "menu_work_id", "content_type", "allowed_source_ids"}
require = pending.require
canonical = pending.canonical
fingerprint = pending.fingerprint
RagPendingImportError = pending.PendingImportError


def _identifier(value, maximum=300):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1," + str(maximum) + "}", value) is not None


def _sources(value, *, canonical_order=True):
    return (isinstance(value, list) and 1 <= len(value) <= 64
            and all(_identifier(item, 100) for item in value) and len(set(value)) == len(value)
            and (not canonical_order or value == sorted(value)))


def _within(route, allowed):
    return (all(route[key] == allowed.get(key) for key in ("namespace", "menu_work_id", "content_type"))
            and _sources(allowed.get("allowed_source_ids"), canonical_order=False)
            and set(route["allowed_source_ids"]) <= set(allowed["allowed_source_ids"]))


def _directory(value):
    require(isinstance(value, (str, Path)), "unsafe_rag_directory")
    path = Path(value)
    require(path.is_absolute() and path.resolve() == path and path.is_dir(), "unsafe_rag_directory")
    current = path
    while current != current.parent:
        require(not current.is_symlink() and not (hasattr(current, "is_junction") and current.is_junction()),
                "unsafe_rag_directory")
        current = current.parent
    return path


def _timestamp(value):
    try:
        require(isinstance(value, str), "invalid_feed_expiry")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.utcoffset() is not None, "invalid_feed_expiry")
        return parsed
    except (ValueError, TypeError):
        raise RagPendingImportError("invalid_feed_expiry") from None


def validate_policy(policy, *, feed_valid_until=None, reviewed_sha256=None, apply=False, now=None,
                    reconcile_policy_date=None):
    require(isinstance(policy, dict) and set(policy) == POLICY_FIELDS
            and type(policy["schema_version"]) is int and policy["schema_version"] == 1
            and policy["purpose"] == "rag_pending_import", "invalid_rag_import_policy")
    require(type(policy["max_items"]) is int and 1 <= policy["max_items"] <= 20
            and pending._hash(policy["day_policy_sha256"]), "invalid_rag_import_scope")
    try:
        day = date.fromisoformat(policy["policy_date"])
        require(day.isoformat() == policy["policy_date"], "invalid_policy_date")
    except (ValueError, TypeError):
        raise RagPendingImportError("invalid_policy_date") from None
    _directory(policy["artifact_root"])
    require(feed_valid_until == policy["feed_valid_until"], "reviewed_feed_expiry_required")
    if feed_valid_until is not None:
        require(_timestamp(feed_valid_until) > (now or datetime.now(timezone.utc)), "feed_expiry_elapsed")
    routes = policy["routes"]
    require(isinstance(routes, list) and 1 <= len(routes) <= 1000, "invalid_rag_import_routes")
    identities = []
    for route in routes:
        require(isinstance(route, dict) and set(route) == ROUTE_FIELDS
                and _identifier(route["namespace"], 100)
                and type(route["menu_work_id"]) is int and 1 <= route["menu_work_id"] <= 2**63-1
                and route["content_type"] in RAG_TYPES and _sources(route["allowed_source_ids"], canonical_order=False),
                "invalid_rag_import_route")
        identities.append((route["namespace"], route["menu_work_id"], route["content_type"]))
    require(len(set(identities)) == len(identities), "duplicate_rag_import_route")
    target = policy["target"]
    require(isinstance(target, dict) and set(target) == pending.TARGET_FIELDS
            and type(target["port"]) is int and 1 <= target["port"] <= 65535
            and isinstance(target["username"], str) and re.fullmatch(r"[A-Za-z0-9_]{1,63}", target["username"]),
            "invalid_import_target")
    if target["environment"] == "test":
        require(target["database"] == "carousel_test" and target["host"] in {"127.0.0.1", "localhost"}
                and isinstance(target["schema"], str) and re.fullmatch(r"carousel_[0-9a-f]{32}", target["schema"]),
                "invalid_test_target")
    else:
        require(target["environment"] in {"production", "checking"}
                and target["database"] == target["username"] == "me_nu_checking"
                and target["host"] == "postgres" and target["port"] == 5432 and target["schema"] == "public",
                "invalid_service_target")
    digest = fingerprint(policy)
    if reviewed_sha256 is not None or apply:
        require(pending._hash(reviewed_sha256) and reviewed_sha256 == digest, "reviewed_policy_digest_required")
    if reconcile_policy_date is not None:
        today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Seoul")).date()
        require(apply and reconcile_policy_date == policy["policy_date"] and day < today,
                "invalid_expired_policy_reconciliation")
    elif apply:
        today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Seoul")).date()
        require(today == day, "policy_date_inactive")
    return digest


def _read_json(path, *, expected_sha=None, limit=pending.MAX_JSON):
    data = pending.read_file(path, limit=limit)
    if expected_sha is not None:
        require(hashlib.sha256(data).hexdigest() == expected_sha, "rag_artifact_hash_changed")
    value = pending._json(data)
    require(canonical(value) == data, "noncanonical_rag_artifact")
    return value


def _request(value):
    require(isinstance(value, dict) and set(value) == REQUEST_FIELDS
            and _identifier(value["namespace"], 100) and _identifier(value["work_id"])
            and type(value["menu_work_id"]) is int and 1 <= value["menu_work_id"] <= 2**63-1
            and value["content_type"] in RAG_TYPES and isinstance(value["query"], str)
            and value["query"] == value["query"].strip() and 1 <= len(value["query"]) <= 4000
            and all(char.isprintable() or char in "\n\t" for char in value["query"])
            and _sources(value["allowed_source_ids"]), "invalid_rag_request")
    seeds = value["seed_evidence"]
    require(isinstance(seeds, list) and 1 <= len(seeds) <= 64, "invalid_rag_seeds")
    require(all(isinstance(row, dict) and set(row) == {"document_id", "content_sha256"}
                and _identifier(row["document_id"]) and pending._hash(row["content_sha256"]) for row in seeds),
            "invalid_rag_seeds")
    require(seeds == sorted(seeds, key=lambda row: row["document_id"])
            and len({row["document_id"] for row in seeds}) == len(seeds), "noncanonical_rag_seeds")
    return value


def _bind_native(native, request, identity):
    require(isinstance(native, dict) and native.get("job_id") == identity["job_id"]
            and native.get("generation_attempt_id") == identity["generation_attempt_id"]
            and native.get("external_job_id", identity["job_id"]) == identity["job_id"]
            and native.get("external_input_sha256") == identity["input_sha256"]
            and native.get("namespace") == request["namespace"] and native.get("work_id") == request["work_id"],
            "unbound_native_rag_result")
    inner = native.get("request")
    require(isinstance(inner, dict) and inner.get("work_id") == request["work_id"]
            and inner.get("menu_work_ids") == [request["menu_work_id"]]
            and inner.get("content_type") == request["content_type"] and inner.get("query") == request["query"]
            and sorted(inner.get("seed_evidence_ids", [])) == [row["document_id"] for row in request["seed_evidence"]]
            and native.get("source_ids") == request["allowed_source_ids"], "native_rag_scope_changed")
    # The worker checks the read-only corpus before and after generation. Here
    # verify every *cited* seed body against the immutable launch fingerprint;
    # unused seeds remain in that request and need not appear as citations.
    evidence = native.get("evidence")
    require(isinstance(evidence, dict), "rag_evidence_missing")
    for seed in request["seed_evidence"]:
        document = evidence.get(seed["document_id"])
        if document is None:
            continue
        require(isinstance(document, dict), "rag_evidence_missing")
        excerpts = document.get("excerpts", [])
        require(isinstance(excerpts, list) and all(isinstance(row, dict) for row in excerpts), "invalid_rag_excerpts")
        try:
            body = (document.get("body") or "") + "\n" + "\n".join(
                (row.get("locator") or "") + "\n" + row["text"] for row in excerpts)
        except (KeyError, TypeError):
            raise RagPendingImportError("invalid_rag_excerpts") from None
        require(hashlib.sha256(body.encode()).hexdigest() == seed["content_sha256"], "rag_seed_body_changed")
    usage = native.get("usage_records")
    require(isinstance(usage, list) and any(isinstance(row, dict) and row.get("stage") == "review"
            and row.get("simulated") is False for row in usage), "native_review_usage_missing")


@dataclass(frozen=True)
class Delivery:
    envelope: dict
    bundle: carousel.PreparedBundle


def validate_limit_revision(revision, reviewed_sha256, policy):
    """Accept only the host-reviewed approval; never rewrite original envelopes."""
    if revision is None:
        require(reviewed_sha256 is None, "reviewed_limit_revision_required")
        return None
    require(isinstance(revision, dict) and set(revision) == REVISION_FIELDS
            and type(revision["schema_version"]) is int and revision["schema_version"] == 1
            and revision["purpose"] == "daily_success_only_revision"
            and revision["policy_date"] == policy["policy_date"]
            and revision["day_policy_sha256"] == policy["day_policy_sha256"]
            and revision["max_attempts"] is None
            and type(revision["success_target"]) is int and revision["success_target"] == 20
            and all(type(revision[key]) is int and revision[key] >= 0 for key in
                    ("effective_record_index", "prior_seen_count", "prior_daily_attempts", "prior_daily_successes"))
            and revision["prior_daily_successes"] <= revision["prior_daily_attempts"] <= 40
            and revision["prior_daily_successes"] <= 20
            and all(pending._hash(revision[key]) for key in ("prior_records_sha256", "prior_seen_sha256"))
            and isinstance(revision["reason"], str) and 1 <= len(revision["reason"].strip()) <= 500,
            "invalid_limit_revision")
    _timestamp(revision["approved_at"])
    require(pending._hash(reviewed_sha256) and fingerprint(revision) == reviewed_sha256,
            "reviewed_limit_revision_required")
    return reviewed_sha256


def load_delivery(path, policy, *, feed_valid_until, now=None, limit_revision=None,
                  reviewed_limit_revision_sha256=None):
    revision_sha = validate_limit_revision(limit_revision, reviewed_limit_revision_sha256, policy)
    root = _directory(policy["artifact_root"])
    envelope = _read_json(path, limit=128 * 1024)
    require(isinstance(envelope, dict) and set(envelope) == ENVELOPE_FIELDS
            and type(envelope["schema_version"]) is int and envelope["schema_version"] == 1
            and envelope["producer"] == "ani_rag" and pending._uuid(envelope["job_id"])
            and pending._uuid(envelope["generation_attempt_id"]), "invalid_rag_delivery")
    require(Path(path).name == envelope["job_id"] + ".json", "rag_delivery_filename_changed")
    hashes = ("result_sha256", "native_result_sha256", "manifest_sha256", "launch_sha256", "input_sha256", "admission_sha256")
    require(all(pending._hash(envelope[key]) for key in hashes)
            and envelope["result_sha256"] == envelope["native_result_sha256"]
            and envelope["policy_date"] == policy["policy_date"]
            and envelope["day_policy_sha256"] == policy["day_policy_sha256"]
            and pending._hash(envelope["policy_sha256"]), "rag_delivery_scope_changed")
    daily, scope = envelope["daily_policy"], envelope["rag_scope"]
    require(isinstance(daily, dict) and type(daily.get("schema_version")) is int
            and daily["schema_version"] in {3, 4}
            and daily.get("limit_mode") == "daily_counts" and daily.get("policy_date") == policy["policy_date"]
            and "max_attempts" in daily
            and (daily["schema_version"] == 3 and type(daily["max_attempts"]) is int and daily["max_attempts"] == 40
                 or daily["schema_version"] == 4 and daily["max_attempts"] is None and revision_sha is None)
            and type(daily.get("success_target")) is int and daily["success_target"] == 20
            and fingerprint(daily) == policy["day_policy_sha256"], "rag_daily_policy_binding_changed")
    require(isinstance(scope, dict) and set(scope) == {"schema_version", "purpose", "routes"}
            and type(scope["schema_version"]) is int and scope["schema_version"] == 1
            and scope["purpose"] == "rag_daily_generation" and isinstance(scope["routes"], list)
            and 1 <= len(scope["routes"]) <= 200
            and all(isinstance(route, dict) and set(route) == ROUTE_FIELDS for route in scope["routes"])
            and fingerprint({"daily": daily, "rag": scope}) == envelope["policy_sha256"],
            "rag_mixed_policy_binding_changed")
    require(all(any(_within(route, allowed) for allowed in scope["routes"]) for route in policy["routes"]),
            "rag_import_scope_not_admitted")
    period_inbox = Path(daily.get("delivery_root", ""))
    require(period_inbox.is_absolute() and not Path(path).parent.is_relative_to(period_inbox)
            and not period_inbox.is_relative_to(Path(path).parent), "rag_inbox_must_be_separate")
    directory = root / "rag" / "jobs" / envelope["job_id"] / "attempts" / envelope["generation_attempt_id"]
    expected = {"manifest_path": directory / "manifest.json", "launch_path": directory / "launch.json",
                "native_result_path": directory / "result.json"}
    for key, target in expected.items():
        require(envelope[key] == str(target), "unsafe_rag_artifact_path")
    manifest = _read_json(expected["manifest_path"], expected_sha=envelope["manifest_sha256"], limit=16384)
    launch = _read_json(expected["launch_path"], expected_sha=envelope["launch_sha256"])
    native = _read_json(expected["native_result_path"], expected_sha=envelope["result_sha256"], limit=MAX_RESULT_BYTES)
    identity = {key: envelope[key] for key in IDENTITY_FIELDS}
    require(isinstance(manifest, dict) and set(manifest) == {"schema_version", "identity", "files"}
            and type(manifest["schema_version"]) is int and manifest["schema_version"] == 1
            and manifest["identity"] == identity and manifest["files"] == {
                "launch.json": envelope["launch_sha256"], "result.json": envelope["result_sha256"]},
            "rag_manifest_binding_changed")
    require(isinstance(launch, dict) and set(launch) == {"identity", "request", "admission_receipt"}
            and launch["identity"] == identity, "rag_launch_binding_changed")
    request = _request(launch["request"])
    require(fingerprint(request) == identity["input_sha256"], "rag_request_hash_changed")
    route = {key: request[key] for key in ROUTE_FIELDS}
    require(any(_within(route, allowed) for allowed in policy["routes"]), "rag_route_not_reviewed")
    receipt = launch["admission_receipt"]
    require(isinstance(receipt, dict) and type(receipt.get("schema_version")) is int
            and (receipt["schema_version"] == 1 and set(receipt) == ADMISSION_FIELDS
                 and daily["schema_version"] == 3
                 and type(receipt["attempt_ordinal"]) is int and 1 <= receipt["attempt_ordinal"] <= 40
                 or receipt["schema_version"] == 2 and set(receipt) == ADMISSION_FIELDS | {"limit_revision_sha256"}
                 and type(receipt["attempt_ordinal"]) is int and receipt["attempt_ordinal"] >= 1
                 and (daily["schema_version"] == 4 and receipt["limit_revision_sha256"] is None
                      or daily["schema_version"] == 3 and revision_sha is not None
                      and receipt["limit_revision_sha256"] == revision_sha
                      and receipt["attempt_ordinal"] > limit_revision["prior_daily_attempts"]))
            and pending._uuid(receipt["reservation_id"]) and receipt["counts_slot"] is True
            and receipt["policy_date"] == policy["policy_date"]
            and receipt["policy_sha256"] == envelope["policy_sha256"]
            and receipt["input_sha256"] == identity["input_sha256"]
            and fingerprint(receipt) == identity["admission_sha256"], "rag_admission_binding_changed")
    _bind_native(native, request, identity)
    pending._no_publication_flags(native)
    with TemporaryDirectory(prefix="rag-pending-") as scratch:
        bundle_path = export_rag_bundle(native, Path(scratch).resolve() / "bundle",
                                       feed_valid_until=feed_valid_until, now=now)
        bundle = carousel.load_bundle(bundle_path, now=now)
    require(bundle.document.producer == "ani_rag" and len(bundle.items) == 1 and not bundle.reviews,
            "invalid_rag_pending_bundle")
    return Delivery(envelope, bundle)


def load_deliveries(policy, *, feed_valid_until, inbox=None, envelope=None, now=None,
                    limit_revision=None, reviewed_limit_revision_sha256=None):
    validate_limit_revision(limit_revision, reviewed_limit_revision_sha256, policy)
    require((inbox is None) != (envelope is None), "one_rag_delivery_source_required")
    if inbox is not None:
        inbox = _directory(inbox)
        paths = sorted(inbox.glob("*.json"))
    else:
        paths = [Path(envelope)]
    require(len(paths) <= policy["max_items"], "rag_delivery_count_exceeded")
    deliveries = [load_delivery(path, policy, feed_valid_until=feed_valid_until, now=now,
        limit_revision=limit_revision, reviewed_limit_revision_sha256=reviewed_limit_revision_sha256) for path in paths]
    require(len({d.envelope["job_id"] for d in deliveries}) == len(deliveries)
            and len({d.bundle.items[0].queue_task_id for d in deliveries}) == len(deliveries),
            "duplicate_rag_delivery_identity")
    return deliveries


async def import_deliveries(db, policy, deliveries, *, reviewed_sha256, feed_valid_until, now=None,
                            reconcile_policy_date=None):
    """Apply inside the caller's transaction, using the existing import lock."""
    digest = validate_policy(policy, feed_valid_until=feed_valid_until, reviewed_sha256=reviewed_sha256,
                             apply=True, now=now, reconcile_policy_date=reconcile_policy_date)
    require(db.get_bind().dialect.name == "postgresql", "apply_requires_postgresql")
    await db.execute(text("SELECT pg_advisory_xact_lock(20261028, 2)"))
    topic = await db.scalar(select(Topic).where(Topic.topic_code == "anime", Topic.is_active.is_(True)))
    require(topic is not None, "active_anime_topic_required")
    # Use the same durable receipt and lock as the period importer so both
    # producers consume one reviewed daily import allowance.
    already = await db.scalar(select(func.count()).select_from(AgentRun).where(
        AgentRun.output_payload[pending.RECEIPT]["day_policy_sha256"].as_string() == policy["day_policy_sha256"]))
    report = {"mode": "apply", "policy_sha256": digest, "created": 0, "unchanged": 0,
              "review_updated": 0, "links_added": 0, "items": []}
    for delivery in deliveries:
        envelope, bundle = delivery.envelope, delivery.bundle
        require(bundle.document.producer == "ani_rag" and len(bundle.items) == 1 and not bundle.reviews,
                "operator_approval_forbidden")
        item, = bundle.items
        existing = await db.scalar(select(AgentRun).where(AgentRun.queue_task_id == item.queue_task_id))
        if existing is not None:
            prior = (existing.output_payload or {}).get(pending.RECEIPT)
            require(prior is None or prior.get("envelope_sha256") == fingerprint(envelope), "existing_rag_delivery_changed")
            replay = await carousel.import_bundle(db, bundle, apply=True, now=now)
            require(replay["unchanged"] == 1 and replay["created"] == replay["review_updated"] == 0,
                    "existing_rag_state_changed")
            report["unchanged"] += 1
            report["items"].append({**replay["items"][0], "job_id": envelope["job_id"], "action": "unchanged"})
            continue
        duplicate_job = await db.scalar(select(AgentRun.agent_run_id).where(
            AgentRun.output_payload["producer_review_evidence"]["jobId"].as_string() == envelope["job_id"]).limit(1))
        require(duplicate_job is None, "job_already_bound_to_other_request")
        require(already + report["created"] + 1 <= policy["max_items"], "durable_daily_import_limit_exceeded")
        works = {}
        for work_id in item.package.menu_work_ids:
            rows = (await db.execute(select(Entity.entity_id, Entity.topic_id).join(
                EntityExternalId, EntityExternalId.entity_id == Entity.entity_id).where(
                EntityExternalId.provider == "menu_work", EntityExternalId.external_id == str(work_id)))).all()
            require(len(rows) == 1 and rows[0].topic_id == topic.topic_id, "existing_work_mapping_required")
            works[work_id] = rows[0].entity_id
        imported = await carousel.import_bundle(db, bundle, apply=True, now=now)
        require(imported["created"] == 1 and imported["review_updated"] == 0, "rag_import_state_changed")
        result, = imported["items"]
        content = await db.scalar(select(Content).where(Content.content_id == result["content_id"]))
        draft = await db.scalar(select(Draft).where(Draft.draft_id == content.source_draft_id))
        run = await db.scalar(select(AgentRun).where(AgentRun.agent_run_id == draft.agent_run_id))
        require((content.status, content.judgment_status, draft.status) == ("pending_review", "needs_review", "checking")
                and content.published_at is None, "new_rag_content_not_pending")
        receipt = {"schema_version": 1, "producer": "ani_rag",
            "policy_sha256": digest, "day_policy_sha256": policy["day_policy_sha256"],
            "envelope_sha256": fingerprint(envelope), "envelope": envelope}
        if reconcile_policy_date is not None:
            receipt["policy_date_reconciliation"] = {"policy_date": reconcile_policy_date,
                "reconciled_at": (now or datetime.now(timezone.utc)).isoformat(), "mode": "explicit_pending_only"}
        run.output_payload = {**run.output_payload, pending.RECEIPT: receipt}
        for order, work_id in enumerate(item.package.menu_work_ids, 1):
            db.add(ContentEntityLink(content_id=content.content_id, entity_id=works[work_id], topic_id=topic.topic_id,
                                    link_method="carousel", sort_order=order))
        await db.flush()
        report["created"] += 1
        report["links_added"] += len(works)
        report["items"].append({**result, "job_id": envelope["job_id"], "action": "pending_review"})
    return {**report, "publication_enabled": False, "operator_approvals_created": 0, "llm_calls": 0}


async def run(*, policy, feed_valid_until=None, inbox=None, envelope=None, apply=False, reviewed_sha256=None,
              database_url=None, deployment_env=None, now=None, reconcile_policy_date=None,
              limit_revision=None, reviewed_limit_revision_sha256=None):
    digest = validate_policy(policy, feed_valid_until=feed_valid_until, reviewed_sha256=reviewed_sha256,
                             apply=apply, now=now, reconcile_policy_date=reconcile_policy_date)
    deliveries = load_deliveries(policy, feed_valid_until=feed_valid_until, inbox=inbox, envelope=envelope, now=now,
        limit_revision=limit_revision, reviewed_limit_revision_sha256=reviewed_limit_revision_sha256)
    if not apply:
        return {"mode": "dry_run", "database_accessed": False, "policy_sha256": digest,
                "validated": len(deliveries), "items": [{"job_id": d.envelope["job_id"],
                "request_id": d.bundle.items[0].package.request_id, "digest": d.bundle.items[0].digest} for d in deliveries],
                "publication_enabled": False, "operator_approvals_created": 0, "llm_calls": 0}
    pending.validate_target(database_url, policy, deployment_env)
    engine = create_async_engine(database_url, echo=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            actual = (await db.execute(text("SELECT current_database() AS database, current_user AS username, current_schema() AS schema"))).mappings().one()
            require(all(actual[key] == policy["target"][key] for key in ("database", "username", "schema")),
                    "actual_database_target_mismatch")
            return await import_deliveries(db, policy, deliveries, reviewed_sha256=reviewed_sha256,
                                           feed_valid_until=feed_valid_until, now=now,
                                           reconcile_policy_date=reconcile_policy_date)
    finally:
        await engine.dispose()


def main(argv=None):
    import argparse
    import asyncio
    import os
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--feed-valid-until", help="Exact reviewed override; omit only for a policy with feed_valid_until: null (preserves native deadline)")
    parser.add_argument("--reviewed-policy-sha256")
    parser.add_argument("--limit-revision-file", type=Path)
    parser.add_argument("--reviewed-limit-revision-sha256")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--inbox", type=Path)
    source.add_argument("--envelope", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reconcile-policy-date", help="Explicit pending-only import for this exact past policy date; expiry and original daily limits still apply")
    parser.add_argument("--database-url-file", type=Path)
    parser.add_argument("--deployment-env")
    args = parser.parse_args(argv)
    try:
        require(not any(os.environ.get(key) for key in ("LLM_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANIME_LLM_KEY_FILE")),
                "importer_must_not_receive_llm_credentials")
        policy = pending._json(pending.read_file(args.policy))
        # Review the complete target/expiry before even opening a DB secret.
        validate_policy(policy, feed_valid_until=args.feed_valid_until, reviewed_sha256=args.reviewed_policy_sha256,
                        apply=args.apply, reconcile_policy_date=args.reconcile_policy_date)
        revision = (_read_json(args.limit_revision_file, limit=16384) if args.limit_revision_file else None)
        validate_limit_revision(revision, args.reviewed_limit_revision_sha256, policy)
        database_url = None
        if args.apply:
            require(args.database_url_file is not None and args.deployment_env, "explicit_database_target_required")
            database_url = pending.read_file(args.database_url_file, limit=16384).decode().strip()
        report = asyncio.run(run(policy=policy, feed_valid_until=args.feed_valid_until, inbox=args.inbox,
            envelope=args.envelope, apply=args.apply, reviewed_sha256=args.reviewed_policy_sha256,
            database_url=database_url, deployment_env=args.deployment_env,
            reconcile_policy_date=args.reconcile_policy_date, limit_revision=revision,
            reviewed_limit_revision_sha256=args.reviewed_limit_revision_sha256))
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error) if isinstance(error, RagPendingImportError) else "rag_pending_import_failed",
                          "error_type": type(error).__name__, "publication_enabled": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
