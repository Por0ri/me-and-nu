"""Native RAG staging contract regression; synthetic provider-shaped data only."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
from uuid import uuid4

from PIL import Image
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentRun, Content, Draft
from app.seeds import carousel_contents as carousel
from app.services.rag_staging import RAG_TYPES, RagStagingError, build_rag_bundle, export_rag_bundle
from test_carousel_import import AsyncAdapter, engine  # noqa: F401
from test_carousel_postgres import _run_async


def native_fixture():
    """Fixture mimics a real transport envelope; it never calls any provider."""
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    claim = {"text": "격리된 테스트 근거입니다.", "evidence_ids": ["doc-1"],
             "evidence_chunk_ids": ["chunk-1"], "attribution": None}
    cards, rendered, images = [], [], {}
    box = {"x": 0.04, "y": 0.04, "width": 0.92, "height": 0.64}
    copy = {"x": 0.06, "y": 0.70, "width": 0.88, "height": 0.26}
    for page, color in enumerate(("navy", "white"), 1):
        buffer = BytesIO()
        Image.new("RGB", (1308, 1632), color).save(buffer, format="PNG")
        png = buffer.getvalue()
        digest = hashlib.sha256(png).hexdigest()
        card_id, asset_id = f"card-{page}", f"image-{page}"
        cards.append({"card_id": card_id, "role": "cover" if page == 1 else "ending",
            "headline": f"RAG 격리 검증 {page}", "body": "실제 서비스 자료가 아닌 테스트입니다.",
            "claims": [claim], "image_asset_id": asset_id, "component_image_asset_ids": [],
            "layout": {"template": "image_top_copy_bottom", "image_box": box,
                "text_overlay_box": copy, "background_style": "blur"}})
        rendered.append({"card_id": card_id, "page": page, "sha256": digest,
            "png_base64": base64.b64encode(png).decode(), "width": 1308, "height": 1632})
        images[asset_id] = {"asset_id": asset_id, "source_id": "fixture_reference",
            "source_url": f"https://example.com/image/{page}", "work_link_status": "matched", "menu_work_ids": [8]}
    document = {"document_id": "doc-1", "title": "격리 검증 출처", "source_id": "fixture_reference",
        "source_url": "https://example.com/reference", "body": claim["text"],
        "work_link_status": "matched", "menu_work_ids": [8]}
    return {"schema_version": 1, "producer": "ani_rag", "simulated": False, "review_mode": "llm",
        "namespace": "isolated-fixture", "job_id": str(uuid4()), "generation_attempt_id": str(uuid4()),
        "work_id": "stable-rag-work-1", "input_sha256": "a" * 64,
        "request": {"work_id": "stable-rag-work-1", "query": "근거 기반 캐릭터 소개",
            "content_type": "character", "menu_work_ids": [8], "seed_evidence_ids": ["doc-1"]},
        "outcome": "ready_for_staging", "producer_status": "ready_for_downstream",
        "draft": {"draft_id": "draft-original", "retrieval_id": "retrieval-original",
            "created_at": now.isoformat(), "title": "RAG 격리 검증", "content_type": "character",
            "menu_work_ids": [8], "evidence_ids": ["doc-1"], "retrieved_chunk_ids": ["chunk-1"],
            "detail_body": "원래 근거의 ID와 연결을 보존하는 테스트입니다.", "detail_claims": [claim],
            "source_footer": "테스트 출처", "ai_disclosure": "AI 생성 테스트", "cards": cards},
        "review": {"review_id": "review-original", "draft_id": "draft-original", "decision": "pass",
            "issues": [], "reviewer_id": "isolated-reviewer", "model_name": "isolated-model-fixture",
            "token_usage": {"input_tokens": 10, "output_tokens": 5}, "reviewed_at": now.isoformat()},
        "evidence": {"doc-1": document}, "retrieved_chunks": {"chunk-1": {"chunk_id": "chunk-1",
            "evidence_id": "doc-1", "menu_work_ids": [8], "source_id": document["source_id"],
            "source_url": document["source_url"], "text": claim["text"]}},
        "images": images, "rendered_cards": rendered, "source_ids": ["fixture_reference"],
        "usage_records": [], "cost_accounting": {"complete": False, "known_estimated_cost_usd": "0", "unknown_calls": 1}}


@pytest.fixture(params=["override", "native_deadline", "native_none"])
def rag_reviewed_bundle(tmp_path, request):
    original = native_fixture()
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    if request.param == "native_deadline":
        original["draft"]["valid_until"], expiry = expiry, None
    elif request.param == "native_none":
        original["draft"]["valid_until"], expiry = None, None
    path = export_rag_bundle(original, tmp_path / "rag-bundle", feed_valid_until=expiry)
    pending = carousel.load_bundle(path)
    item, = pending.items
    record = {"digest": item.digest, "decision": "approved", "reviewedBy": "isolated-operator",
        "reviewedAt": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "comment": "격리 DB 회귀 테스트의 운영자 승인입니다."}
    review_path = tmp_path / "operator-review.json"
    review_path.write_text(json.dumps({"schemaVersion": 1, "records": [record]}), encoding="utf-8")
    approved = carousel.load_bundle(path, review_record_file=review_path)
    return pending, approved, False


def test_native_ids_links_images_and_unknown_cost_preserved(rag_reviewed_bundle):
    pending, approved, _ = rag_reviewed_bundle
    item, = pending.items
    assert pending.document.producer == "ani_rag" and pending.reviews == {}
    assert item.package.content_type == "character" and item.package.menu_work_ids == [8]
    raw = item.raw.package["rag_provenance"]
    assert raw["draft"]["draft_id"] == "draft-original"
    assert raw["review"]["review_id"] == "review-original"
    assert raw["cost_accounting"]["complete"] is False
    fact, = item.raw.package["facts"]
    assert fact["rag_document_ids"] == ["doc-1"] and fact["rag_chunk_ids"] == ["chunk-1"]
    assert item.package.sources[0].url == raw["evidence"]["doc-1"]["source_url"]
    assert item.digest == approved.items[0].digest
    assert all("png_base64" not in row for row in raw["rendered_cards"])
    assert {hashlib.sha256(value).hexdigest() for value in item.images.values()} == {
        row["sha256"] for row in raw["rendered_cards"]}


@pytest.mark.parametrize("case", ["simulated", "offline_review", "held", "review_missing", "blocker", "review_other_draft",
    "review_usage_missing", "wrong_work", "wrong_chunk_source", "missing_evidence", "missing_claim_chunk",
    "image_wrong_work", "duplicate_card", "wrong_png_sha", "empty_expiry", "source_expired"])
def test_reject_incomplete_or_changed_native_results(tmp_path, case):
    result = native_fixture()
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    if case == "simulated": result["simulated"] = True
    elif case == "offline_review": result["review_mode"] = "offline_fixture"
    elif case == "held": result["outcome"] = "held"
    elif case == "review_missing": result["review"] = None
    elif case == "blocker": result["review"]["issues"] = [{"code": "blocked", "blocking": True}]
    elif case == "review_other_draft": result["review"]["draft_id"] = "other"
    elif case == "review_usage_missing": result["review"]["token_usage"] = None
    elif case == "wrong_work": result["evidence"]["doc-1"]["menu_work_ids"] = [9]
    elif case == "wrong_chunk_source": result["retrieved_chunks"]["chunk-1"]["source_url"] += "/changed"
    elif case == "missing_evidence": result["evidence"] = {}
    elif case == "missing_claim_chunk": result["draft"]["detail_claims"][0]["evidence_chunk_ids"] = ["unknown"]
    elif case == "image_wrong_work": result["images"]["image-1"]["menu_work_ids"] = [9]
    elif case == "duplicate_card": result["rendered_cards"][1]["card_id"] = "card-1"
    elif case == "wrong_png_sha": result["rendered_cards"][0]["sha256"] = "b" * 64
    elif case == "empty_expiry": expiry = ""
    elif case == "source_expired": result["draft"]["valid_until"] = datetime.now(timezone.utc).isoformat()
    with pytest.raises((RagStagingError, carousel.CarouselImportError)):
        export_rag_bundle(result, tmp_path / "rejected", feed_valid_until=expiry)
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("kind", sorted(RAG_TYPES))
def test_null_override_preserves_native_none_without_inventing_expiry(tmp_path, kind):
    result = native_fixture()
    result["request"]["content_type"] = result["draft"]["content_type"] = kind
    result["draft"]["valid_until"] = None
    before = deepcopy(result)
    path = export_rag_bundle(result, tmp_path / "no-deadline")
    package = carousel.load_bundle(path).items[0].raw.package
    assert "feed_valid_until" in package and package["feed_valid_until"] is None
    assert "feed_expiry_policy" not in package  # Marker belongs to stored visibility metadata only.
    assert result == before


def test_null_override_preserves_finite_native_deadline_and_conversion_identity(tmp_path):
    result = native_fixture()
    expiry = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    result["draft"]["valid_until"] = expiry
    implicit = build_rag_bundle(result)
    explicit = build_rag_bundle(result, feed_valid_until=expiry)
    assert implicit == explicit
    path = export_rag_bundle(result, tmp_path / "native-deadline")
    assert carousel.load_bundle(path).items[0].raw.package["feed_valid_until"] == expiry
    earlier = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert build_rag_bundle(result, feed_valid_until=earlier)["items"][0]["package"]["feed_valid_until"] == earlier
    later = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    with pytest.raises(RagStagingError, match="rag_expiry_extends_source"):
        build_rag_bundle(result, feed_valid_until=later)


@pytest.mark.parametrize("value", ["", "null", "2026-11-27", "2026-11-27T12:00:00", "not-a-date", 0, False])
@pytest.mark.parametrize("origin", ["override", "native"])
def test_malformed_expiry_is_not_treated_as_no_deadline(value, origin):
    result = native_fixture()
    if origin == "native":
        result["draft"]["valid_until"] = value
    with pytest.raises(RagStagingError, match="rag_timestamp_required"):
        build_rag_bundle(result, feed_valid_until=value if origin == "override" else None)


def test_expired_native_deadline_is_not_removed_by_null_override(tmp_path):
    result = native_fixture()
    # After creation but before import: the producer timestamp is valid but stale.
    result["draft"]["valid_until"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with pytest.raises(carousel.CarouselImportError, match="(?i)expir"):
        export_rag_bundle(result, tmp_path / "expired")
    assert not (tmp_path / "expired").exists()


def test_bypassing_converter_cannot_change_native_binding(tmp_path):
    result = native_fixture()
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    path = export_rag_bundle(result, tmp_path / "rag", feed_valid_until=expiry)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["items"][0]["package"]["detail_body"] = "변경된 미검수 문구"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(carousel.CarouselImportError, match="rag_conversion_changed"):
        carousel.load_bundle(path)


def test_idempotency_identity_preserves_native_work_across_attempts():
    result = native_fixture()
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    first = build_rag_bundle(result, feed_valid_until=expiry)["items"][0]["package"]
    other = deepcopy(result)
    other["generation_attempt_id"] = str(uuid4())
    second = build_rag_bundle(other, feed_valid_until=expiry)["items"][0]["package"]
    assert first["request_id"] == second["request_id"]
    assert first != second  # A changed sealed result must conflict, not create a second article.


def test_unused_seed_is_preserved_without_falsely_requiring_a_citation():
    result = native_fixture()
    result["request"]["seed_evidence_ids"].append("unused-seed")
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    package = build_rag_bundle(result, feed_valid_until=expiry)["items"][0]["package"]
    assert package["rag_provenance"]["request"]["seed_evidence_ids"] == ["doc-1", "unused-seed"]
    assert package["sources"][0]["rag_document_ids"] == ["doc-1"]


def test_pending_duplicate_approval_preserves_history(engine, rag_reviewed_bundle):
    pending, approved, _ = rag_reviewed_bundle

    async def scenario():
        with Session(engine) as db, db.begin():
            adapter = AsyncAdapter(db)
            dry = await carousel.import_bundle(adapter, pending)
            assert dry["created"] == 1 and db.scalar(select(func.count()).select_from(Content)) == 0
            first = await carousel.import_bundle(adapter, pending, apply=True)
            repeat = await carousel.import_bundle(adapter, pending, apply=True)
            assert (first["created"], repeat["unchanged"]) == (1, 1)
            content, draft, run = (db.scalars(select(model)).one() for model in (Content, Draft, AgentRun))
            assert (content.status, content.judgment_status, draft.status) == ("pending_review", "needs_review", "checking")
            stored_expiry = content.feed_valid_until
            if stored_expiry is not None and stored_expiry.tzinfo is None:
                stored_expiry = stored_expiry.replace(tzinfo=timezone.utc)  # SQLite strips UTC tzinfo.
            assert stored_expiry == pending.items[0].expires_at
            if pending.items[0].package.feed_valid_until is None:
                assert content.ai_judgment_basis["feed_expiry_policy"] == "no_deadline_v1"
                assert draft.factcheck_result["feed_expiry_policy"] == "no_deadline_v1"
            native = deepcopy(run.output_payload)
            await carousel.import_bundle(adapter, approved, apply=True)
            await carousel.import_bundle(adapter, pending, apply=True)
            assert (content.status, content.judgment_status, draft.status) == ("active", "confirmed", "published")
            assert run.output_payload == native
            for model in (Content, Draft, AgentRun):
                assert db.scalar(select(func.count()).select_from(model)) == 1

    _run_async(scenario())


def test_rag_preview_cannot_bypass_operator_approval(engine, rag_reviewed_bundle):
    pending, _, _ = rag_reviewed_bundle

    async def scenario():
        with Session(engine) as db, db.begin():
            with pytest.raises(carousel.CarouselImportError, match="separate operator approval"):
                await carousel.import_bundle(AsyncAdapter(db), pending, apply=True, local_test_preview=True)
            assert db.scalar(select(func.count()).select_from(Content)) == 0

    _run_async(scenario())


def test_changed_attempt_of_same_native_work_cannot_create_another_content(engine, tmp_path):
    result = native_fixture()
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    first = carousel.load_bundle(export_rag_bundle(result, tmp_path / "first", feed_valid_until=expiry))
    result["generation_attempt_id"] = str(uuid4())
    changed = carousel.load_bundle(export_rag_bundle(result, tmp_path / "second", feed_valid_until=expiry))

    async def scenario():
        with Session(engine) as db, db.begin():
            adapter = AsyncAdapter(db)
            await carousel.import_bundle(adapter, first, apply=True)
            original = deepcopy(db.scalars(select(AgentRun)).one().output_payload)
            with pytest.raises(carousel.CarouselImportError, match="different immutable digest"):
                await carousel.import_bundle(adapter, changed, apply=True)
            assert db.scalar(select(func.count()).select_from(Content)) == 1
            assert db.scalars(select(AgentRun)).one().output_payload == original

    _run_async(scenario())
