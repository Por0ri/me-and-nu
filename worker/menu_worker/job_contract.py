"""Admission rules for the fake queue and the offline anime input probe."""
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
    def __init__(self, message, code="invalid_job_input"):
        super().__init__(message)
        self.code = code


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
    menu_work_id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.idempotency_key):
            raise JobInputError("idempotency_key must be 1 to 128 ASCII identifier characters")
        if not isinstance(self.source_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.source_id):
            raise JobInputError("Invalid source identifier", "invalid_source_id")
        if not isinstance(self.content_type, str) or self.content_type not in CONTENT_TYPES:
            raise JobInputError("Unsupported content_type")
        if self.source_id == "fixture":
            if self.menu_work_id is not None:
                raise JobInputError("Fake input cannot select a work", "unexpected_menu_work_id")
            canonical_source_url(self.source_url)
        elif type(self.menu_work_id) is not int or not 1 <= self.menu_work_id <= 2**63 - 1:
            raise JobInputError("Provide a positive catalog work ID", "invalid_menu_work_id")

    @property
    def canonical_url(self) -> str:
        if self.source_id == "fixture":
            return canonical_source_url(self.source_url)
        from menu_worker.anime_catalog import validate_anime_source
        return validate_anime_source(self.source_id, self.source_url, self.content_type, self.menu_work_id)

    @property
    def input_sha256(self) -> str:
        return self.hash_for_kind("fake" if self.source_id == "fixture" else "anime")

    def hash_for_kind(self, job_kind: str) -> str:
        return validate_job_request(self, job_kind)[2]

    @property
    def scenario(self) -> str:
        return urlsplit(self.canonical_url).path.split("/")[1]

    def as_dict(self) -> dict[str, str | int]:
        fields = {name: getattr(self, name) for name in sorted(INPUT_FIELDS)}
        if self.menu_work_id is not None:
            fields["menu_work_id"] = self.menu_work_id
        return fields


def validate_input(value: Mapping) -> JobInput:
    if not isinstance(value, Mapping) or set(value) not in (INPUT_FIELDS, INPUT_FIELDS | {"menu_work_id"}):
        raise JobInputError("Unsupported or missing input fields")
    return JobInput(**value)


def validate_job_request(value: JobInput | Mapping, job_kind: str) -> tuple[JobInput, str, str]:
    validate_job_kind(job_kind)
    request = value if isinstance(value, JobInput) else validate_input(value)
    if job_kind == "anime" and request.source_id == "fixture":
        raise JobInputError("Choose a registered anime source and work", "registered_source_required")
    if job_kind == "fake" and request.source_id != "fixture":
        raise JobInputError("Fake jobs require fixture input", "fixture_source_required")
    canonical_url = request.canonical_url
    identity = {"source_id": request.source_id, "source_url": canonical_url, "content_type": request.content_type}
    if job_kind == "anime":
        identity.update(job_kind="anime", menu_work_id=request.menu_work_id)
    # The v1/v2 fake identity remains byte-for-byte compatible.
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return request, canonical_url, hashlib.sha256(encoded).hexdigest()
