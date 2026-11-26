"""File-only native RAG inbox checks; no provider, broker or database access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.services import rag_pending_import as rag
from app.services import pending_content_import as pending
from test_carousel_postgres import _run_async
from test_rag_staging import native_fixture


def seal(root, native, request, daily, scope):
    """Canonical producer-shaped fixture; never used by a running worker."""
    root.mkdir(exist_ok=True)
    identity = {"job_id": native["job_id"], "generation_attempt_id": native["generation_attempt_id"],
                "input_sha256": rag.fingerprint(request)}
    mixed = rag.fingerprint({"daily": daily, "rag": scope})
    receipt = {"schema_version": 1, "reservation_id": str(uuid4()), "policy_date": daily["policy_date"],
        "policy_sha256": mixed, "input_sha256": identity["input_sha256"], "counts_slot": True, "attempt_ordinal": 1}
    identity["admission_sha256"] = rag.fingerprint(receipt)
    native["external_input_sha256"] = identity["input_sha256"]
    directory = root / "rag/jobs" / identity["job_id"] / "attempts" / identity["generation_attempt_id"]
    directory.mkdir(parents=True, exist_ok=True)
    launch = {"identity": identity, "request": request, "admission_receipt": receipt}
    files = {"launch.json": launch, "result.json": native}
    hashes = {}
    for name, value in files.items():
        data = rag.canonical(value)
        (directory / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    manifest = {"schema_version": 1, "identity": identity, "files": hashes}
    (directory / "manifest.json").write_bytes(rag.canonical(manifest))
    envelope = {"schema_version": 1, "producer": "ani_rag", **identity,
        "result_sha256": hashes["result.json"], "native_result_path": str(directory / "result.json"),
        "native_result_sha256": hashes["result.json"], "manifest_path": str(directory / "manifest.json"),
        "manifest_sha256": rag.fingerprint(manifest), "launch_path": str(directory / "launch.json"),
        "launch_sha256": hashes["launch.json"], "policy_date": daily["policy_date"], "policy_sha256": mixed,
        "day_policy_sha256": rag.fingerprint(daily), "daily_policy": daily, "rag_scope": scope}
    return envelope


def fixture(tmp_path, *, target=None):
    root = tmp_path / "artifacts"
    root.mkdir()
    inbox = tmp_path / "rag-inbox"
    inbox.mkdir()
    native = native_fixture()
    native["usage_records"] = [{"stage": "review", "model": "isolated-model-fixture", "simulated": False,
                                "estimated_cost_usd": None, "usage_complete": False}]
    request = {"namespace": native["namespace"], "work_id": native["work_id"], "menu_work_id": 8,
        "content_type": "character", "query": native["request"]["query"], "allowed_source_ids": native["source_ids"],
        "seed_evidence": [{"document_id": "doc-1", "content_sha256": hashlib.sha256(
            (native["evidence"]["doc-1"]["body"] + "\n").encode()).hexdigest()}]}
    routes = [{key: request[key] for key in rag.ROUTE_FIELDS}]
    scope = {"schema_version": 1, "purpose": "rag_daily_generation", "routes": routes}
    now = datetime.now(timezone.utc)
    daily = {"schema_version": 3, "policy_date": now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat(),
        "limit_mode": "daily_counts", "max_attempts": 40, "success_target": 20,
        "delivery_root": str(tmp_path / "period-inbox")}
    raw = seal(root, native, request, daily, scope)
    path = inbox / (native["job_id"] + ".json")
    path.write_bytes(rag.canonical(raw))
    policy = {"schema_version": 1, "purpose": "rag_pending_import", "policy_date": daily["policy_date"],
        "day_policy_sha256": rag.fingerprint(daily), "artifact_root": str(root), "routes": deepcopy(routes),
        "max_items": 20, "feed_valid_until": (now + timedelta(days=7)).isoformat(),
        "target": target or {"environment": "test", "host": "127.0.0.1", "port": 5432,
            "database": "carousel_test", "username": "postgres", "schema": "carousel_" + uuid4().hex}}
    return {"root": root, "inbox": inbox, "path": path, "native": native, "request": request,
            "daily": daily, "scope": scope, "raw": raw, "policy": policy}


def reseal(f):
    f["raw"] = seal(f["root"], f["native"], f["request"], f["daily"], f["scope"])
    f["path"].write_bytes(rag.canonical(f["raw"]))


def execute(f, **kwargs):
    return _run_async(rag.run(policy=f["policy"], feed_valid_until=f["policy"]["feed_valid_until"],
                             envelope=f["path"], **kwargs))


def test_default_dry_run_never_connects_database_and_keeps_source_bytes(tmp_path, monkeypatch):
    f = fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    monkeypatch.setattr(rag, "create_async_engine", lambda *a, **k: pytest.fail("dry-run opened DB"))
    report = execute(f, database_url="invalid://must-not-open")
    assert report["validated"] == 1 and report["database_accessed"] is False
    assert report["publication_enabled"] is False and report["llm_calls"] == 0
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}


@pytest.mark.parametrize("case", ["body_sha", "manifest_identity", "launch_identity", "native_job", "scope",
    "seed_body", "missing_external_hash", "review_usage", "offline", "operator_flag", "blocker",
    "wrong_daily_hash", "wrong_mixed_hash", "changed_daily_limit", "same_period_inbox", "escape_path"])
def test_reject_changed_or_unreviewed_delivery(tmp_path, case):
    f = fixture(tmp_path)
    if case == "body_sha":
        Path(f["raw"]["native_result_path"]).write_bytes(b"{}")
    elif case == "manifest_identity":
        path = Path(f["raw"]["manifest_path"])
        value = json.loads(path.read_bytes())
        value["identity"]["job_id"] = str(uuid4())
        path.write_bytes(rag.canonical(value))
        f["raw"]["manifest_sha256"] = rag.fingerprint(value)
    elif case == "launch_identity":
        path = Path(f["raw"]["launch_path"])
        value = json.loads(path.read_bytes())
        value["identity"]["input_sha256"] = "f" * 64
        path.write_bytes(rag.canonical(value))
        f["raw"]["launch_sha256"] = rag.fingerprint(value)
    elif case == "native_job":
        f["native"]["external_job_id"] = str(uuid4())
        reseal(f)
    elif case == "scope":
        f["native"]["request"]["menu_work_ids"] = [9]
        reseal(f)
    elif case == "seed_body":
        f["native"]["evidence"]["doc-1"]["body"] += " changed"
        reseal(f)
    elif case == "missing_external_hash":
        path = Path(f["raw"]["native_result_path"])
        value = json.loads(path.read_bytes())
        value.pop("external_input_sha256")
        path.write_bytes(rag.canonical(value))
    elif case == "review_usage":
        f["native"]["usage_records"] = []
        reseal(f)
    elif case == "offline":
        f["native"]["simulated"] = True
        reseal(f)
    elif case == "operator_flag":
        f["native"]["operatorReviewCreated"] = True
        reseal(f)
    elif case == "blocker":
        f["native"]["review"]["issues"] = [{"blocking": True, "code": "test_blocker"}]
        reseal(f)
    elif case == "wrong_daily_hash": f["raw"]["day_policy_sha256"] = "a" * 64
    elif case == "wrong_mixed_hash": f["raw"]["policy_sha256"] = "a" * 64
    elif case == "changed_daily_limit":
        f["daily"]["max_attempts"] = 400
        reseal(f)
        f["policy"]["day_policy_sha256"] = rag.fingerprint(f["daily"])
    elif case == "same_period_inbox":
        f["daily"]["delivery_root"] = str(f["inbox"])
        reseal(f)
        f["policy"]["day_policy_sha256"] = rag.fingerprint(f["daily"])
    elif case == "escape_path": f["raw"]["native_result_path"] = str(tmp_path / "outside.json")
    f["path"].write_bytes(rag.canonical(f["raw"]))
    with pytest.raises((rag.RagPendingImportError, ValueError)):
        execute(f)


def test_cited_subset_of_allowed_sources_and_unused_seeds_are_valid(tmp_path):
    f = fixture(tmp_path)
    # Scope ordering is not meaningful; only the submitted request is canonical.
    f["scope"]["routes"][0]["allowed_source_ids"] = ["unused_source", "fixture_reference"]
    f["policy"]["routes"] = deepcopy(f["scope"]["routes"])
    f["request"]["seed_evidence"].append({"document_id": "doc-unused", "content_sha256": "0" * 64})
    f["native"]["request"]["seed_evidence_ids"].append("doc-unused")
    reseal(f)
    assert execute(f)["validated"] == 1


def test_apply_requires_reviewed_target_expiry_before_database(tmp_path, monkeypatch):
    f = fixture(tmp_path)
    monkeypatch.setattr(rag, "create_async_engine", lambda *a, **k: pytest.fail("invalid approval opened DB"))
    with pytest.raises(rag.RagPendingImportError, match="reviewed_policy_digest_required"):
        execute(f, apply=True)
    digest = rag.fingerprint(f["policy"])
    f["policy"]["feed_valid_until"] = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
    with pytest.raises(rag.RagPendingImportError, match="reviewed_policy_digest_required"):
        execute(f, apply=True, reviewed_sha256=digest)
    with pytest.raises(rag.RagPendingImportError, match="database_target_mismatch"):
        execute(f, apply=True, reviewed_sha256=rag.fingerprint(f["policy"]),
                database_url="postgresql+psycopg://postgres@127.0.0.1:5432/production", deployment_env="test")


def test_apply_rejects_old_day_without_mutating_anything(tmp_path):
    f = fixture(tmp_path)
    with pytest.raises(rag.RagPendingImportError, match="policy_date_inactive"):
        execute(f, apply=True, reviewed_sha256=rag.fingerprint(f["policy"]),
                now=datetime.now(timezone.utc) + timedelta(days=1))


def test_duplicate_json_and_symlink_artifacts_rejected(tmp_path):
    f = fixture(tmp_path)
    original = f["path"].read_bytes()
    f["path"].write_bytes(b'{"producer":"ani_rag",' + original[1:])
    with pytest.raises(rag.RagPendingImportError): execute(f)
    f["path"].write_bytes(original)
    result = Path(f["raw"]["native_result_path"])
    other = result.with_name("unchanged-result.json")
    result.rename(other)
    result.symlink_to(other)
    with pytest.raises(rag.RagPendingImportError, match="unsafe_input_file"): execute(f)


def test_real_worker_exporter_hands_off_exact_native_contract_to_consumer(tmp_path, monkeypatch):
    # Imports the actual producer-side file exporter, with no Celery app/runtime
    # setup. The generated transport below is synthetic and calls no model.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "worker"))
    from menu_worker.rag_daily_runtime import export_native_delivery
    f = fixture(tmp_path)
    f["native"]["usage_records"][0].update(estimated_cost_usd="0.001", usage_complete=True)
    f["native"]["usage_summary"] = {"llm_calls": 1, "input_tokens": 10, "output_tokens": 5}
    f["native"]["cost_accounting"] = {"complete": True, "known_estimated_cost_usd": "0.001",
                                      "unknown_calls": 0, "total_estimated_cost_usd": "0.001"}
    reseal(f)
    launch = json.loads(Path(f["raw"]["launch_path"]).read_bytes())
    job = {**launch["identity"], "request": f["request"], "admission_receipt": launch["admission_receipt"],
        "execution_status": "succeeded", "outcome": "ready_for_staging", "producer_status": "ready_for_downstream",
        "result_sha256": f["raw"]["result_sha256"], "usage_records": f["native"]["usage_records"],
        "cost_complete": True, "artifact_path": Path(f["raw"]["manifest_path"]).relative_to(f["root"]).as_posix()}
    expected = f["path"].read_bytes()
    f["path"].unlink()
    first = export_native_delivery(f["root"], f["inbox"], job, daily_policy=f["daily"], rag_scope=f["scope"])
    repeat = export_native_delivery(f["root"], f["inbox"], job, daily_policy=f["daily"], rag_scope=f["scope"])
    assert first == repeat and f["path"].read_bytes() == expected
    assert execute(f)["validated"] == 1
