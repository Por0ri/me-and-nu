"""Immutable fixture inputs for fake jobs and the anime routing probe."""
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


CONTENT_TYPES = frozenset({
    "news", "event", "guide", "people", "goods", "review", "humor",
    "character", "curation", "music",
})
FIXTURE_SCENARIOS = frozenset({"success", "held", "skipped", "fail", "slow"})
INPUT_FIELDS = frozenset({"idempotency_key", "source_id", "source_url", "content_type"})
JOB_KINDS = frozenset({"fake", "anime"})


class JobInputError(ValueError):
    """A bounded input error that does not echo untrusted URLs or credentials."""


def validate_job_kind(value: str) -> str:
    if not isinstance(value, str) or value not in JOB_KINDS:
        raise JobInputError("job_kind must be fake or anime")
    return value


def canonical_source_url(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise JobInputError("source_url must be a bounded fixture HTTPS URL")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or parsed.hostname != "example.invalid"
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or parsed.port not in {None, 443}
            or any(char.isspace() or ord(char) < 32 for char in value)
            or "\\" in value
        ):
            raise ValueError
        parts = parsed.path.split("/")
        if len(parts) < 3 or parts[1] not in FIXTURE_SCENARIOS or not any(parts[2:]):
            raise ValueError
        if any(part in {".", ".."} for part in parts):
            raise ValueError
        query = [
            (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
        ]
        return urlunsplit(("https", "example.invalid", parsed.path, urlencode(sorted(query)), ""))
    except ValueError:
        raise JobInputError("source_url must select a known scenario below https://example.invalid/") from None


@dataclass(frozen=True)
class JobInput:
    idempotency_key: str
    source_id: str
    source_url: str
    content_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.idempotency_key):
            raise JobInputError("idempotency_key must be 1 to 128 ASCII identifier characters")
        if self.source_id != "fixture":
            raise JobInputError("Only the fixture source is enabled")
        if not isinstance(self.content_type, str) or self.content_type not in CONTENT_TYPES:
            raise JobInputError("Unsupported content_type")
        canonical_source_url(self.source_url)

    @property
    def canonical_url(self) -> str:
        return canonical_source_url(self.source_url)

    @property
    def input_sha256(self) -> str:
        return self.hash_for_kind("fake")

    def hash_for_kind(self, job_kind: str) -> str:
        validate_job_kind(job_kind)
        normalized = {
            "source_id": self.source_id,
            "source_url": self.canonical_url,
            "content_type": self.content_type,
        }
        if job_kind == "anime":
            if self.scenario != "success":
                raise JobInputError("The anime routing probe requires a /success/ fixture URL")
            normalized["job_kind"] = job_kind
        # Keep the original fake serialization byte-for-byte for existing manifests and jobs.
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @property
    def scenario(self) -> str:
        return urlsplit(self.canonical_url).path.split("/")[1]

    def as_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in sorted(INPUT_FIELDS)}


def validate_input(value: Mapping) -> JobInput:
    if not isinstance(value, Mapping) or set(value) != INPUT_FIELDS:
        raise JobInputError("Input must contain exactly idempotency_key, source_id, source_url and content_type")
    return JobInput(**value)
