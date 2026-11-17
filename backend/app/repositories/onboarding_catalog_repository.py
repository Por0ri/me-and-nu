"""Read the curated onboarding catalogue; these operations never persist choices."""

from sqlalchemy import Boolean, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import GenericFunction

from app.models import Entity, EntityTag, PreferenceTag


class _AliasMatches(GenericFunction):
    type = Boolean()
    inherit_cache = True


def _alias_match_expression(element, *, sqlite=False):
    column, pattern = list(element.clauses)
    if sqlite:
        aliases = func.json_each(column).table_valued("value").alias("work_alias")
    else:
        # PostgreSQL's function output is otherwise named after the function,
        # whereas SQLite's json_each already exposes a column named value.
        aliases = func.jsonb_array_elements_text(column).table_valued("value").render_derived(name="work_alias")
    return exists(select(1).select_from(aliases).where(_compact(aliases.c.value).ilike(pattern, escape="\\")))


@compiles(_AliasMatches)
def _postgres_alias_match(element, compiler, **kwargs):
    return compiler.process(_alias_match_expression(element), **kwargs)


@compiles(_AliasMatches, "sqlite")
def _sqlite_alias_match(element, compiler, **kwargs):
    return compiler.process(_alias_match_expression(element, sqlite=True), **kwargs)


def _literal_pattern(query: str) -> str:
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _compact(column):
    # Spaces are ignored on both sides, so "장송의프리렌" finds "장송의 프리렌".
    return func.replace(column, " ", "")


async def search_works(
    db: AsyncSession, topic_id: int, *, query: str, after_id: int | None, limit: int,
) -> list[Entity]:
    compact = "".join(query.split())
    if not compact:
        return []
    pattern = _literal_pattern(compact)
    statement = select(Entity).where(
        Entity.topic_id == topic_id,
        Entity.entity_type == "anime",
        Entity.is_active.is_(True),
        or_(_compact(Entity.name).ilike(pattern, escape="\\"), _AliasMatches(Entity.aliases, pattern)),
    )
    if after_id is not None:
        statement = statement.where(Entity.entity_id > after_id)
    result = await db.execute(statement.order_by(Entity.entity_id).limit(limit))
    return list(result.scalars().all())


async def find_active_works(
    db: AsyncSession, topic_id: int, entity_ids: list[int], *, lock: bool = False,
) -> list[Entity]:
    statement = select(Entity).where(
        Entity.topic_id == topic_id,
        Entity.entity_id.in_(entity_ids),
        Entity.entity_type == "anime",
        Entity.is_active.is_(True),
    ).order_by(Entity.entity_id)
    if lock:
        statement = statement.with_for_update(read=True)
    return list((await db.execute(statement)).scalars().all())


async def list_active_tags(db: AsyncSession, topic_id: int) -> list[PreferenceTag]:
    """Every active genre/theme chip of the Topic in display order."""
    statement = select(PreferenceTag).where(
        PreferenceTag.topic_id == topic_id,
        PreferenceTag.is_active.is_(True),
        PreferenceTag.kind.in_(("genre", "theme")),
    ).order_by(PreferenceTag.kind, PreferenceTag.sort_order, PreferenceTag.tag_id)
    return list((await db.execute(statement)).scalars().all())


async def find_active_tags(
    db: AsyncSession, topic_id: int, tag_ids: list[int], *, lock: bool = False,
) -> list[PreferenceTag]:
    statement = select(PreferenceTag).where(
        PreferenceTag.topic_id == topic_id,
        PreferenceTag.tag_id.in_(tag_ids),
        PreferenceTag.is_active.is_(True),
        PreferenceTag.kind.in_(("genre", "theme")),
    ).order_by(PreferenceTag.tag_id)
    if lock:
        # Same order as the catalogue importers: entities first, then tags by ID.
        statement = statement.with_for_update(read=True)
    return list((await db.execute(statement)).scalars().all())


async def list_active_tag_links(
    db: AsyncSession, topic_id: int, entity_ids: list[int], *, lock: bool = False,
) -> list[tuple[EntityTag, PreferenceTag]]:
    statement = select(EntityTag, PreferenceTag).join(
        PreferenceTag,
        (PreferenceTag.tag_id == EntityTag.tag_id)
        & (PreferenceTag.topic_id == EntityTag.topic_id),
    ).where(
        EntityTag.topic_id == topic_id,
        EntityTag.entity_id.in_(entity_ids),
        PreferenceTag.is_active.is_(True),
        PreferenceTag.kind.in_(("genre", "theme")),
    )
    if lock:
        # The importer locks entities, then tags, in ascending ID order too.
        # Display order must not create an opposite lock order for shared tags.
        statement = statement.order_by(PreferenceTag.tag_id, EntityTag.entity_id).with_for_update(
            read=True, of=[EntityTag, PreferenceTag]
        )
    else:
        statement = statement.order_by(PreferenceTag.kind, PreferenceTag.sort_order, PreferenceTag.tag_id, EntityTag.entity_id)
    rows = list((await db.execute(statement)).all())
    if lock:
        rows.sort(key=lambda row: (row[1].kind, row[1].sort_order, row[1].tag_id, row[0].entity_id))
    return rows
