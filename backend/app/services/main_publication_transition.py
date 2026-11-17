"""Prepare and apply a fixed, explicitly approved existing-publication set.

This is an operator bulk-publication transition, not an LLM quality review.
Isolated rehearsals remain separate from the one approved Checking transition.
The production entry point requires matching external manifest/ID hashes,
explicit confirmation and a verified backup receipt supplied by the operator.
The receipt's actual file/restore verification belongs to that operator script.
Existing review compare-and-swap writes run in one transaction. Before/after
history uses existing JSON fields; no admin table or migration is needed.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile

from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.models import Content, ContentSource, Draft
from app.models.content_slide import ContentSlide
from app.repositories import admin_content_review_repository as repo
from app.services.admin_content_review_service import _without_preview


CONTENT_FIELDS = (
    "content_id", "source_draft_id", "status", "judgment_status", "published_at", "updated_at",
    "deleted_at", "ai_judgment_basis", "feed_valid_until", "title", "body", "summary",
    "image_url", "topic_id", "content_type", "production_type",
)
DRAFT_FIELDS = (
    "draft_id", "status", "updated_at", "factcheck_result", "title", "body",
    "structure_template", "structure_template_history",
)
SLIDE_FIELDS = tuple(column.name for column in ContentSlide.__table__.columns if column.name != "image_data")
SOURCE_FIELDS = tuple(column.name for column in ContentSource.__table__.columns)
RATIONALE = "Operator-authorized transition of the fixed existing Checking public set; no new content and no LLM quality review."
HISTORY_KEY = "main_publication_transitions"
APPROVED_CHECKING_IDS_SHA256 = "59d795b06e5cbf43b718c820bd70cfbfcfb69e50161c96eeb482af486396dded"
CHECKING_CONFIRMATION = "publish-existing-checking-3530-to-main"


class TransitionConflict(ValueError):
    """Only fixed error descriptions are emitted; content and credentials stay private."""


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TransitionConflict("Invalid timestamp")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _jsonable(value):
    if isinstance(value, datetime):
        return _time(value).isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _ids(values):
    if (not isinstance(values, list) or not 1 <= len(values) <= 5000
            or any(type(value) is not int or not 0 < value <= 2**63 - 1 for value in values)
            or len(set(values)) != len(values)):
        raise TransitionConflict("A unique nonempty fixed allowlist of at most 5000 IDs is required")
    return sorted(values)


async def snapshot_rows(db, content_ids, *, lock=False):
    """Read explicit metadata only; never discover or add IDs to the approved set."""
    ids = _ids(content_ids)
    async def rows(model, names, condition, order):
        query = select(*(getattr(model, name) for name in names)).where(condition).order_by(*order)
        if lock:
            query = query.with_for_update()
        return [dict(row) for row in (await db.execute(query)).mappings()]
    contents = await rows(Content, CONTENT_FIELDS, Content.content_id.in_(ids), [Content.content_id])
    if [row["content_id"] for row in contents] != ids:
        raise TransitionConflict("A fixed target is missing")
    draft_ids = [row["source_draft_id"] for row in contents]
    if any(type(value) is not int for value in draft_ids) or len(set(draft_ids)) != len(draft_ids):
        raise TransitionConflict("Each target must retain its own existing draft")
    drafts = {row["draft_id"]: row for row in await rows(Draft, DRAFT_FIELDS,
        Draft.draft_id.in_(draft_ids), [Draft.draft_id])}
    slides = await rows(ContentSlide, SLIDE_FIELDS, ContentSlide.content_id.in_(ids),
        [ContentSlide.content_id, ContentSlide.page])
    sources = await rows(ContentSource, SOURCE_FIELDS, ContentSource.content_id.in_(ids),
        [ContentSource.content_id, ContentSource.content_source_id])
    by_slide, by_source = {i: [] for i in ids}, {i: [] for i in ids}
    for row in slides:
        by_slide[row["content_id"]].append(row)
    for row in sources:
        by_source[row["content_id"]].append(row)
    return _jsonable([dict(content=c, draft=drafts.get(c["source_draft_id"]),
        slides=by_slide[c["content_id"]], sources=by_source[c["content_id"]]) for c in contents])


def _classification(row, at):
    try:
        c, d, slides, sources = row["content"], row["draft"], row["slides"], row["sources"]
        basis = c["ai_judgment_basis"]
        if (c["status"] != "active" or c["deleted_at"] is not None
                or (c["published_at"] is not None and _time(c["published_at"]) > at)
                or c["feed_valid_until"] is None or _time(c["feed_valid_until"]) <= at
                or not isinstance(d, dict) or c["source_draft_id"] != d["draft_id"]
                or not isinstance(basis, dict) or basis.get("method") != "carousel_import_v1"
                or not isinstance(d["factcheck_result"], dict)
                or not 2 <= len(slides) <= 10
                or [s["page"] for s in slides] != list(range(1, len(slides) + 1))
                or any(s["content_id"] != c["content_id"] for s in slides)
                or not sources or any(s["content_id"] != c["content_id"] for s in sources)):
            raise TransitionConflict("Target is not an existing visible carousel")
        _time(c["updated_at"])
        _time(d["updated_at"])
        if (c["judgment_status"] == "confirmed" and (basis.get("preview") is None or basis.get("preview") is False)
                and d["status"] == "published"):
            return "already_approved"
        preview = basis.get("checking_test_preview")
        if (c["judgment_status"] == "needs_review" and basis.get("preview") is True
                and isinstance(preview, dict) and preview.get("database") == "me_nu_checking"
                and preview.get("environment") == "checking"
                and preview.get("quality_approval") == "not_reviewed"
                and d["status"] in {"checking", "approved"}):
            return "existing_checking_preview"
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, TransitionConflict):
            raise
        raise TransitionConflict("Incomplete publication snapshot") from None
    raise TransitionConflict("Target is not an approved publication or eligible Checking preview")


def build_plan(rows, content_ids, *, as_of, operator_id):
    """Make a deterministic plan from a read-only export and exact operator allowlist."""
    ids, at = _ids(content_ids), _time(as_of)
    if not isinstance(operator_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,100}", operator_id):
        raise TransitionConflict("An explicit local operator identifier is required")
    if not isinstance(rows, list) or sorted(r["content"]["content_id"] for r in rows) != ids:
        raise TransitionConflict("Export must match the fixed allowlist exactly")
    entries = []
    draft_ids = set()
    for row in sorted(rows, key=lambda item: item["content"]["content_id"]):
        snapshot = _jsonable(row)
        classification = _classification(snapshot, at)
        if snapshot["draft"]["draft_id"] in draft_ids:
            raise TransitionConflict("Targets cannot share a draft")
        draft_ids.add(snapshot["draft"]["draft_id"])
        entries.append({"content_id": snapshot["content"]["content_id"], "classification": classification,
            "expected_fingerprint": fingerprint(snapshot), "snapshot": snapshot})
    plan = dict(schema_version=1, kind="fixed_existing_publication", as_of=at.isoformat(),
        allowed_content_ids=ids, operator_id=operator_id, rationale=RATIONALE, llm_quality_review_performed=False,
        production_apply_available=False, entries=entries)
    return plan | {"manifest_sha256": fingerprint(plan)}


def validate_plan(plan):
    if not isinstance(plan, dict):
        raise TransitionConflict("Invalid plan")
    try:
        rebuilt = build_plan([entry["snapshot"] for entry in plan["entries"]],
            plan["allowed_content_ids"], as_of=plan["as_of"], operator_id=plan["operator_id"])
    except (KeyError, TypeError, ValueError):
        raise TransitionConflict("Invalid fixed publication plan") from None
    if plan != rebuilt:
        raise TransitionConflict("Publication plan fingerprint or scope changed")
    return rebuilt


def _require_isolated(db):
    url = make_url(db.get_bind().url)
    if url.query:
        raise TransitionConflict("Connection overrides are forbidden for isolated rehearsal")
    if url.get_backend_name() == "sqlite":
        if url.database == ":memory:":
            return
        path = Path(url.database or "").resolve()
        if path.is_relative_to(Path(tempfile.gettempdir()).resolve()) and path.name.endswith("-test.sqlite"):
            return
    if (url.get_backend_name() == "postgresql" and url.host in {"localhost", "127.0.0.1", "::1", "main-publication-test-db"}
            and re.fullmatch(r"test_[a-z0-9_]+|[a-z0-9_]+_(?:test|e2e)", url.database or "")):
        return
    raise TransitionConflict("Applying publication plans is restricted to isolated test databases")


def _history(value):
    result = value.get(HISTORY_KEY, [])
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise TransitionConflict("Existing transition history is malformed")
    return result


def _append_history(value, record):
    return value | {HISTORY_KEY: [*_history(value), record]}


def _remove_last_history(value):
    history = _history(value)
    if not history:
        raise TransitionConflict("Transition history is missing")
    # Absence and an empty historical list are distinct in the plan fingerprint.
    original_had_key = history[-1]["history_key_existed"]
    clean = {key: item for key, item in value.items() if key != HISTORY_KEY}
    if len(history) > 1 or original_had_key:
        clean[HISTORY_KEY] = history[:-1]
    return clean


async def apply_isolated_plan(db, plan, *, operator_id):
    """One atomic rehearsal; embedded history requires no admin schema/identity."""
    plan = validate_plan(plan)
    _require_isolated(db)
    return await _apply_plan(db, plan, operator_id=operator_id)


def _require_checking_release(db, plan, *, expected_manifest_sha256, expected_ids_sha256, backup_receipt, confirmation):
    """Bind the one approved release to its exact DB, snapshot and recovery receipt."""
    url = make_url(db.get_bind().url)
    if (url.drivername != "postgresql+psycopg" or url.host != "postgres" or url.port != 5432
            or url.username != "me_nu_checking" or url.database != "me_nu_checking" or url.query):
        raise TransitionConflict("The approved Checking publication requires its exact database target")
    if confirmation != CHECKING_CONFIRMATION:
        raise TransitionConflict("Explicit confirmation for the fixed Checking publication is required")
    if (not isinstance(expected_manifest_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256)
            or plan["manifest_sha256"] != expected_manifest_sha256):
        raise TransitionConflict("The external approved manifest hash does not match")
    if (expected_ids_sha256 != APPROVED_CHECKING_IDS_SHA256
            or fingerprint(plan["allowed_content_ids"]) != APPROVED_CHECKING_IDS_SHA256
            or len(plan["entries"]) != 3530
            or sum(e["classification"] == "existing_checking_preview" for e in plan["entries"]) != 3526
            or sum(e["classification"] == "already_approved" for e in plan["entries"]) != 4):
        raise TransitionConflict("The publication scope differs from the approved existing 3530 contents")
    if (not isinstance(backup_receipt, dict)
            or set(backup_receipt) != {"verified", "database", "backup_sha256", "recovery_verified",
                "captured_before_apply", "plan_sha256", "allowed_ids_sha256"}
            or any(backup_receipt[key] is not True for key in ("verified", "recovery_verified", "captured_before_apply"))
            or backup_receipt["database"] != "me_nu_checking"
            or backup_receipt["plan_sha256"] != expected_manifest_sha256
            or backup_receipt["allowed_ids_sha256"] != expected_ids_sha256
            or not isinstance(backup_receipt["backup_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", backup_receipt["backup_sha256"])):
        raise TransitionConflict("A verified recovery backup receipt for this exact manifest is required")


async def apply_checking_publication_plan(db, plan, *, operator_id, expected_manifest_sha256,
        expected_ids_sha256, backup_receipt, confirmation):
    """Apply only the separately authorized Checking release, never generic discovery.

The historical plan's production_apply_available=false remains unchanged: the
plan itself does not grant production permission. This separate guarded entry
point consumes the subsequent explicit operator confirmation and backup proof.
No HTTP route or automatic caller is registered for this function.
"""
    plan = validate_plan(plan)
    _require_checking_release(db, plan, expected_manifest_sha256=expected_manifest_sha256,
        expected_ids_sha256=expected_ids_sha256, backup_receipt=backup_receipt, confirmation=confirmation)
    return await _apply_plan(db, plan, operator_id=operator_id, verify_checking_identity=True)


async def _apply_plan(db, plan, *, operator_id, verify_checking_identity=False):
    """Shared atomic implementation; callers must validate their distinct boundaries."""
    if operator_id != plan["operator_id"] or db.in_transaction():
        raise TransitionConflict("The declared operator and a fresh transaction are required")
    manifest_id = plan["manifest_sha256"]
    async with db.begin():
        if verify_checking_identity:
            actual = (await db.execute(text("SELECT current_database() AS database_name, current_user AS user_name"))).mappings().one()
            if actual["database_name"] != "me_nu_checking" or actual["user_name"] != "me_nu_checking":
                raise TransitionConflict("The connected database identity differs from the approved Checking target")
        current = {row["content"]["content_id"]: row for row in await snapshot_rows(db, plan["allowed_content_ids"], lock=True)}
        now = datetime.now(timezone.utc)
        pending, replayed, unchanged = [], [], []
        for entry in plan["entries"]:
            cid, row = entry["content_id"], current[entry["content_id"]]
            if (not isinstance(row["draft"], dict) or not isinstance(row["content"]["ai_judgment_basis"], dict)
                    or not isinstance(row["draft"]["factcheck_result"], dict)):
                raise TransitionConflict("Publication metadata changed after the snapshot")
            c_history = _history(row["content"]["ai_judgment_basis"])
            d_history = _history(row["draft"]["factcheck_result"])
            c_last = c_history[-1] if c_history else None
            d_last = d_history[-1] if d_history else None
            if c_last and c_last.get("manifest_sha256") == manifest_id:
                clean = row | {
                    "content": row["content"] | {"ai_judgment_basis": _remove_last_history(row["content"]["ai_judgment_basis"])},
                    "draft": row["draft"] | {"factcheck_result": _remove_last_history(row["draft"]["factcheck_result"])},
                }
                expected_record = {
                    "schema_version": 1, "manifest_sha256": manifest_id, "operator_id": operator_id,
                    "acted_at": row["content"]["updated_at"], "approval_kind": "operator_bulk_publication", "rationale": RATIONALE,
                    "llm_quality_review_performed": False,
                    "source_snapshot_sha256": entry["expected_fingerprint"], "result_snapshot_sha256": fingerprint(clean),
                    "before_value": {"content": entry["snapshot"]["content"], "draft": entry["snapshot"]["draft"]},
                }
                if (entry["classification"] != "existing_checking_preview"
                        or c_last != expected_record | {"history_key_existed": HISTORY_KEY in entry["snapshot"]["content"]["ai_judgment_basis"]}
                        or d_last != expected_record | {"history_key_existed": HISTORY_KEY in entry["snapshot"]["draft"]["factcheck_result"]}):
                    raise TransitionConflict("Committed transition history does not match this replay")
                replayed.append(cid)
                continue
            if fingerprint(row) != entry["expected_fingerprint"] or _classification(row, now) != entry["classification"]:
                raise TransitionConflict("Content, draft or publication material changed after the snapshot")
            if entry["classification"] == "already_approved":
                unchanged.append(cid)
            else:
                pending.append((entry, row))
        if pending and replayed:
            raise TransitionConflict("A partially committed manifest is not a valid atomic replay")
        for entry, row in pending:
            c, d = row["content"], row["draft"]
            content_values = dict(status="active", judgment_status="confirmed", updated_at=now,
                published_at=_time(c["published_at"]) if c["published_at"] else now,
                ai_judgment_basis=_without_preview(c["ai_judgment_basis"]))
            draft_values = dict(status="published", updated_at=now, factcheck_result=_without_preview(d["factcheck_result"]))
            resulting = row | {"content": c | _jsonable(content_values), "draft": d | _jsonable(draft_values)}
            record = {
                "schema_version": 1, "manifest_sha256": manifest_id, "operator_id": operator_id,
                "acted_at": now.isoformat(), "approval_kind": "operator_bulk_publication", "rationale": RATIONALE,
                "llm_quality_review_performed": False,
                "source_snapshot_sha256": entry["expected_fingerprint"], "result_snapshot_sha256": fingerprint(resulting),
                "before_value": {"content": c, "draft": d},
            }
            content_values["ai_judgment_basis"] = _append_history(content_values["ai_judgment_basis"],
                record | {"history_key_existed": HISTORY_KEY in c["ai_judgment_basis"]})
            draft_values["factcheck_result"] = _append_history(draft_values["factcheck_result"],
                record | {"history_key_existed": HISTORY_KEY in d["factcheck_result"]})
            original_c = c | {key: _time(c[key]) if c[key] else None for key in ("updated_at", "published_at", "deleted_at", "feed_valid_until")}
            original_d = d | {"updated_at": _time(d["updated_at"])}
            if not await repo.update_content(db, original_c, content_values) or not await repo.update_draft(db, original_d, draft_values):
                raise TransitionConflict("Publication compare-and-swap failed")
        result = dict(manifest_sha256=manifest_id, transitioned=len(pending), replayed=len(replayed),
            already_approved=len(unchanged), fixed_target_count=len(plan["entries"]), llm_calls=0)
    return result
