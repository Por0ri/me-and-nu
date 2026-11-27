"""Translate independently reviewed native RAG output into pending carousel content.

This boundary neither runs an Agent nor approves/publicizes content. Native IDs,
evidence and review records are bound into the immutable import package. The
existing carousel importer owns transactions, idempotency and operator approval.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import UUID


RAG_TYPES = frozenset({"character", "guide", "people", "music", "humor", "curation", "review"})
MAX_RESULT_BYTES = 200 * 1024 * 1024


class RagStagingError(ValueError):
    """Bounded error codes only; never includes original content or credentials."""


def require(condition, code):
    if not condition:
        raise RagStagingError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _text(value, maximum=300):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError):
        return False


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _time(value):
    require(isinstance(value, str), "rag_timestamp_required")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(stamp.tzinfo is not None and stamp.utcoffset() is not None, "rag_timestamp_required")
        return stamp.astimezone(timezone.utc)
    except ValueError:
        raise RagStagingError("rag_timestamp_required") from None


def _unique(values, code):
    require(isinstance(values, list) and values and all(_text(value) for value in values)
            and len(set(values)) == len(values), code)
    return set(values)


def _native(result):
    """Check native provenance without importing producer code into the API image."""
    require(isinstance(result, dict) and result.get("schema_version") == 1
            and result.get("producer") == "ani_rag" and result.get("simulated") is False
            and result.get("review_mode") == "llm",
            "rag_real_result_required")
    require(result.get("outcome") == "ready_for_staging"
            and result.get("producer_status") == "ready_for_downstream", "rag_not_review_passed")
    require(_uuid(result.get("job_id")) and _uuid(result.get("generation_attempt_id"))
            and result.get("external_job_id", result["job_id"]) == result["job_id"]
            and _hash(result.get("input_sha256")) and _text(result.get("namespace"))
            and _text(result.get("work_id")), "rag_job_identity_invalid")
    request, draft, review = (result.get(key) for key in ("request", "draft", "review"))
    require(all(isinstance(row, dict) for row in (request, draft, review)), "rag_native_payload_missing")
    kind = request.get("content_type")
    work_ids = request.get("menu_work_ids")
    require(kind in RAG_TYPES and request.get("work_id") == result["work_id"]
            and _text(request.get("query"), 10000)
            and isinstance(work_ids, list) and len(work_ids) == 1
            and type(work_ids[0]) is int and work_ids[0] > 0,
            "rag_request_scope_invalid")
    require(draft.get("content_type") == kind and draft.get("menu_work_ids") == work_ids
            and _text(draft.get("draft_id")) and _text(draft.get("retrieval_id"))
            and _text(draft.get("title")) and _text(draft.get("detail_body"), 10000),
            "rag_draft_scope_invalid")
    _time(draft.get("created_at"))
    require(review.get("draft_id") == draft["draft_id"] and review.get("decision") == "pass"
            and _text(review.get("review_id")) and _text(review.get("reviewer_id"))
            and _text(review.get("model_name")), "rag_independent_review_required")
    _time(review.get("reviewed_at"))
    issues = review.get("issues")
    require(isinstance(issues, list) and all(isinstance(item, dict) and item.get("blocking") is False
            for item in issues), "rag_review_blocker")
    require(not review.get("revision_instructions") and not review.get("evidence_requests"),
            "rag_review_incomplete")
    token_usage = review.get("token_usage")
    require(isinstance(token_usage, dict) and all(type(token_usage.get(key)) is int and token_usage[key] >= 0
            for key in ("input_tokens", "output_tokens")), "rag_review_usage_missing")
    evidence, chunks, images = (result.get(key) for key in ("evidence", "retrieved_chunks", "images"))
    require(all(isinstance(row, dict) and row for row in (evidence, chunks, images)), "rag_evidence_missing")
    evidence_ids = _unique(draft.get("evidence_ids"), "rag_draft_evidence_invalid")
    chunk_ids = _unique(draft.get("retrieved_chunk_ids"), "rag_draft_chunks_invalid")
    # Seeds select retrieval candidates; the finished draft retains only the
    # documents it actually cites. Unused seed IDs remain in the pinned request.
    _unique(request.get("seed_evidence_ids"), "rag_seed_evidence_invalid")
    require(evidence_ids <= evidence.keys() and chunk_ids <= chunks.keys(),
            "rag_evidence_link_missing")
    for document_id, document in evidence.items():
        require(isinstance(document, dict) and document.get("document_id") == document_id
                and document.get("work_link_status") == "matched"
                and set(work_ids) <= set(document.get("menu_work_ids", []))
                and _text(document.get("source_id")) and _text(document.get("source_url"), 1000)
                and _text(document.get("title")), "rag_evidence_scope_invalid")
    allowed_sources = result.get("source_ids")
    require(isinstance(allowed_sources, list) and {doc["source_id"] for doc in evidence.values()} <= set(allowed_sources),
            "rag_source_scope_invalid")
    for chunk_id in chunk_ids:
        chunk = chunks[chunk_id]
        require(isinstance(chunk, dict) and chunk.get("chunk_id") == chunk_id
                and chunk.get("evidence_id") in evidence_ids
                and set(work_ids) <= set(chunk.get("menu_work_ids", [])), "rag_chunk_link_invalid")
        document = evidence[chunk["evidence_id"]]
        require(chunk.get("source_id") == document["source_id"]
                and chunk.get("source_url") == document["source_url"], "rag_chunk_source_changed")
    cards = draft.get("cards")
    require(isinstance(cards, list) and 2 <= len(cards) <= 10
            and all(isinstance(card, dict) and _text(card.get("card_id")) for card in cards)
            and len({card["card_id"] for card in cards}) == len(cards), "rag_cards_invalid")
    for card in cards:
        source_assets = [card.get("image_asset_id"), *card.get("component_image_asset_ids", [])]
        require(all(asset_id in images for asset_id in source_assets), "rag_image_link_missing")
        for asset_id in source_assets:
            asset = images[asset_id]
            require(isinstance(asset, dict) and asset.get("asset_id") == asset_id
                    and asset.get("work_link_status") == "matched"
                    and set(work_ids) <= set(asset.get("menu_work_ids", [])), "rag_image_scope_invalid")
    return request, draft, review, evidence, chunks


def _converted(result, feed_valid_until):
    request, draft, review, evidence, chunks = _native(result)
    # A null override preserves the producer's own deadline, including None.
    # Never synthesize a lifetime or discard an explicit native deadline.
    native_expiry = draft.get("valid_until")
    effective_expiry = feed_valid_until if feed_valid_until is not None else native_expiry
    if effective_expiry is not None:
        expiry = _time(effective_expiry)
        require(expiry > _time(draft["created_at"]), "rag_expiry_invalid")
        if native_expiry is not None:
            require(expiry <= _time(native_expiry), "rag_expiry_extends_source")
    # One service source URL can preserve several native document IDs.
    groups = {}
    for document_id in draft["evidence_ids"]:
        doc = evidence[document_id]
        group = groups.setdefault(doc["source_url"], {"id": "rag-source:" + hashlib.sha256(doc["source_url"].encode()).hexdigest(),
            "url": doc["source_url"], "title": doc["title"], "role": "reference",
            "rag_document_ids": [], "rag_source_ids": []})
        group["rag_document_ids"].append(document_id)
        if doc["source_id"] not in group["rag_source_ids"]:
            group["rag_source_ids"].append(doc["source_id"])
    # The first cited document supplies the service's source navigation link;
    # all supporting documents remain explicit, including same-URL documents.
    next(iter(groups.values()))["role"] = "primary"
    facts = {}

    def claims(values):
        require(isinstance(values, list) and values, "rag_claims_missing")
        result_ids = []
        for claim in values:
            require(isinstance(claim, dict) and _text(claim.get("text"), 10000), "rag_claim_invalid")
            docs = _unique(claim.get("evidence_ids"), "rag_claim_evidence_invalid")
            cited_chunks = _unique(claim.get("evidence_chunk_ids"), "rag_claim_chunks_invalid")
            require(docs <= set(draft["evidence_ids"]) and cited_chunks <= set(draft["retrieved_chunk_ids"])
                    and {chunks[key]["evidence_id"] for key in cited_chunks} == docs,
                    "rag_claim_provenance_missing")
            identity = "rag-fact:" + hashlib.sha256(canonical(claim)).hexdigest()
            facts[identity] = {"id": identity, "statement": claim["text"],
                "source_ids": sorted({groups[evidence[key]["source_url"]]["id"] for key in docs}),
                "rag_document_ids": sorted(docs), "rag_chunk_ids": sorted(cited_chunks),
                "attribution": claim.get("attribution")}
            result_ids.append(identity)
        return list(dict.fromkeys(result_ids))

    rendered = result.get("rendered_cards")
    require(isinstance(rendered, list) and len(rendered) == len(draft["cards"]), "rag_rendered_cards_missing")
    by_id = {row.get("card_id"): row for row in rendered if isinstance(row, dict)}
    require(len(by_id) == len(rendered) and set(by_id) == {card["card_id"] for card in draft["cards"]},
            "rag_rendered_card_identity_invalid")
    cards, assets = [], []
    for page, original in enumerate(draft["cards"], 1):
        image = by_id[original["card_id"]]
        require(image.get("page") == page and _hash(image.get("sha256"))
                and image.get("width") == 1308 and image.get("height") == 1632, "rag_rendered_card_invalid")
        layout = original.get("layout")
        require(isinstance(layout, dict), "rag_card_layout_missing")
        cards.append({"page": page, "role": original["role"], "eyebrow": "",
            "title_lines": [original["headline"]], "body_sentences": [original["body"]],
            "fact_ids": claims(original.get("claims")), "rendered_image_key": image["sha256"],
            "rag_card_id": original["card_id"], "rag_image_asset_id": original["image_asset_id"],
            "layout": {"template": layout.get("template", "image_top_copy_bottom"),
                "image_box": layout.get("image_box"), "copy_box": layout.get("text_overlay_box"),
                "text_overlay_box": layout.get("text_overlay_box"), "background_style": layout.get("background_style", "blur")}})
        assets.append({"page": page, "path": f"cards/card_{page:02d}.png", "sha256": image["sha256"]})
    detail_ids = claims(draft.get("detail_claims"))
    metadata = deepcopy(result)
    for row in metadata["rendered_cards"]:
        row.pop("png_base64", None)
    # This metadata is inside package, so the existing immutable package digest
    # covers original IDs, review evidence, rendered cards and source links.
    package = {"request_id": "rag:" + hashlib.sha256(canonical([result["namespace"], result["work_id"]])).hexdigest(),
        "content_type": request["content_type"], "menu_work_ids": request["menu_work_ids"],
        "feed_valid_until": effective_expiry, "sources": list(groups.values()), "facts": list(facts.values()),
        "cards": cards, "detail_body": "\n\n".join(part for part in (
            draft["detail_body"], draft.get("source_footer"), draft.get("ai_disclosure")) if part), "detail_fact_ids": detail_ids,
        "detail_source_ids": sorted({source for key in detail_ids for source in facts[key]["source_ids"]}),
        "rag_provenance": metadata}
    review_evidence = {"producer": "ani_rag", "producer_status": result["producer_status"],
        "jobId": result["job_id"], "generationAttemptId": result["generation_attempt_id"],
        "inputSha256": result["input_sha256"], "review": review, "operatorReviewCreated": False,
        "usage_records": result.get("usage_records"), "cost_accounting": result.get("cost_accounting")}
    return {"package": package, "assets": assets, "reviewEvidence": review_evidence}


def build_rag_bundle(result, *, feed_valid_until=None):
    """Build a validated RAG bundle document. No filesystem, network or DB access."""
    try:
        item = _converted(result, feed_valid_until)
        return {"schemaVersion": 1, "producer": "ani_rag", "topicCode": "anime", "items": [item]}
    except RagStagingError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise RagStagingError("rag_result_invalid") from None


def validate_rag_item(package, assets, review_evidence):
    """Require exact conversion even when callers bypass this module's exporter."""
    try:
        require(isinstance(package, dict) and isinstance(package.get("rag_provenance"), dict), "rag_provenance_missing")
        expected = _converted(package["rag_provenance"], package["feed_valid_until"])
        require(expected == {"package": package, "assets": assets, "reviewEvidence": review_evidence},
                "rag_conversion_changed")
    except RagStagingError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise RagStagingError("rag_result_invalid") from None


def export_rag_bundle(result, output: Path, *, feed_valid_until=None, now=None):
    """Materialize original PNGs locally; no overwrite or service DB operation."""
    from app.seeds import carousel_contents as carousel

    require(len(canonical(result)) <= MAX_RESULT_BYTES, "rag_result_too_large")
    document = build_rag_bundle(result, feed_valid_until=feed_valid_until)
    output = Path(output)
    require(output.is_absolute() and output.parent.resolve() == output.parent
            and output.parent.is_dir() and not output.exists() and not output.is_symlink(), "rag_output_unavailable")
    with TemporaryDirectory(dir=output.parent, prefix=".rag-staging-") as temporary:
        folder = Path(temporary) / "bundle"
        (folder / "cards").mkdir(parents=True)
        for row in result["rendered_cards"]:
            try:
                data = base64.b64decode(row["png_base64"], validate=True)
            except (ValueError, TypeError, KeyError):
                raise RagStagingError("rag_png_invalid") from None
            require(len(data) <= carousel.MAX_PNG_BYTES and hashlib.sha256(data).hexdigest() == row["sha256"],
                    "rag_png_hash_changed")
            (folder / "cards" / f"card_{row['page']:02d}.png").write_bytes(data)
        manifest = folder / "bundle.json"
        manifest.write_bytes(canonical(document))
        carousel.load_bundle(manifest, now=now)
        # Reserve an empty directory to prevent replacing any existing output.
        output.mkdir()
        try:
            for path in folder.iterdir():
                path.rename(output / path.name)
        except OSError:
            raise RagStagingError("rag_output_incomplete") from None
    return output / "bundle.json"
