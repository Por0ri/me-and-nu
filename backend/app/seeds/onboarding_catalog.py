"""Import a reviewed onboarding catalog; no external service is contacted.

Preview is the default. Select the database explicitly with --expected-database;
--apply commits the whole document atomically. The bundled synthetic example
additionally requires --demo and a local development database.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from ipaddress import ip_address
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Entity, EntityExternalId, EntityTag, PreferenceTag, Topic


DEMO_CATALOG = Path(__file__).with_name("onboarding_catalog_demo.json")
_KOREAN = re.compile(r"[가-힣]")


class CatalogError(ValueError):
    """A safe validation or conflict error, without database credentials."""


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False, str_strip_whitespace=True)


class TagReference(CatalogModel):
    kind: Literal["genre", "theme"]
    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.code


class CatalogTag(TagReference):
    display_name: str = Field(alias="displayName", min_length=1, max_length=100)
    sort_order: StrictInt = Field(default=0, alias="sortOrder", ge=0, le=2147483647)
    is_active: StrictBool = Field(default=True, alias="isActive")

    @field_validator("display_name")
    @classmethod
    def korean_label(cls, value: str) -> str:
        if not _KOREAN.search(value):
            raise ValueError("displayName must contain a reviewed Korean label")
        return value


class CatalogWork(CatalogModel):
    provider: str = Field(min_length=1, max_length=30, pattern=r"^[a-z][a-z0-9_-]*$")
    external_id: str = Field(alias="externalId", min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list)
    image_url: AnyHttpUrl | None = Field(default=None, alias="imageUrl")
    release_year: StrictInt | None = Field(default=None, alias="releaseYear", ge=1, le=9999)
    media_format: str | None = Field(default=None, alias="mediaFormat", min_length=1, max_length=30)
    is_active: StrictBool = Field(default=True, alias="isActive")
    tags: list[TagReference]

    @field_validator("name")
    @classmethod
    def korean_name(cls, value: str) -> str:
        if not _KOREAN.search(value):
            raise ValueError("name must contain a reviewed Korean title")
        return value

    @field_validator("aliases")
    @classmethod
    def unique_aliases(cls, values: list[str]) -> list[str]:
        aliases = [value.strip() for value in values]
        if any(not value or len(value) > 200 for value in aliases):
            raise ValueError("aliases must be nonempty strings of at most 200 characters")
        if len(aliases) != len(set(aliases)):
            raise ValueError("aliases must not contain duplicates")
        return aliases

    @field_validator("image_url")
    @classmethod
    def public_image_reference(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and (value.username or value.password or len(str(value)) > 1000):
            raise ValueError("imageUrl must not contain credentials and must fit 1000 characters")
        return value

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, values: list[TagReference]) -> list[TagReference]:
        if len(values) != len({value.key for value in values}):
            raise ValueError("work tags must not contain duplicates")
        return values

    @property
    def key(self) -> tuple[str, str]:
        return self.provider, self.external_id


class CatalogDocument(CatalogModel):
    topic_code: str = Field(alias="topicCode", min_length=1, max_length=50)
    demo: StrictBool = False
    tags: list[CatalogTag] = Field(min_length=1)
    works: list[CatalogWork] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> CatalogDocument:
        tag_keys = {tag.key for tag in self.tags}
        if len(tag_keys) != len(self.tags):
            raise ValueError("catalog tags must have unique (kind, code) keys")
        if len({work.key for work in self.works}) != len(self.works):
            raise ValueError("works must have unique (provider, externalId) keys")
        for work in self.works:
            if {tag.key for tag in work.tags} - tag_keys:
                raise ValueError("every work tag must be declared in this document")
        if self.demo:
            if any(work.provider != "demo" or not work.name.startswith("[DEMO]") for work in self.works):
                raise ValueError("demo works must use provider=demo and a [DEMO] title")
        elif any(work.provider == "demo" for work in self.works):
            raise ValueError("provider=demo requires demo=true")
        return self


def load_catalog(path: Path) -> CatalogDocument:
    try:
        return CatalogDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        raise CatalogError("Catalog file could not be read as UTF-8.") from None
    except ValidationError as exc:
        # Show field locations, not potentially private file values or URLs.
        fields = ", ".join(".".join(map(str, error["loc"])) or "document" for error in exc.errors())
        raise CatalogError(f"Invalid catalog document; check: {fields}.") from None


def validate_target(settings, *, expected_database: str, document: CatalogDocument, demo: bool) -> None:
    try:
        target = make_url(settings.database_url)
    except (ValueError, TypeError, SQLAlchemyError):
        raise CatalogError("Invalid database configuration.") from None
    if (not expected_database or target.database != expected_database
            or target.get_backend_name() != "postgresql"
            or expected_database in {"postgres", "template0", "template1"}):
        raise CatalogError("A PostgreSQL database matching --expected-database is required.")
    if demo != document.demo:
        raise CatalogError("The --demo flag must exactly match the document's demo marker.")
    if demo:
        try:
            local = target.host == "localhost" or ip_address(target.host or "").is_loopback
        except ValueError:
            local = False
        if not local or target.query or not settings.enable_dev_api:
            raise CatalogError("Demo catalog requires a loopback DB without URL overrides and ENABLE_DEV_API=true.")


def _tag_values(tag: CatalogTag) -> dict:
    return {"display_name": tag.display_name, "sort_order": tag.sort_order, "is_active": tag.is_active}


def _work_values(work: CatalogWork) -> dict:
    return {"name": work.name, "aliases": list(work.aliases),
            "image_url": str(work.image_url) if work.image_url is not None else None,
            "release_year": work.release_year, "media_format": work.media_format,
            "is_active": work.is_active}


@dataclass
class WorkPlan:
    source: CatalogWork
    entity: Entity | None
    current_tags: set[int]


async def seed_catalog(db, document: CatalogDocument, *, apply: bool = False) -> dict:
    """Preview or upsert a complete reviewed document; caller owns the transaction.

    Works are identified by provider/externalId, never by title. Each included
    work's tag list replaces its complete mapping; omitted works/tags are kept.
    This function never creates users, consent, subscriptions or answer records.
    """
    topic = await db.scalar(select(Topic).where(Topic.topic_code == document.topic_code))
    if topic is None or not topic.is_active:
        raise CatalogError("An existing active topic matching topicCode is required.")
    tag_rows = {}
    for tag in document.tags:
        tag_rows[tag.key] = await db.scalar(select(PreferenceTag).where(
            PreferenceTag.topic_id == topic.topic_id,
            PreferenceTag.kind == tag.kind, PreferenceTag.code == tag.code,
        ))
    plans = []
    resolved_entities = set()
    for work in document.works:
        mapping = await db.scalar(select(EntityExternalId).where(
            EntityExternalId.provider == work.provider, EntityExternalId.external_id == work.external_id,
        ))
        entity = None if mapping is None else await db.get(Entity, mapping.entity_id)
        if mapping is not None and (entity is None or entity.topic_id != topic.topic_id or entity.entity_type != "anime"):
            raise CatalogError("An external identity belongs to another topic or incompatible entity.")
        if entity is not None:
            if entity.entity_id in resolved_entities:
                raise CatalogError("Each entity must appear once; choose one canonical external identity per work.")
            resolved_entities.add(entity.entity_id)
        current = set() if entity is None else set((await db.scalars(
            select(EntityTag.tag_id).where(EntityTag.entity_id == entity.entity_id)
        )).all())
        plans.append(WorkPlan(work, entity, current))
    if apply:
        # Completion reads Entity FOR SHARE before PreferenceTag FOR SHARE.
        # Acquire write locks in that same table order, sorted by stable IDs,
        # before assigning or flushing anything. Document order is not a lock
        # order: two overlapping catalogs can list their works/tags differently.
        entity_ids = sorted(resolved_entities)
        if entity_ids:
            locked_entities = {entity.entity_id: entity for entity in (await db.scalars(
                select(Entity).where(Entity.entity_id.in_(entity_ids))
                .order_by(Entity.entity_id).with_for_update()
                .execution_options(populate_existing=True)
            )).all()}
            if len(locked_entities) != len(entity_ids):
                raise CatalogError("An entity changed during planning; retry the catalog import.")
            for plan in plans:
                if plan.entity is not None:
                    plan.entity = locked_entities[plan.entity.entity_id]
                    if plan.entity.topic_id != topic.topic_id or plan.entity.entity_type != "anime":
                        raise CatalogError("An entity changed topic or type during planning.")
        tag_ids = sorted(row.tag_id for row in tag_rows.values() if row is not None)
        if tag_ids:
            locked_tags = {tag.tag_id: tag for tag in (await db.scalars(
                select(PreferenceTag).where(PreferenceTag.tag_id.in_(tag_ids))
                .order_by(PreferenceTag.tag_id).with_for_update()
                .execution_options(populate_existing=True)
            )).all()}
            if len(locked_tags) != len(tag_ids):
                raise CatalogError("A tag changed during planning; retry the catalog import.")
            for key, row in tag_rows.items():
                if row is not None:
                    refreshed = locked_tags[row.tag_id]
                    if refreshed.topic_id != topic.topic_id or (refreshed.kind, refreshed.code) != key:
                        raise CatalogError("A tag changed identity during planning.")
                    tag_rows[key] = refreshed
        # Metadata and mappings may have changed while a row lock was awaited.
        for plan in plans:
            if plan.entity is not None:
                plan.current_tags = set((await db.scalars(
                    select(EntityTag.tag_id).where(EntityTag.entity_id == plan.entity.entity_id)
                )).all())
    report = {"mode": "apply" if apply else "dry_run", "demo": document.demo,
              "topic_id": topic.topic_id, "new_tags": 0, "updated_tags": 0,
              "new_works": 0, "updated_works": 0, "added_links": 0, "removed_links": 0,
              "works": []}
    for tag in document.tags:
        row = tag_rows[tag.key]
        report["new_tags"] += row is None
        report["updated_tags"] += row is not None and any(getattr(row, k) != v for k, v in _tag_values(tag).items())
    for plan in plans:
        report["new_works"] += plan.entity is None
        report["updated_works"] += plan.entity is not None and any(
            getattr(plan.entity, k) != v for k, v in _work_values(plan.source).items()
        )
        existing_desired = {tag_rows[ref.key].tag_id for ref in plan.source.tags if tag_rows[ref.key] is not None}
        report["added_links"] += len(plan.source.tags) - len(plan.current_tags & existing_desired)
        report["removed_links"] += len(plan.current_tags - existing_desired)
    if apply:
        for tag in document.tags:
            row = tag_rows[tag.key]
            if row is None:
                row = PreferenceTag(topic_id=topic.topic_id, kind=tag.kind, code=tag.code, **_tag_values(tag))
                db.add(row)
                tag_rows[tag.key] = row
            else:
                for key, value in _tag_values(tag).items():
                    setattr(row, key, value)
        await db.flush()
        for plan in plans:
            if plan.entity is None:
                plan.entity = Entity(topic_id=topic.topic_id, entity_type="anime", **_work_values(plan.source))
                db.add(plan.entity)
                await db.flush()
                db.add(EntityExternalId(entity_id=plan.entity.entity_id, provider=plan.source.provider,
                                        external_id=plan.source.external_id))
            else:
                for key, value in _work_values(plan.source).items():
                    setattr(plan.entity, key, value)
            desired = {tag_rows[ref.key].tag_id for ref in plan.source.tags}
            stale = plan.current_tags - desired
            if stale:
                await db.execute(delete(EntityTag).where(
                    EntityTag.entity_id == plan.entity.entity_id, EntityTag.tag_id.in_(stale),
                ))
            for tag_id in sorted(desired - plan.current_tags):
                db.add(EntityTag(entity_id=plan.entity.entity_id, tag_id=tag_id, topic_id=topic.topic_id))
        await db.flush()
    report["works"] = [{"provider": plan.source.provider, "external_id": plan.source.external_id,
                        "entity_id": plan.entity.entity_id if plan.entity else None} for plan in plans]
    return report


async def run(*, document: CatalogDocument, expected_database: str, demo: bool, apply: bool,
              database_url: str | None = None, development: bool = False, settings=None) -> dict:
    if database_url is not None:
        if settings is not None:
            raise CatalogError("Choose an explicit database URL or settings, not both.")
        # Explicit targets do not load application settings or an environment file.
        settings = SimpleNamespace(database_url=database_url, enable_dev_api=development)
    elif development:
        raise CatalogError("--development requires an explicit --database-url.")
    elif settings is None:
        from app.core.config import settings
    validate_target(settings, expected_database=expected_database, document=document, demo=demo)
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            if apply:
                await db.execute(text("SELECT pg_advisory_xact_lock(20261028, 1)"))
            else:
                await db.execute(text("SET TRANSACTION READ ONLY"))
            return await seed_catalog(db, document, apply=apply)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True, help="Reviewed JSON catalog; no automatic downloads.")
    parser.add_argument("--expected-database", required=True, help="Exact target database name, never a URL.")
    parser.add_argument("--database-url", help="Explicit database URL instead of application configuration; never printed.")
    parser.add_argument("--development", action="store_true", help="Declare an explicit URL as local development; required with --demo and --database-url.")
    parser.add_argument("--demo", action="store_true", help="Explicitly allow marked synthetic fixtures on local dev only.")
    parser.add_argument("--apply", action="store_true", help="Commit all catalog changes atomically; default is read-only.")
    args = parser.parse_args()
    try:
        document = load_catalog(args.file)
        task = run(document=document, expected_database=args.expected_database, demo=args.demo, apply=args.apply,
                   database_url=args.database_url, development=args.development)
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                report = runner.run(task)
        else:
            report = asyncio.run(task)
    except CatalogError as exc:
        parser.exit(1, f"Catalog import stopped: {exc}\n")
    except (ValueError, SQLAlchemyError):
        parser.exit(1, "Catalog import failed; no changes were committed. Check configuration and migrations.\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
