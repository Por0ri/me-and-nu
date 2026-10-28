"""Link contents to the works they are about (content_entity_link).

Carousel packages carry menu_work_ids in package order (the first is the main
work). Links are filled from the stored draft, so contents imported before the
work catalog existed are linked once the catalog is loaded.
"""

from sqlalchemy import func, select

from app.models import Content, ContentEntityLink, Draft, Entity, EntityExternalId
from app.repositories.carousel_repository import CAROUSEL_METHOD


MENU_WORK_PROVIDER = "menu_work"


async def link_carousel_works(db) -> dict:
    """Add missing carousel links; caller owns the transaction.

    A work number that is unknown or belongs to another Topic is reported, never
    guessed. sort_order keeps the package position, so a missing main work stays
    visible as a gap instead of promoting a related work to main.
    """
    rows = (await db.execute(
        select(Content.content_id, Content.topic_id, Draft.structure_template)
        .join(Draft, Draft.draft_id == Content.source_draft_id)
        .where(func.coalesce(Content.ai_judgment_basis["method"].as_string(), "") == CAROUSEL_METHOD)
    )).all()
    wanted = {
        content_id: (topic_id, (template or {}).get("menu_work_ids") or [])
        for content_id, topic_id, template in rows
    }
    wanted = {content_id: value for content_id, value in wanted.items() if value[1]}
    report = {"links_added": 0, "contents_linked": 0, "order_conflicts": 0, "unresolved_menu_work_ids": []}
    if not wanted:
        return report

    numbers = sorted({str(menu_work_id) for _, ids in wanted.values() for menu_work_id in ids})
    entities = {
        external_id: (entity_id, topic_id)
        for external_id, entity_id, topic_id in (await db.execute(
            select(EntityExternalId.external_id, Entity.entity_id, Entity.topic_id)
            .join(Entity, Entity.entity_id == EntityExternalId.entity_id)
            .where(EntityExternalId.provider == MENU_WORK_PROVIDER, EntityExternalId.external_id.in_(numbers))
        )).all()
    }
    existing: dict[int, tuple[set[int], set[int]]] = {}
    for content_id, entity_id, sort_order in (await db.execute(
        select(ContentEntityLink.content_id, ContentEntityLink.entity_id, ContentEntityLink.sort_order)
        .where(ContentEntityLink.content_id.in_(list(wanted)))
    )).all():
        linked_entities, used_orders = existing.setdefault(content_id, (set(), set()))
        linked_entities.add(entity_id)
        used_orders.add(sort_order)

    linked_contents, unresolved = set(), set()
    for content_id, (topic_id, menu_work_ids) in sorted(wanted.items()):
        linked_entities, used_orders = existing.setdefault(content_id, (set(), set()))
        for sort_order, menu_work_id in enumerate(menu_work_ids, start=1):
            entity = entities.get(str(menu_work_id))
            if entity is None or entity[1] != topic_id:
                unresolved.add(menu_work_id)
                continue
            if entity[0] in linked_entities:
                continue
            if sort_order in used_orders:
                report["order_conflicts"] += 1
                continue
            db.add(ContentEntityLink(
                content_id=content_id, entity_id=entity[0], topic_id=topic_id,
                link_method="carousel", sort_order=sort_order,
            ))
            linked_entities.add(entity[0])
            used_orders.add(sort_order)
            linked_contents.add(content_id)
            report["links_added"] += 1
    if report["links_added"]:
        await db.flush()
    report["contents_linked"] = len(linked_contents)
    report["unresolved_menu_work_ids"] = sorted(unresolved)
    return report
