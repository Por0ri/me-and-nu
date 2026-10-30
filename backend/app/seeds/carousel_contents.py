"""Validate and import local carousel bundles; preview is read-only by default.

Producer scores are never publication approval. An optional, separately supplied
operator review record attests review of the exact digest; it is a trusted local
operational input, not cryptographic proof that a review took place.
Explicit test previews preserve unapproved status and original evidence. They
are visible only in a matching local test service or the opted-in Checking stack.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path, PureWindowsPath
import sys
from typing import Any, Literal
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, ValidationError, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AgentRun, Content, ContentSource, Draft, Topic
from app.core.carousel_preview import checking_preview_database
from app.models.content_slide import ContentSlide
from app.repositories.content_work_repository import link_carousel_works
from app.repositories.carousel_repository import local_test_database

METHOD = "carousel_import_v1"
MAX_PNG_BYTES = 20 * 1024 * 1024
MAX_BUNDLE_BYTES = 200 * 1024 * 1024


class CarouselImportError(ValueError):
    """A bounded, credential-free validation or conflict message."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=1000)
    role: Literal["primary", "citation", "material", "secondary", "reference", "editorial", "discovery", "trend"] = "primary"

    @field_validator("url")
    @classmethod
    def source_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or any(ord(c) < 32 for c in value)):
            raise ValueError("source URL must be HTTP(S) without credentials")
        return value


class FactModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=10000)
    source_ids: list[str] = Field(min_length=1, max_length=100)


class Box(StrictModel):
    x: StrictFloat = Field(ge=0, le=1)
    y: StrictFloat = Field(ge=0, le=1)
    width: StrictFloat = Field(gt=0, le=1)
    height: StrictFloat = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside_canvas(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("layout box exceeds canvas")
        return self


class Layout(StrictModel):
    template: str = Field(default="split", min_length=1, max_length=50)
    fit_mode: str = Field(default="contain_blur", min_length=1, max_length=50)
    background_style: Literal["blur", "palette", "gradient"] = "blur"
    image_box: Box
    copy_box: Box
    text_overlay_box: Box
    image_scale: StrictFloat = Field(default=1.0, ge=0.5, le=1.5)
    image_offset_y: StrictFloat = Field(default=0.0, ge=-0.3, le=0.3)
    contrast_mode: str = Field(default="scrim_required", min_length=1, max_length=50)
    blur_background: StrictBool = False


class CardModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    page: StrictInt = Field(ge=1, le=10)
    role: str = Field(min_length=1, max_length=50)
    eyebrow: str = Field(default="", max_length=300)
    title_lines: list[str] = Field(min_length=1, max_length=4)
    body_sentences: list[str] = Field(default_factory=list, max_length=4)
    fact_ids: list[str] = Field(default_factory=list, max_length=100)
    layout: Layout
    rendered_image_key: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("title_lines", "body_sentences")
    @classmethod
    def bounded_text(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 1000 for value in values):
            raise ValueError("card text must be nonempty and at most 1000 characters per line")
        return values


class PackageModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    request_id: str = Field(min_length=1, max_length=300)
    content_type: Literal["news", "event", "guide", "people", "goods", "behind", "review", "humor", "character", "curation", "music"]
    menu_work_ids: list[StrictInt] = Field(default_factory=list, max_length=100)
    feed_valid_until: str
    purge_at: str | None = None
    sources: list[SourceModel] = Field(min_length=1, max_length=100)
    facts: list[FactModel] = Field(min_length=1, max_length=100)
    cards: list[CardModel] = Field(min_length=3, max_length=10)
    detail_body: str = Field(min_length=1, max_length=10000)
    detail_fact_ids: list[str] = Field(min_length=1, max_length=100)
    detail_source_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("request_id", "detail_body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("request identity and detail body cannot be blank")
        return value

    @model_validator(mode="after")
    def references(self):
        source_ids = {source.id for source in self.sources}
        fact_ids = {fact.id for fact in self.facts}
        if (len(source_ids) != len(self.sources) or len(fact_ids) != len(self.facts)
                or len({source.url for source in self.sources}) != len(self.sources)):
            raise ValueError("source/fact identities and source URLs must be unique")
        if sorted(card.page for card in self.cards) != list(range(1, len(self.cards) + 1)):
            raise ValueError("cards must have unique contiguous pages starting at one")
        if len(self.menu_work_ids) != len(set(self.menu_work_ids)) or any(value < 1 for value in self.menu_work_ids):
            raise ValueError("menu_work_ids must be unique positive canonical IDs")
        if any(set(fact.source_ids) - source_ids for fact in self.facts):
            raise ValueError("unknown fact source reference")
        if any(set(card.fact_ids) - fact_ids for card in self.cards):
            raise ValueError("unknown card fact reference")
        if set(self.detail_fact_ids) - fact_ids or set(self.detail_source_ids) - source_ids:
            raise ValueError("unknown detail evidence reference")
        detail_sources = {source for fact in self.facts if fact.id in self.detail_fact_ids for source in fact.source_ids}
        if not detail_sources <= set(self.detail_source_ids):
            raise ValueError("detail sources must include all referenced fact sources")
        if len(" ".join(min(self.cards, key=lambda card: card.page).title_lines)) > 300:
            raise ValueError("cover title exceeds 300 characters")
        return self


class Asset(StrictModel):
    page: StrictInt = Field(ge=1, le=10)
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class BundleItem(StrictModel):
    package: dict[str, Any]
    assets: list[Asset] = Field(min_length=3, max_length=10)
    reviewEvidence: dict[str, Any] | None = None


class Bundle(StrictModel):
    schemaVersion: Literal[1]
    producer: Literal["ani_menu_agent"]
    topicCode: Literal["anime"]
    items: list[BundleItem] = Field(min_length=1, max_length=50)


class ReviewRecord(StrictModel):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approved", "rejected"]
    reviewedBy: str = Field(min_length=1, max_length=200)
    reviewedAt: str
    comment: str = Field(min_length=1, max_length=2000)

    @field_validator("reviewedBy", "comment")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review identity and comment cannot be blank")
        return value


class ReviewFile(StrictModel):
    schemaVersion: Literal[1]
    records: list[ReviewRecord] = Field(min_length=1, max_length=50)


def _timestamp(value: str, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, TypeError):
        raise CarouselImportError(f"{field} must include a valid timezone.") from None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _read_json(path: Path) -> dict:
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise CarouselImportError("JSON file exceeds 16 MiB.")
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (OSError, UnicodeError, ValueError):
        raise CarouselImportError("Could not read a bounded UTF-8 JSON document.") from None


def _png(root: Path, asset: Asset) -> bytes:
    relative = Path(asset.path)
    windows = PureWindowsPath(asset.path)
    if relative.is_absolute() or windows.drive or windows.root or ".." in relative.parts or ".." in windows.parts:
        raise CarouselImportError("PNG path must stay inside the bundle directory.")
    try:
        candidate = root / relative
        current = candidate
        while current != root:
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise CarouselImportError("PNG symlinks and junctions are not accepted.")
            current = current.parent
        if not candidate.resolve(strict=True).is_relative_to(root) or not candidate.is_file():
            raise CarouselImportError("PNG path must be a regular bundle file.")
        with candidate.open("rb") as handle:
            binary = handle.read(MAX_PNG_BYTES + 1)
        if len(binary) > MAX_PNG_BYTES:
            raise CarouselImportError("PNG exceeds 20 MiB.")
        if hashlib.sha256(binary).hexdigest() != asset.sha256:
            raise CarouselImportError("PNG digest does not match the manifest.")
        with Image.open(BytesIO(binary)) as image:
            if image.format != "PNG" or image.size != (1308, 1632) or getattr(image, "n_frames", 1) != 1:
                raise CarouselImportError("Each card must be a single 1308x1632 PNG.")
            image.verify()
        with Image.open(BytesIO(binary)) as image:
            image.load()
        return binary
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise CarouselImportError("PNG could not be safely decoded or validated.") from None


@dataclass(frozen=True)
class PreparedItem:
    raw: BundleItem
    package: PackageModel
    digest: str
    queue_task_id: str
    images: dict[int, bytes]
    expires_at: datetime


@dataclass(frozen=True)
class PreparedBundle:
    document: Bundle
    items: tuple[PreparedItem, ...]
    reviews: dict[str, ReviewRecord]


def load_bundle(path: Path, *, review_record_file: Path | None = None, now: datetime | None = None) -> PreparedBundle:
    """Read all files and validate the complete batch before database access."""
    moment = now or datetime.now(UTC)
    try:
        document = Bundle.model_validate(_read_json(path))
        prepared = []
        requests = set()
        total_bytes = 0
        for item in document.items:
            package = PackageModel.model_validate(item.package)
            if package.request_id in requests:
                raise CarouselImportError("Duplicate producer request in bundle.")
            requests.add(package.request_id)
            expires_at = _timestamp(package.feed_valid_until, "feed_valid_until")
            if expires_at <= moment:
                raise CarouselImportError("Expired content cannot be imported.")
            if package.purge_at and _timestamp(package.purge_at, "purge_at") < expires_at:
                raise CarouselImportError("purge_at cannot precede feed_valid_until.")
            assets = {asset.page: asset for asset in item.assets}
            if len(assets) != len(item.assets) or set(assets) != {card.page for card in package.cards}:
                raise CarouselImportError("PNG pages must match all carousel cards exactly.")
            images = {}
            for card in package.cards:
                asset = assets[card.page]
                if card.rendered_image_key != asset.sha256:
                    raise CarouselImportError("Card rendered key must match its PNG digest.")
                images[card.page] = _png(path.parent.resolve(), asset)
                total_bytes += len(images[card.page])
                if total_bytes > MAX_BUNDLE_BYTES:
                    raise CarouselImportError("Bundle PNG total exceeds 200 MiB.")
            digest = hashlib.sha256(_canonical({"producer": document.producer, "topicCode": document.topicCode,
                "package": item.package, "assets": [{"page": page, "sha256": assets[page].sha256} for page in sorted(assets)]})).hexdigest()
            identity = hashlib.sha256(_canonical([document.producer, package.request_id])).hexdigest()
            prepared.append(PreparedItem(item, package, digest, f"carousel-v1:{identity}", images, expires_at))
        reviews = {}
        if review_record_file is not None:
            records = ReviewFile.model_validate(_read_json(review_record_file)).records
            digests = {item.digest for item in prepared}
            for record in records:
                if record.digest not in digests or record.digest in reviews:
                    raise CarouselImportError("Review digest must match one unique bundle item.")
                if _timestamp(record.reviewedAt, "reviewedAt") > moment:
                    raise CarouselImportError("Review time cannot be in the future.")
                reviews[record.digest] = record
        return PreparedBundle(document, tuple(prepared), reviews)
    except ValidationError as exc:
        locations = ", ".join(".".join(map(str, item["loc"])) for item in exc.errors())
        raise CarouselImportError(f"Invalid bundle/review fields: {locations}.") from None


def _review_transition(previous: dict | None, incoming: ReviewRecord | None) -> tuple[dict | None, bool]:
    current = incoming.model_dump() if incoming else None
    if current is None or current == previous:
        return previous, False
    if previous and _timestamp(current["reviewedAt"], "reviewedAt") <= _timestamp(previous["reviewedAt"], "reviewedAt"):
        raise CarouselImportError("A review update must be later than the stored operator review.")
    return current, True


def _state(review: dict | None) -> tuple[str, str, str, str]:
    if review and review["decision"] == "approved":
        return "active", "confirmed", "published", "publish_candidate"
    if review:
        return "hidden", "failed", "discarded", "rejected_archive"
    return "pending_review", "needs_review", "checking", "on_hold"


def _sources(package: PackageModel) -> list[tuple[SourceModel, str, int]]:
    # Preserve explicit citations; other evidence roles are service material.
    ordered = sorted(package.sources, key=lambda source: source.role != "primary")
    return [(source, source.role if source.role in {"primary", "citation"} else "material", order)
            for order, source in enumerate(ordered, 1)]


def _cover_values(package: PackageModel) -> tuple[str, str]:
    cover = min(package.cards, key=lambda card: card.page)
    title = " ".join(cover.title_lines)
    return title, " ".join(cover.body_sentences) or title


async def _verify_stored_copy(db, item: PreparedItem, run, draft, content) -> None:
    """A review of an import digest must not approve subsequently edited DB rows."""
    package = item.package
    title, summary = _cover_values(package)
    image_url = f"/api/v1/contents/{content.content_id}/slides/1/image?topicId={content.topic_id}"
    expiry = content.feed_valid_until
    # SQLite fixtures lose timezone metadata; production PostgreSQL retains it.
    if expiry is not None and expiry.tzinfo is None and db.get_bind().dialect.name == "sqlite":
        expiry = expiry.replace(tzinfo=UTC)
    if ((run.output_payload or {}).get("package") != item.raw.package
            or run.agent_code != "carousel_import" or run.agent_version != "1"
            or content.topic_id != run.topic_id or draft.topic_id != run.topic_id
            or content.production_type != "ai" or draft.production_type != "ai"
            or content.source_draft_id != draft.draft_id or draft.agent_run_id != run.agent_run_id
            or (content.ai_judgment_basis or {}).get("method") != METHOD
            or (content.ai_judgment_basis or {}).get("digest") != item.digest
            or content.title != title or content.body != package.detail_body
            or content.summary != summary or content.image_url != image_url
            or content.content_type != package.content_type or expiry != item.expires_at
            or draft.title != title or draft.body != package.detail_body
            or content.status == "deleted" or content.deleted_at is not None):
        raise CarouselImportError("Stored import no longer matches its immutable reviewed copy.")
    rows = (await db.scalars(select(ContentSlide).where(ContentSlide.content_id == content.content_id))).all()
    by_page = {row.page: row for row in rows}
    if set(by_page) != {card.page for card in package.cards}:
        raise CarouselImportError("Stored carousel pages are incomplete or changed.")
    for card in package.cards:
        row = by_page[card.page]
        # Select bytes explicitly: image_data is deferred on normal API reads.
        binary = await db.scalar(select(ContentSlide.image_data).where(
            ContentSlide.content_id == content.content_id, ContentSlide.page == card.page))
        if (row.role != card.role or row.eyebrow != card.eyebrow or row.title_lines != card.title_lines
                or row.body_sentences != card.body_sentences or row.layout_json != card.layout.model_dump()
                or row.image_sha256 != card.rendered_image_key or row.image_width != 1308 or row.image_height != 1632
                or binary is None or hashlib.sha256(binary).hexdigest() != card.rendered_image_key):
            raise CarouselImportError("Stored carousel image or text no longer matches its digest.")
    sources = (await db.scalars(select(ContentSource).where(ContentSource.content_id == content.content_id))).all()
    expected = {(source.url, source.title, role, order) for source, role, order in _sources(package)}
    actual = {(source.source_url, source.source_title, source.source_role, source.citation_order) for source in sources}
    if actual != expected:
        raise CarouselImportError("Stored evidence sources no longer match their reviewed copy.")


async def import_bundle(db, bundle: PreparedBundle, *, apply: bool = False, now: datetime | None = None,
                        local_test_preview: bool = False, checking_test_preview: bool = False,
                        expected_database: str | None = None) -> dict:
    """Caller owns one transaction; no commit, download, producer import or DB access."""
    if local_test_preview and checking_test_preview:
        raise CarouselImportError("Choose exactly one test preview scope.")
    preview_scope = "checking_test_preview" if checking_test_preview else "local_test_preview" if local_test_preview else None
    preview_metadata = {"database": expected_database, "quality_approval": "not_reviewed"}
    if checking_test_preview:
        preview_metadata["environment"] = "checking"
    if preview_scope:
        validate_target(str(db.get_bind().url), expected_database or "",
                        local_test_preview=local_test_preview, checking_test_preview=checking_test_preview)
        if bundle.reviews:
            raise CarouselImportError("Test preview cannot be combined with operator review records.")
    moment = now or datetime.now(UTC)
    if apply and db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(20261028, 2)"))
    topic = await db.scalar(select(Topic).where(Topic.topic_code == bundle.document.topicCode))
    if topic is None or not topic.is_active:
        raise CarouselImportError("An existing active anime Topic is required.")
    plans = []
    for item in bundle.items:
        if item.expires_at <= moment:
            raise CarouselImportError("Content expired before import.")
        run = await db.scalar(select(AgentRun).where(AgentRun.queue_task_id == item.queue_task_id))
        draft = content = None
        previous_review = None
        if run:
            output = run.output_payload or {}
            if run.topic_id != topic.topic_id or output.get("method") != METHOD or output.get("digest") != item.digest:
                raise CarouselImportError("Existing producer request has a different immutable digest.")
            draft = await db.scalar(select(Draft).where(Draft.agent_run_id == run.agent_run_id))
            content = None if draft is None else await db.scalar(select(Content).where(Content.source_draft_id == draft.draft_id))
            if draft is None or content is None:
                raise CarouselImportError("Existing import is incomplete; reconcile it before retrying.")
            await _verify_stored_copy(db, item, run, draft, content)
            previous_basis = content.ai_judgment_basis or {}
            other_scopes = {"local_test_preview", "checking_test_preview"} - {preview_scope}
            if (preview_scope and (previous_basis.get("preview") is not True
                    or previous_basis.get(preview_scope) != preview_metadata
                    or any(previous_basis.get(scope) is not None for scope in other_scopes)
                    or previous_basis.get("operator_review") is not None)) or (
                    not preview_scope and (any(previous_basis.get(scope) is not None for scope in other_scopes)
                                           or previous_basis.get("preview") is True)):
                raise CarouselImportError("Existing import has a different publication scope; reconcile before changing it.")
            previous_review = (content.ai_judgment_basis or {}).get("operator_review")
        review, changed = _review_transition(previous_review, bundle.reviews.get(item.digest))
        plans.append((item, run, draft, content, review, changed))
    report = {"mode": "apply" if apply else "dry_run", "created": 0, "review_updated": 0, "unchanged": 0, "items": []}
    if preview_scope:
        report["publication_scope"] = preview_scope
        report["quality_approved"] = False
    for item, run, draft, content, review, changed in plans:
        status, judgment, draft_status, outcome = _state(review)
        if preview_scope:
            # Expose only in the explicit test service; this is not a QC approval.
            status, judgment, draft_status, outcome = "active", "needs_review", "checking", "on_hold"
        new = run is None
        report["created" if new else "review_updated" if changed else "unchanged"] += 1
        basis = {"method": METHOD, "digest": item.digest, "operator_review": review,
                 "producer_review_is_approval": False}
        if preview_scope:
            basis.update({"preview": True, preview_scope: preview_metadata})
        if apply and new:
            package = item.package
            run = AgentRun(topic_id=topic.topic_id, agent_code="carousel_import", agent_version="1",
                queue_task_id=item.queue_task_id, status="succeeded", outcome=outcome,
                input_payload={"producer": bundle.document.producer, "request_id": package.request_id},
                output_payload={"method": METHOD, "digest": item.digest, "package": item.raw.package,
                                "producer_review_evidence": item.raw.reviewEvidence},
                started_at=moment, finished_at=moment, node_count=0)
            db.add(run)
            await db.flush()
            title, summary = _cover_values(package)
            draft = Draft(agent_run_id=run.agent_run_id, topic_id=topic.topic_id, production_type="ai",
                content_type=package.content_type, title=title, body=package.detail_body,
                structure_template={"method": METHOD, "menu_work_ids": package.menu_work_ids},
                factcheck_result=basis, status=draft_status)
            db.add(draft)
            await db.flush()
            content = Content(source_draft_id=draft.draft_id, topic_id=topic.topic_id, production_type="ai",
                title=title, body=package.detail_body, summary=summary, content_type=package.content_type,
                status=status, judgment_status=judgment, ai_judgment_basis=basis,
                feed_valid_until=item.expires_at, published_at=moment if status == "active" else None)
            db.add(content)
            await db.flush()
            content.image_url = f"/api/v1/contents/{content.content_id}/slides/1/image?topicId={topic.topic_id}"
            for card in package.cards:
                db.add(ContentSlide(content_id=content.content_id, page=card.page, role=card.role,
                    eyebrow=card.eyebrow, title_lines=card.title_lines, body_sentences=card.body_sentences,
                    layout_json=card.layout.model_dump(), image_data=item.images[card.page],
                    image_sha256=card.rendered_image_key, image_width=1308, image_height=1632))
            for source, role, order in _sources(package):
                db.add(ContentSource(content_id=content.content_id, source_url=source.url,
                    source_title=source.title, source_role=role,
                    citation_order=order))
        elif apply and changed:
            run.outcome = outcome
            draft.status, draft.factcheck_result = draft_status, basis
            content.status, content.judgment_status, content.ai_judgment_basis = status, judgment, basis
            if status == "active" and content.published_at is None:
                content.published_at = moment
        report["items"].append({"request_id": item.package.request_id, "digest": item.digest,
            "content_id": content.content_id if content else None, "status": content.status if content and not changed else status})
    if apply:
        await db.flush()
    return report


def validate_target(database_url: str, expected_database: str, *, local_test_preview: bool = False,
                    checking_test_preview: bool = False) -> None:
    if local_test_preview and checking_test_preview:
        raise CarouselImportError("Choose exactly one test preview scope.")
    try:
        url = make_url(database_url)
    except (ValueError, TypeError, SQLAlchemyError):
        raise CarouselImportError("Invalid database configuration.") from None
    if (not expected_database or url.database != expected_database or url.get_backend_name() != "postgresql"
            or expected_database in {"postgres", "template0", "template1"}):
        raise CarouselImportError("An explicitly matching PostgreSQL database is required.")
    if local_test_preview and local_test_database(database_url) != expected_database:
        raise CarouselImportError("Local test preview requires an explicit loopback test PostgreSQL database without URL overrides.")
    if checking_test_preview:
        from app.core.config import settings
        if checking_preview_database(settings, database_url) != expected_database:
            raise CarouselImportError("Checking test preview requires explicit opt-in and the isolated Checking database and runtime.")


async def run(*, bundle: PreparedBundle, expected_database: str, apply: bool = False,
              database_url: str | None = None, local_test_preview: bool = False,
              checking_test_preview: bool = False) -> dict:
    if database_url is None:
        from app.core.config import settings
        database_url = settings.database_url
    validate_target(database_url, expected_database, local_test_preview=local_test_preview,
                    checking_test_preview=checking_test_preview)
    engine = create_async_engine(database_url, echo=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            if not apply:
                await db.execute(text("SET TRANSACTION READ ONLY"))
            report = await import_bundle(db, bundle, apply=apply, local_test_preview=local_test_preview,
                                         checking_test_preview=checking_test_preview,
                                         expected_database=expected_database)
            if apply:
                # Same transaction: work numbers that are not in the catalog yet are reported.
                report["work_links"] = await link_carousel_works(db)
            return report
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--review-record-file", type=Path, help="Separate operator review of exact digests; never inferred from producer scores.")
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--database-url", help="Explicit service DB URL; never printed.")
    parser.add_argument("--apply", action="store_true", help="Atomically persist; default is read-only preview.")
    preview_modes = parser.add_mutually_exclusive_group()
    preview_modes.add_argument("--local-test-preview", action="store_true",
                        help="Expose unapproved outputs only in a matching loopback test DB, visibly marked as preview.")
    preview_modes.add_argument("--checking-test-preview", action="store_true",
                        help="Expose unapproved outputs only in the opted-in, isolated Checking stack, visibly marked as preview.")
    args = parser.parse_args()
    try:
        bundle = load_bundle(args.file, review_record_file=args.review_record_file)
        task = run(bundle=bundle, expected_database=args.expected_database, apply=args.apply,
                   database_url=args.database_url, local_test_preview=args.local_test_preview,
                   checking_test_preview=args.checking_test_preview)
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                report = runner.run(task)
        else:
            report = asyncio.run(task)
    except CarouselImportError as exc:
        parser.exit(1, f"Carousel import stopped: {exc}\n")
    except (ValueError, SQLAlchemyError):
        parser.exit(1, "Carousel import failed; no changes were committed. Check configuration and migrations.\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
