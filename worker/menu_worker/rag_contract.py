"""Immutable RAG input and admission already reserved by the shared daily controller.

These are local worker contracts, not a public API. A receipt is evidence of the
caller's durable reservation, not an independent allowance or a signature.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import re
from uuid import UUID


RAG_CONTENT_TYPES = frozenset({"character", "guide", "people", "music", "humor", "curation", "review"})
REQUEST_FIELDS = frozenset({"namespace", "work_id", "menu_work_id", "content_type", "query",
                            "seed_evidence", "allowed_source_ids"})
RECEIPT_FIELDS = frozenset({"schema_version", "reservation_id", "policy_date", "policy_sha256",
                            "input_sha256", "counts_slot", "attempt_ordinal"})


class RagContractError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise RagContractError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _identifier(value, maximum=300):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1," + str(maximum) + r"}", value) is not None


def is_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@dataclass(frozen=True)
class RagRequest:
    namespace: str
    work_id: str
    menu_work_id: int
    content_type: str
    query: str
    seed_evidence: tuple
    allowed_source_ids: tuple

    @classmethod
    def parse(cls, value):
        return validate_request(value)

    def __post_init__(self):
        require(_identifier(self.namespace, 100) and _identifier(self.work_id), "invalid_rag_identity")
        require(type(self.menu_work_id) is int and 1 <= self.menu_work_id <= 2**63 - 1, "invalid_rag_work")
        require(isinstance(self.content_type, str) and self.content_type in RAG_CONTENT_TYPES,
                "invalid_rag_content_type")
        require(isinstance(self.query, str) and self.query == self.query.strip() and 1 <= len(self.query) <= 4000
                and all(char.isprintable() or char in "\n\t" for char in self.query), "invalid_rag_query")
        require(isinstance(self.seed_evidence, (tuple, list)) and 1 <= len(self.seed_evidence) <= 64,
                "invalid_rag_seeds")
        seeds = []
        for seed in self.seed_evidence:
            require(isinstance(seed, Mapping) and set(seed) == {"document_id", "content_sha256"}
                    and _identifier(seed.get("document_id")) and is_hash(seed.get("content_sha256")),
                    "invalid_rag_seeds")
            seeds.append((seed["document_id"], seed["content_sha256"]))
        require(len({row[0] for row in seeds}) == len(seeds), "duplicate_rag_seed")
        require(isinstance(self.allowed_source_ids, (tuple, list)) and 1 <= len(self.allowed_source_ids) <= 64
                and all(_identifier(value, 100) for value in self.allowed_source_ids), "invalid_rag_sources")
        require(len(set(self.allowed_source_ids)) == len(self.allowed_source_ids), "duplicate_rag_source")
        # Store immutable pairs rather than caller-owned mutable dictionaries.
        object.__setattr__(self, "seed_evidence", tuple(sorted(seeds)))
        object.__setattr__(self, "allowed_source_ids", tuple(sorted(self.allowed_source_ids)))

    @property
    def identity_key(self):
        return digest(["ani_rag", self.namespace, self.work_id])

    @property
    def input_sha256(self):
        return digest(self.as_dict())

    def as_dict(self):
        return {"namespace": self.namespace, "work_id": self.work_id, "menu_work_id": self.menu_work_id,
                "content_type": self.content_type, "query": self.query,
                "seed_evidence": [{"document_id": key, "content_sha256": value} for key, value in self.seed_evidence],
                "allowed_source_ids": list(self.allowed_source_ids)}


def validate_request(value):
    if isinstance(value, RagRequest):
        return value
    require(isinstance(value, Mapping) and set(value) == REQUEST_FIELDS, "invalid_rag_request")
    return RagRequest(**value)


def validate_admission_receipt(value, request):
    request = validate_request(request)
    require(isinstance(value, Mapping) and set(value) == RECEIPT_FIELDS, "rag_daily_admission_required")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["counts_slot"] is True and type(value["attempt_ordinal"]) is int
            and 1 <= value["attempt_ordinal"] <= 40, "invalid_rag_daily_admission")
    try:
        valid_id = isinstance(value["reservation_id"], str) and str(UUID(value["reservation_id"])) == value["reservation_id"]
        valid_day = isinstance(value["policy_date"], str) and date.fromisoformat(value["policy_date"]).isoformat() == value["policy_date"]
    except (ValueError, TypeError, AttributeError):
        valid_id = valid_day = False
    require(valid_id and valid_day and is_hash(value["policy_sha256"])
            and value["input_sha256"] == request.input_sha256, "invalid_rag_daily_admission")
    return dict(value)
