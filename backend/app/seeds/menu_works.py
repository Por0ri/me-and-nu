"""Load the me;nu work catalog into the service work dictionary (entity).

model/catalog/menu_works.csv is the source of truth for work numbers and
titles; the model side maintains it. Works are matched by entity_external_id
(provider=menu_work), never by title. A work with a Korean title is active and
searchable in onboarding; the rest stay inactive so contents can still link to
them. Genre/theme mappings (entity_tag) and images are left untouched. After the
works, carousel contents get their missing content_entity_link rows.

Preview is the default. Select the database explicitly with --expected-database;
--apply commits everything atomically.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import sys

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Entity, EntityExternalId, Topic
from app.repositories.content_work_repository import MENU_WORK_PROVIDER, link_carousel_works


CATALOG_DIR = Path(__file__).resolve().parents[3] / "model" / "catalog"
TOPIC_CODE = "anime"


class MenuWorksError(ValueError):
    """A safe validation or conflict error, without database credentials."""


@dataclass(frozen=True)
class MenuWork:
    menu_work_id: int
    name: str
    aliases: tuple[str, ...]
    release_year: int | None
    media_format: str | None
    is_active: bool

    def values(self) -> dict:
        return {"name": self.name, "aliases": list(self.aliases), "release_year": self.release_year,
                "media_format": self.media_format, "is_active": self.is_active}


def _read(path: Path) -> list[dict[str, str]]:
    try:
        # Excel saves UTF-8 CSV with a byte order mark; utf-8-sig accepts both.
        with path.open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))
    except (OSError, UnicodeError, csv.Error):
        raise MenuWorksError(f"{path.name} could not be read as a UTF-8 CSV file.") from None


def _year(value: str) -> int | None:
    try:
        year = int(float(value))
    except ValueError:
        return None
    return year if 1 <= year <= 9999 else None


def load_works(works_path: Path, entries_path: Path) -> list[MenuWork]:
    formats: dict[str, set[str]] = {}
    for row in _read(entries_path):
        formats.setdefault((row.get("menu_work_id") or "").strip(), set()).add((row.get("format") or "").strip())
    works, seen = [], set()
    for line, row in enumerate(_read(works_path), start=2):
        number = (row.get("menu_work_id") or "").strip()
        if not number.isdigit() or int(number) < 1 or number in seen:
            raise MenuWorksError(f"{works_path.name} line {line}: menu_work_id must be a unique positive integer.")
        seen.add(number)
        korean = (row.get("title_ko") or "").strip()
        romaji, english, native = ((row.get(key) or "").strip() for key in ("title_romaji", "title_english", "title_native"))
        name = korean or english or romaji or native
        if not name or len(name) > 200:
            raise MenuWorksError(f"{works_path.name} line {line}: a title of at most 200 characters is required.")
        aliases = []
        # Search matches substrings, so a spaceless Korean title finds "진격의거인" too.
        for alias in (korean.replace(" ", ""), romaji, english, native):
            if alias and alias != name and alias not in aliases and len(alias) <= 200:
                aliases.append(alias)
        work_formats = formats.get(number, set()) - {""}
        media_format = None if not work_formats else "MOVIE" if work_formats == {"MOVIE"} else "SERIES"
        works.append(MenuWork(int(number), name, tuple(aliases), _year((row.get("first_year") or "").strip()),
                              media_format, bool(korean)))
    if not works:
        raise MenuWorksError(f"{works_path.name} has no works.")
    return works


async def seed_works(db, works: list[MenuWork], *, apply: bool = False) -> dict:
    """Preview or upsert every work; caller owns the transaction.

    Works omitted from the CSV are kept as they are; this never deletes or
    deactivates them, and never creates users, answers or selections.
    """
    topic = await db.scalar(select(Topic).where(Topic.topic_code == TOPIC_CODE))
    if topic is None or not topic.is_active:
        raise MenuWorksError("An existing active anime topic is required.")
    mapped = {external_id: entity_id for external_id, entity_id in (await db.execute(
        select(EntityExternalId.external_id, EntityExternalId.entity_id)
        .where(EntityExternalId.provider == MENU_WORK_PROVIDER)
    )).all()}
    entities = {}
    if mapped:
        statement = select(Entity).where(Entity.entity_id.in_(sorted(mapped.values())))
        if apply:
            # Same lock order as the onboarding catalog import: stable entity IDs.
            statement = statement.order_by(Entity.entity_id).with_for_update()
        entities = {entity.entity_id: entity for entity in (await db.scalars(statement)).all()}
    in_csv = {str(work.menu_work_id) for work in works}
    report = {"mode": "apply" if apply else "dry_run", "topic_id": topic.topic_id, "works": len(works),
              "created": 0, "updated": 0, "unchanged": 0,
              "active": sum(work.is_active for work in works),
              "inactive": sum(not work.is_active for work in works),
              "kept_not_in_csv": len(set(mapped) - in_csv)}
    created = []
    for work in works:
        number = str(work.menu_work_id)
        entity = entities.get(mapped.get(number))
        if number in mapped and (entity is None or entity.topic_id != topic.topic_id or entity.entity_type != "anime"):
            raise MenuWorksError(f"menu_work {number} belongs to another topic or an incompatible entity.")
        values = work.values()
        if entity is None:
            report["created"] += 1
            if apply:
                entity = Entity(topic_id=topic.topic_id, entity_type="anime", **values)
                db.add(entity)
                created.append((number, entity))
        elif any(getattr(entity, key) != value for key, value in values.items()):
            report["updated"] += 1
            if apply:
                for key, value in values.items():
                    setattr(entity, key, value)
        else:
            report["unchanged"] += 1
    if apply:
        await db.flush()
        for number, entity in created:
            db.add(EntityExternalId(provider=MENU_WORK_PROVIDER, external_id=number, entity_id=entity.entity_id))
        await db.flush()
    return report


def validate_target(database_url: str, expected_database: str) -> None:
    try:
        url = make_url(database_url)
    except (ValueError, TypeError, SQLAlchemyError):
        raise MenuWorksError("Invalid database configuration.") from None
    if (not expected_database or url.database != expected_database or url.get_backend_name() != "postgresql"
            or expected_database in {"postgres", "template0", "template1"}):
        raise MenuWorksError("An explicitly matching PostgreSQL database is required.")


async def run(*, works: list[MenuWork], expected_database: str, apply: bool = False,
              database_url: str | None = None) -> dict:
    if database_url is None:
        from app.core.config import settings
        database_url = settings.database_url
    validate_target(database_url, expected_database)
    engine = create_async_engine(database_url, echo=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            if apply:
                # Shares the onboarding catalog lock: both commands write entity rows.
                await db.execute(text("SELECT pg_advisory_xact_lock(20261028, 1)"))
            else:
                await db.execute(text("SET TRANSACTION READ ONLY"))
            report = await seed_works(db, works, apply=apply)
            if apply:
                report["work_links"] = await link_carousel_works(db)
            return report
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--works", type=Path, default=CATALOG_DIR / "menu_works.csv")
    parser.add_argument("--entries", type=Path, default=CATALOG_DIR / "menu_work_entries.csv",
                        help="Season/movie rows; used only to tell movies from series.")
    parser.add_argument("--expected-database", required=True, help="Exact target database name, never a URL.")
    parser.add_argument("--database-url", help="Explicit service DB URL; never printed.")
    parser.add_argument("--apply", action="store_true", help="Commit all changes atomically; default is read-only.")
    args = parser.parse_args()
    try:
        works = load_works(args.works, args.entries)
        task = run(works=works, expected_database=args.expected_database, apply=args.apply,
                   database_url=args.database_url)
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                report = runner.run(task)
        else:
            report = asyncio.run(task)
    except MenuWorksError as exc:
        parser.exit(1, f"Work catalog import stopped: {exc}\n")
    except (ValueError, SQLAlchemyError):
        parser.exit(1, "Work catalog import failed; no changes were committed. Check configuration and migrations.\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
