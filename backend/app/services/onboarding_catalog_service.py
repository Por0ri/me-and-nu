"""Search works, list the curated genre/theme chips and validate V2 selections.

Q2 offers every active chip of the Topic. Chips linked to the chosen works carry
their sourceEntityIds so the screen can suggest them first; any active chip may
be selected, up to seven genres and seven themes.
"""

from collections import Counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.repositories import onboarding_catalog_repository as repo
from app.repositories.onboarding_repository import find_active_topic
from app.schemas.onboarding import (
    OnboardingAnswersV2, OnboardingWork, OnboardingWorksResponse,
    PreferenceOption, PreferenceOptionsResponse,
)
from app.services.cursor import decode_cursor, encode_cursor

# EXP-33: more than seven chips per kind lowered the ranking score. Same value
# as PreferenceOptionsResponse.max_selections_per_kind.
MAX_TAGS_PER_KIND = 7


def invalid_selection(field: str, message: str) -> ApiError:
    return ApiError(422, "VALIDATION_ERROR", "입력값을 확인해 주세요.", fields=[{
        "field": field, "code": "VALIDATION_ERROR", "message": message,
    }])


async def _require_topic(db: AsyncSession, topic_id: int) -> None:
    if await find_active_topic(db, topic_id) is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.")


async def works(
    db: AsyncSession, topic_id: int, *, q: str | None, cursor: str | None, limit: int,
) -> OnboardingWorksResponse:
    await _require_topic(db, topic_id)
    query = q.strip() if q else ""
    context = {"v": 1, "resource": "onboarding-works", "topicId": topic_id, "q": query.lower()}
    after_id = None
    if cursor is not None:
        value = decode_cursor(cursor, expected=context).get("after")
        if type(value) is not int or not 0 < value <= 9223372036854775807:
            raise ApiError(422, "INVALID_CURSOR", "유효하지 않은 cursor입니다.")
        after_id = value
    if not query:
        return OnboardingWorksResponse(items=[])
    rows = await repo.search_works(db, topic_id, query=query, after_id=after_id, limit=limit + 1)
    page = rows[:limit]
    # Q2 no longer depends on the chosen works' chips, so every active work can be chosen.
    return OnboardingWorksResponse(
        items=[OnboardingWork(
            entity_id=row.entity_id, name=row.name, aliases=row.aliases,
            image_url=row.image_url, release_year=row.release_year,
            media_format=row.media_format, selectable=True,
        ) for row in page],
        next_cursor=encode_cursor({**context, "after": page[-1].entity_id}) if len(rows) > limit else None,
    )


async def _require_works(
    db: AsyncSession, topic_id: int, entity_ids: list[int], *, field: str, lock: bool = False,
) -> set[int]:
    selected = set(entity_ids)
    if not 1 <= len(entity_ids) <= 5 or len(selected) != len(entity_ids):
        raise invalid_selection(field, "서로 다른 작품을 1~5개 선택해 주세요.")
    entities = await repo.find_active_works(db, topic_id, entity_ids, lock=lock)
    if {entity.entity_id for entity in entities} != selected:
        raise invalid_selection(field, "선택할 수 없는 작품이 있습니다. 현재 작품 목록을 다시 확인해 주세요.")
    return selected


async def preference_options(
    db: AsyncSession, topic_id: int, entity_ids: list[int], *, field: str = "entityIds",
) -> PreferenceOptionsResponse:
    await _require_topic(db, topic_id)
    selected = await _require_works(db, topic_id, entity_ids, field=field)
    options = {
        tag.tag_id: PreferenceOption(
            tag_id=tag.tag_id, kind=tag.kind, code=tag.code, label=tag.display_name, source_entity_ids=[],
        )
        for tag in await repo.list_active_tags(db, topic_id)
    }
    for link, tag in await repo.list_active_tag_links(db, topic_id, entity_ids):
        options[tag.tag_id].source_entity_ids.append(link.entity_id)
    return PreferenceOptionsResponse(
        entity_ids=sorted(selected),
        genres=[option for option in options.values() if option.kind == "genre"],
        themes=[option for option in options.values() if option.kind == "theme"],
    )


async def validate_v2_selection(
    db: AsyncSession, topic_id: int, answers: OnboardingAnswersV2,
) -> None:
    # Shared locks last until the owning completion transaction commits. This
    # prevents a catalogue edit between validation and persistence; historical
    # preferences remain untouched by later catalogue edits.
    await _require_works(
        db, topic_id, answers.favorite_entity_ids, field="answers.favoriteEntityIds", lock=True,
    )
    tags = await repo.find_active_tags(db, topic_id, answers.preference_tag_ids, lock=True)
    if {tag.tag_id for tag in tags} != set(answers.preference_tag_ids):
        raise invalid_selection(
            "answers.preferenceTagIds",
            "현재 선택할 수 있는 장르·소재만 선택할 수 있습니다. 목록을 다시 확인해 주세요.",
        )
    if max(Counter(tag.kind for tag in tags).values()) > MAX_TAGS_PER_KIND:
        raise invalid_selection(
            "answers.preferenceTagIds", f"장르와 소재는 각각 {MAX_TAGS_PER_KIND}개까지 선택할 수 있습니다.",
        )
