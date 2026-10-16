"""수집 출처 설정 모델."""

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class Source(BaseModel):
    """하나의 정보 출처."""

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    url: HttpUrl
    category: str
    role: Literal["primary", "discovery", "trend"]
    collection: Literal["api", "rss", "html", "youtube_rss", "manual"]
    status: Literal[
        "active",
        "blocked",
        "terms_review",
        "setup_required",
        "technical_review",
        "trend_only",
        "later",
    ]
    region: str
    notes: str = ""
    work_title_ko: str | None = None
    work_title_original: str | None = None
    allowed_asset_hosts: list[str] = Field(default_factory=list)
    feed_url: HttpUrl | None = None
    min_interval_seconds: float = Field(default=2.0, ge=0.0, le=3600.0)

    @model_validator(mode="after")
    def validate_status(self) -> "Source":
        if self.role == "trend" and self.status != "trend_only":
            raise ValueError("trend 출처는 trend_only 상태여야 합니다.")
        if self.status == "active" and self.collection == "manual":
            raise ValueError("active 출처에는 자동 수집 방식이 필요합니다.")
        return self


class SourceRegistry(BaseModel):
    """출처 설정 파일 전체."""

    version: int = Field(ge=1)
    sources: list[Source]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "SourceRegistry":
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("출처 id가 중복됐습니다.")
        return self

    @property
    def automatic_sources(self) -> list[Source]:
        """이용 조건 검토가 끝나 현재 자동 수집해도 되는 출처."""
        return [source for source in self.sources if source.status == "active"]

    @property
    def collectable_sources(self) -> list[Source]:
        """Active fact sources plus trend-only sources, which remain non-evidentiary."""
        return [
            source
            for source in self.sources
            if source.status == "active"
            or (source.role == "trend" and source.status == "trend_only")
        ]
