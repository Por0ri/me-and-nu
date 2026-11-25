"""Synthetic immutable deliveries: validation only, no DB/network/LLM calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.services import pending_content_import as pending
from test_carousel_import import make_raw_bundle


def make_delivery(root, *, number=1, work_id=8, independent=False):
    job, attempt = str(uuid4()), str(uuid4())
    folder = root / job
    folder.mkdir(parents=True)
    raw = make_raw_bundle(folder, pages=2)
    now = datetime.now(timezone.utc)
    url = f"https://one-piece.com/news/{83000+number}/index.html"
    raw["items"][0]["package"].update(request_id=f"pending-{job}", content_type="goods", menu_work_ids=[work_id],
        feed_valid_until=(now+timedelta(days=2)).isoformat(), purge_at=(now+timedelta(days=32)).isoformat())
    raw["items"][0]["package"]["sources"][0].update(id="one_piece_official", url=url)
    for fact in raw["items"][0]["package"]["facts"]:
        fact["source_ids"] = ["one_piece_official"]
    raw["items"][0]["package"]["detail_source_ids"] = ["one_piece_official"]
    result_hash = hashlib.sha256(job.encode()).hexdigest()
    evidence = {"jobId":job, "generationAttemptId":attempt, "resultSha256":result_hash,
        "status":"ready_for_feed", "review_mode":"llm", "editorial_decision":"approved_for_feed",
        "quality_report":{"decision":"pass_candidate", "hard_blockers":[]}, "qa_report":[],
        "language_review":{"content_type_fit":5}, "operatorReviewCreated":False,
        "usage_records":[{"stage":"review", "model":"isolated-contract-fixture", "total_tokens":1}]}
    if independent:
        evidence.update(status="review_passed", reviewId=str(uuid4()), reviewResultSha256="a"*64,
                        reviewArtifactSha256="b"*64, originalGeneration={"status":"rejected_archived",
                        "outcome":"rejected", "resultSha256":result_hash})
    raw["items"][0]["reviewEvidence"] = evidence
    envelope = {"schema_version":1, "job_id":job, "generation_attempt_id":attempt, "result_sha256":result_hash,
        "bundle_path":f"{job}/bundle.json", "bundle_sha256":"", "source_id":"one_piece_official",
        "canonical_url":url, "content_type":"goods", "policy_date":now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat(),
        "run_id":"isolated-daily-test", "day_policy_sha256":"d"*64}
    write_delivery(root, raw, envelope)
    return raw, envelope


def write_delivery(root, raw, envelope, *, inbox=None):
    data = json.dumps(raw, ensure_ascii=False).encode()
    (root/envelope["bundle_path"]).write_bytes(data)
    envelope["bundle_sha256"] = hashlib.sha256(data).hexdigest()
    if inbox is not None:
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox/(envelope["job_id"]+".json")).write_text(json.dumps(envelope), encoding="utf-8")


def policy_for(root, envelope, *, target=None, maximum=40):
    return {"schema_version":1, "purpose":"pending_content_import", "bundle_root":str(root),
        "max_items":maximum, **{key:envelope[key] for key in
        ("run_id","policy_date","source_id","content_type","day_policy_sha256")},
        "target":target or {"host":"127.0.0.1", "port":5433, "database":"carousel_test",
                            "username":"recommendation_test", "schema":"carousel_"+"0"*32, "environment":"test"}}


@pytest.fixture
def delivery_fixture(tmp_path):
    root = tmp_path / "exports"
    root.mkdir()
    raw, envelope = make_delivery(root)
    return root, raw, envelope, policy_for(root, envelope)


def test_export_evidence_and_pngs_are_bound_without_creating_an_operator_review(delivery_fixture):
    root, raw, envelope, policy = delivery_fixture
    digest = pending.validate_policy(policy)
    assert pending.validate_policy(policy, reviewed_sha256=digest, apply=True) == digest
    delivery = pending.load_delivery(envelope, policy)
    assert delivery.bundle.reviews == {}
    assert delivery.bundle.items[0].raw.reviewEvidence == raw["items"][0]["reviewEvidence"]
    assert len(delivery.bundle.items[0].images) == 2
    assert envelope["bundle_sha256"] == hashlib.sha256((root/envelope["bundle_path"]).read_bytes()).hexdigest()


@pytest.mark.parametrize("change", ["bundle_hash", "job", "attempt", "result", "scope", "day_hash", "path", "extra",
                                   "preview", "approval", "blocked", "qa", "no_review", "fixture", "png", "expired"])
def test_changed_or_unapproved_deliveries_fail_before_database_access(delivery_fixture, change):
    root, raw, envelope, policy = delivery_fixture
    evidence = raw["items"][0]["reviewEvidence"]
    if change == "bundle_hash": envelope["bundle_sha256"] = "f"*64
    elif change == "job": evidence["jobId"] = str(uuid4())
    elif change == "attempt": evidence["generationAttemptId"] = str(uuid4())
    elif change == "result": evidence["resultSha256"] = "e"*64
    elif change == "scope": envelope["run_id"] = "different-run"
    elif change == "day_hash": envelope["day_policy_sha256"] = "e"*64
    elif change == "path": envelope["bundle_path"] = "../bundle.json"
    elif change == "extra": envelope["operator_review"] = {"decision":"approved"}
    elif change == "preview": raw["items"][0]["package"]["preview"] = True
    elif change == "approval": evidence["operatorReviewCreated"] = True
    elif change == "blocked": evidence["quality_report"]["hard_blockers"] = ["stop"]
    elif change == "qa": evidence["qa_report"] = [{"severity":"blocker"}]
    elif change == "no_review": evidence["usage_records"] = []
    elif change == "fixture": evidence["fixtureOnly"] = True
    elif change == "expired": raw["items"][0]["package"]["feed_valid_until"] = "2020-01-01T00:00:00+00:00"
    if change not in {"bundle_hash", "scope", "day_hash", "path", "extra", "png"}:
        write_delivery(root, raw, envelope)
    if change == "png":
        (root/envelope["job_id"]/"cards/1.png").write_bytes(b"tampered")
    with pytest.raises(pending.PendingImportError):
        pending.load_delivery(envelope, policy)


def test_policy_digest_date_target_and_limit_are_explicit(delivery_fixture):
    _, _, _, policy = delivery_fixture
    with pytest.raises(pending.PendingImportError, match="reviewed_policy_digest_required"):
        pending.validate_policy(policy, apply=True)
    with pytest.raises(pending.PendingImportError, match="reviewed_policy_digest_required"):
        pending.validate_policy(policy, reviewed_sha256="a"*64, apply=True)
    future = deepcopy(policy)
    future["policy_date"] = "2099-01-01"
    pending.validate_policy(future)  # Tomorrow's reviewed scope can be prepared offline.
    with pytest.raises(pending.PendingImportError, match="policy_date_inactive"):
        pending.validate_policy(future, reviewed_sha256=pending.fingerprint(future), apply=True)
    for key, value in (("max_items",41),("max_items",True),("purpose","publish")):
        with pytest.raises(pending.PendingImportError):
            pending.validate_policy({**policy,key:value})
    url = "postgresql+psycopg://recommendation_test@127.0.0.1:5433/carousel_test?options=-csearch_path%3Dcarousel_"+"0"*32+"%2Cpublic"
    pending.validate_target(url, policy, "test")
    for unsafe in (url.replace("5433","5434"), url.replace("carousel_test?","me_nu_checking?"), url+"&host=elsewhere"):
        with pytest.raises(pending.PendingImportError):
            pending.validate_target(unsafe, policy, "test")


def test_duplicate_manifest_and_inbox_filename_are_rejected(delivery_fixture, tmp_path):
    root, raw, envelope, policy = delivery_fixture
    manifest = tmp_path/"manifest.json"
    manifest.write_text(json.dumps({"schema_version":1,"envelopes":[envelope,envelope]}))
    with pytest.raises(pending.PendingImportError, match="duplicate_delivery_identity"):
        pending.load_deliveries(policy, manifest=manifest)
    inbox = tmp_path/"inbox"
    inbox.mkdir()
    (inbox/"wrong.json").write_text(json.dumps(envelope))
    with pytest.raises(pending.PendingImportError, match="delivery_filename_mismatch"):
        pending.load_deliveries(policy, inbox=inbox)


def test_independent_approval_preserves_original_rejection_without_operator_approval(tmp_path):
    root = tmp_path/"exports"
    root.mkdir()
    raw, envelope = make_delivery(root, independent=True)
    delivery = pending.load_delivery(envelope, policy_for(root,envelope))
    assert delivery.bundle.items[0].raw.reviewEvidence["originalGeneration"]["outcome"] == "rejected"
    assert delivery.bundle.reviews == {}


def test_input_symlinks_are_rejected(delivery_fixture, tmp_path):
    root, _, envelope, _ = delivery_fixture
    link = tmp_path/"linked.json"
    try:
        link.symlink_to(root/envelope["bundle_path"])
    except (OSError, NotImplementedError):
        pytest.skip("Creating symlinks is unavailable on this host")
    with pytest.raises(pending.PendingImportError, match="unsafe_input_file"):
        pending.read_file(link)
