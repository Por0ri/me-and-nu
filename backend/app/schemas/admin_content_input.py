"""Unregistered author-input contract; this is not a persistence/publication API."""

import re
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from app.schemas.common import CamelModel

# Application input limits, not changes to the storage schema.
SUMMARY_LIMIT = 10_000
BODY_LIMIT = 100_000
SOURCE_LIMIT = 50
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")
_HTTP_URL = TypeAdapter(AnyHttpUrl)


def clean_text(value):
    if isinstance(value, str):
        if _CONTROL.search(value):
            raise ValueError("Unsupported control character")
        return value.strip()
    return value


class AdminInputModel(CamelModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AdminContentSourceInput(AdminInputModel):
    # Role, citation order, publisher and provenance IDs remain server-owned.
    url: str = Field(min_length=1, max_length=1000)
    title: str | None = Field(default=None, max_length=300)

    @field_validator("url", "title", mode="before")
    @classmethod
    def clean(cls, value):
        return clean_text(value)

    @field_validator("title")
    @classmethod
    def empty_title(cls, value):
        return value or None

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        # Stored reference only: never resolve/fetch a submitted URL.
        if re.search(r"\s|\\", value):
            raise ValueError("Use an absolute HTTP(S) URL without whitespace")
        try:
            _HTTP_URL.validate_python(value)  # Validate host syntax, keep the original URL below.
            parsed = urlsplit(value)
            port = parsed.port
            if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or (port is not None and not 1 <= port <= 65535)):
                raise ValueError("Invalid source URL")
        except ValueError:
            raise ValueError("Use an absolute HTTP(S) URL without credentials") from None
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("Use an absolute HTTP(S) URL")
        return value


class AdminContentCreateRequest(AdminInputModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=SUMMARY_LIMIT)
    body: str | None = Field(default=None, max_length=BODY_LIMIT)
    sources: Annotated[list[AdminContentSourceInput], Field(max_length=SOURCE_LIMIT)] = Field(default_factory=list)

    @field_validator("title", "summary", "body", mode="before")
    @classmethod
    def clean(cls, value):
        return clean_text(value)

    @field_validator("summary", "body")
    @classmethod
    def empty_optional(cls, value):
        return value or None

    @model_validator(mode="after")
    def unique_sources(self):
        # Same exact-URL uniqueness as ContentSource, no Worker canonicalization.
        if len({source.url for source in self.sources}) != len(self.sources):
            raise ValueError("Duplicate source URL")
        return self


class AdminContentUpdateRequest(AdminContentCreateRequest):
    """Full author-field input, not PATCH; omission must never erase a field."""

    summary: str | None = Field(max_length=SUMMARY_LIMIT)
    body: str | None = Field(max_length=BODY_LIMIT)
    sources: Annotated[list[AdminContentSourceInput], Field(max_length=SOURCE_LIMIT)]
