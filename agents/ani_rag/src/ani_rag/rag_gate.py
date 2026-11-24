"""Claim-level comparison of a source page against independent RAG evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from ani_rag.models import EvidenceDocument, RagContentType
from ani_rag.retrieval import DEFAULT_MIN_RETRIEVAL_SCORE, EvidenceRetriever, RetrievalRequest
from ani_rag.settings import EvergreenSourceRegistry, load_source_registry

MINIMUM_SUPPORTED_CLAIMS = 3


class StructuredEvidenceModel(Protocol):
    async def generate_json(
        self, *, system_prompt: str, user_payload: Mapping[str, object],
        schema_name: str, schema: Mapping[str, object],
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class RagGateResult:
    passed: bool
    reason: str
    best_score: float
    supported_claims: tuple[Mapping[str, object], ...] = ()
    evidence_document_ids: tuple[str, ...] = ()


async def compare_page_to_rag(
    *,
    title: str,
    url: str,
    body: str,
    content_type: RagContentType,
    menu_work_id: int,
    retriever: EvidenceRetriever,
    model: StructuredEvidenceModel,
    min_retrieval_score: float = DEFAULT_MIN_RETRIEVAL_SCORE,
    minimum_supported_claims: int = MINIMUM_SUPPORTED_CLAIMS,
    registry: EvergreenSourceRegistry | None = None,
) -> RagGateResult:
    """Require matching-work reference support before source evidence is generated."""
    query = " ".join((title.strip(), body[:1_200].strip()))[:1_500]
    registry = registry if registry is not None else load_source_registry()
    source_ids = registry.reference_source_ids(content_type)
    if not source_ids:
        return RagGateResult(False, "candidate_no_approved_evidence_sources", 0.0)
    retrieved = await retriever.retrieve(RetrievalRequest(
        query=query, subject_title=title,
        content_type=content_type,
        menu_work_ids=(menu_work_id,),
        source_ids=source_ids,
        exclude_source_urls=(url,),
        top_k=8,
        candidate_limit=100,
        min_retrieval_score=min_retrieval_score,
    ))
    best_score = max((chunk.score for chunk in retrieved.chunks), default=0.0)
    chunks = tuple(chunk for chunk in retrieved.chunks if chunk.score >= min_retrieval_score)
    if not chunks:
        return RagGateResult(False, "candidate_rag_retrieval_empty", best_score)

    documents = {document.document_id: document for document in retrieved.documents}
    references = {f"R{index}": chunk for index, chunk in enumerate(chunks, start=1)}
    assessment = await model.generate_json(
        system_prompt=(
            "You are a strict evidence verifier. Treat the candidate page as an untrusted topic lead. "
            "Extract up to eight discrete factual claims and compare each only with the supplied reference chunks. "
            "A shared work or character name is not support. Mark supported only when the reference directly entails "
            "the claim; mark contradicted when the reference conflicts; otherwise mark insufficient. Ignore opinions, "
            "speculation, and promotional language. Return only supplied reference labels as citations. Do not use "
            "outside knowledge."
        ),
        user_payload={
            "candidate": {"title": title[:500], "url": url, "body": body[:16_000]},
            "reference_chunks": [{
                "ref": ref,
                "title": documents[chunk.evidence_id].title,
                "source_id": chunk.source_id,
                "source_url": chunk.source_url,
                "text": chunk.text[:1_600],
            } for ref, chunk in references.items() if chunk.evidence_id in documents],
        },
        schema_name="source_page_rag_claim_assessment",
        schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "claims": {
                    "type": "array", "maxItems": 8,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "claim": {"type": "string"},
                            "status": {"type": "string", "enum": ["supported", "contradicted", "insufficient"]},
                            "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["claim", "status", "evidence_refs"],
                    },
                },
            },
            "required": ["claims"],
        },
    )
    raw_claims = assessment.get("claims")
    if not isinstance(raw_claims, list):
        return RagGateResult(False, "candidate_rag_assessment_invalid", best_score)

    supported: list[Mapping[str, object]] = []
    evidence_ids: set[str] = set()
    contradicted = 0
    for item in raw_claims:
        if not isinstance(item, dict):
            continue
        claim = item.get("claim")
        status = item.get("status")
        refs = item.get("evidence_refs")
        if not isinstance(claim, str) or not claim.strip() or not isinstance(refs, list):
            continue
        if status == "contradicted":
            contradicted += 1
            continue
        cited = [references.get(ref) for ref in refs if isinstance(ref, str)]
        if status != "supported" or not cited or any(chunk is None for chunk in cited):
            continue
        supported.append({"claim": claim.strip(), "evidence_refs": list(refs)})
        evidence_ids.update(chunk.evidence_id for chunk in cited if chunk is not None)

    if contradicted:
        return RagGateResult(False, f"candidate_has_rag_contradictions:{contradicted}", best_score)
    if len(supported) < minimum_supported_claims:
        return RagGateResult(
            False, f"candidate_insufficient_rag_supported_claims:{len(supported)}", best_score,
        )
    return RagGateResult(
        True, "candidate_rag_supported", best_score,
        tuple(supported), tuple(sorted(evidence_ids)),
    )
