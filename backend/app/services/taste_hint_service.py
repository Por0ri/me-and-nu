"""Hints above the My Page taste edit screens (Q1 · Q2 · Q3): what the member reacted to lately.

Only posts the member reacted to within HINT_DAYS count. A post that was only shown is what we chose
to show, not what they liked, and a "그만볼래요" post never counts. A post scores the way the
recommender learns from it (model/algo/content_v1.SIGNAL, card swipes as agreed 2026-11-03): its best
read (part of the cards 0.2, every card or 80% of the body 0.8, 30-80% of the body 0.3) plus like 0.8,
save 1.0 and "더볼래요" 1.0. The scores then add up per main work (Q1), per genre and theme chip of
that work, split evenly so a work with many chips counts no more (Q2), and per Q3 answer of the
post's type (Q3). Fewer than MIN_CONTENTS reacted posts gives no hints.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Content, ContentEntityLink, ContentReaction, Entity, EntityTag, PreferenceTag, SavedItem, UserEvent
from app.schemas.taste import TasteHintTag, TasteHintType, TasteHintWork, TasteHintsResponse
from app.services.taste_service import _tap

HINT_DAYS = 30
MIN_CONTENTS = 1   # from the first reacted post, so user testers with few records see hints too (2026-11-04, was 3)
TOP = 3
# Same strengths as the recommender (model/algo/content_v1.SIGNAL; tests/test_taste_hints.py keeps them equal).
SIGNAL = {"like": 0.8, "save": 1.0, "more": 1.0, "read_full": 0.8, "read_some": 0.3, "swipe_some": 0.2}
READ_FULL, READ_SOME = 0.8, 0.3   # read ratio: from 0.8 a full read; under 0.3 a bounce, which scores nothing here
# Q3 answer → post types, as model/algo/service.ANSWER_TYPES
ANSWER_TYPES = {
    "world": ("guide", "review"), "relationships": ("character", "people"), "ost": ("music",),
    "goods": ("goods",), "events": ("event",), "news": ("news", "curation", "humor", "behind"),
}
_ANSWER_OF = {kind: answer for answer, kinds in ANSWER_TYPES.items() for kind in kinds}


def _utc(moment: datetime | None) -> datetime | None:
    if moment is not None and moment.tzinfo is None:    # SQLite fixtures lose timezone metadata
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def read_score(kind: str, value: dict) -> float:
    """One card swipe or detail read → its score (0 for a bounce or an unusable value)."""
    if kind == "card_swipe":
        index, count = value.get("card_index"), value.get("card_count")
        if not index or not count:
            return 0.0
        return SIGNAL["read_full"] if index >= count else SIGNAL["swipe_some"]
    pct = value.get("scroll_pct")
    if pct is None:
        return 0.0
    ratio = pct / 100
    return SIGNAL["read_full"] if ratio >= READ_FULL else SIGNAL["read_some"] if ratio >= READ_SOME else 0.0


def percents(scores: dict, order: list) -> dict:
    """Shares of the total as whole percents that add up to exactly 100: the largest remainders round up,
    and equal ones in `order` (the order they are shown in), so a tie never shows 16% before 17%."""
    total = sum(scores.values())
    if total <= 0:
        return {key: 0 for key in scores}
    raw = {key: value * 100 / total for key, value in scores.items()}
    result = {key: int(share) for key, share in raw.items()}
    for key in sorted(raw, key=lambda k: (result[k] - raw[k], order.index(k)))[:100 - sum(result.values())]:
        result[key] += 1
    return result


async def _reacted_scores(db: AsyncSession, tap_id: int, since: datetime) -> dict[int, float]:
    """Content id → score of the posts reacted to since `since`; "그만볼래요" posts are left out."""
    score, disliked = defaultdict(float), set()
    events = await db.execute(
        select(UserEvent.content_id, UserEvent.event_type, UserEvent.event_value, UserEvent.occurred_at).where(
            UserEvent.tap_id == tap_id, UserEvent.event_type.in_(("open", "card_swipe")),
            UserEvent.data_source != "automated_test",
        )
    )
    for content_id, kind, value, at in events.all():
        if _utc(at) >= since:
            score[content_id] = max(score[content_id], read_score(kind, value or {}))
    reactions = await db.execute(
        select(ContentReaction.content_id, ContentReaction.is_liked, ContentReaction.liked_at,
               ContentReaction.ox_feedback, ContentReaction.updated_at).where(ContentReaction.tap_id == tap_id)
    )
    for content_id, liked, liked_at, ox, updated_at in reactions.all():
        if ox == "X":
            disliked.add(content_id)
        if liked and _utc(liked_at or updated_at) >= since:
            score[content_id] += SIGNAL["like"]
        if ox == "O" and _utc(updated_at) >= since:
            score[content_id] += SIGNAL["more"]
    saves = await db.execute(
        select(SavedItem.content_id, SavedItem.saved_at).where(
            SavedItem.tap_id == tap_id, SavedItem.status.in_(("kept", "resurface_off")))
    )
    for content_id, saved_at in saves.all():
        if _utc(saved_at) >= since:
            score[content_id] += SIGNAL["save"]
    return {content_id: value for content_id, value in score.items() if value > 0 and content_id not in disliked}


async def get_taste_hints(db: AsyncSession, user_id: int, topic_id: int, *, now: datetime | None = None) -> TasteHintsResponse:
    tap = await _tap(db, user_id, topic_id)
    scores = await _reacted_scores(db, tap.tap_id, (now or datetime.now(timezone.utc)) - timedelta(days=HINT_DAYS))
    types = dict((await db.execute(
        select(Content.content_id, Content.content_type).where(Content.content_id.in_(list(scores)), Content.topic_id == topic_id)
    )).all()) if scores else {}
    reacted = {content_id: value for content_id, value in scores.items() if content_id in types}
    if len(reacted) < MIN_CONTENTS:
        return TasteHintsResponse(topic_id=topic_id, days=HINT_DAYS, contents=len(reacted), enough=False,
                                  works=[], genres=[], themes=[], types=[])

    # Main work = the first linked work, as the recommender uses it.
    main: dict[int, int] = {}
    links = await db.execute(
        select(ContentEntityLink.content_id, ContentEntityLink.entity_id).where(ContentEntityLink.content_id.in_(list(reacted)))
        .order_by(ContentEntityLink.content_id, ContentEntityLink.sort_order)
    )
    for content_id, entity_id in links.all():
        main.setdefault(content_id, entity_id)
    work_ids = set(main.values())

    # Q1: only works that can be picked again (active), shares among them
    works = {row.entity_id: row for row in (await db.execute(
        select(Entity.entity_id, Entity.name, Entity.is_active).where(Entity.entity_id.in_(work_ids))
    )).all()} if work_ids else {}
    work_score = defaultdict(float)
    for content_id, value in reacted.items():
        work = works.get(main.get(content_id))
        if work is not None and work.is_active:
            work_score[work.entity_id] += value
    ranked_works = sorted(work_score, key=lambda w: (-work_score[w], w))
    work_pct = percents(work_score, ranked_works)
    top_works = ranked_works[:TOP]

    # Q2: a post's score is split evenly over its work's genres, and over its themes
    chips: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    rows = await db.execute(
        select(EntityTag.entity_id, PreferenceTag.tag_id, PreferenceTag.kind, PreferenceTag.display_name, PreferenceTag.sort_order)
        .join(PreferenceTag, PreferenceTag.tag_id == EntityTag.tag_id)
        .where(EntityTag.entity_id.in_(work_ids), PreferenceTag.is_active.is_(True), PreferenceTag.kind.in_(("genre", "theme")))
    ) if work_ids else None
    for entity_id, tag_id, kind, name, order in (rows.all() if rows is not None else []):
        chips[entity_id][kind].append((tag_id, name, order))
    tag_score, tag_info = defaultdict(float), {}
    for content_id, value in reacted.items():
        for kind, tags in chips.get(main.get(content_id), {}).items():
            for tag_id, name, order in tags:
                tag_score[tag_id] += value / len(tags)
                tag_info[tag_id] = (kind, name, order)

    def top_tags(kind: str) -> list[TasteHintTag]:
        ranked = sorted((t for t in tag_score if tag_info[t][0] == kind), key=lambda t: (-tag_score[t], tag_info[t][2], t))
        return [TasteHintTag(tag_id=t, name=tag_info[t][1]) for t in ranked[:TOP]]

    # Q3: all six answers, adding up to 100 ("더보기" shows them all)
    type_score = {answer: 0.0 for answer in ANSWER_TYPES}
    for content_id, value in reacted.items():
        answer = _ANSWER_OF.get(types[content_id])
        if answer is not None:
            type_score[answer] += value
    order = list(ANSWER_TYPES)
    ranked_types = sorted(type_score, key=lambda a: (-type_score[a], order.index(a)))
    type_pct = percents(type_score, ranked_types)
    return TasteHintsResponse(
        topic_id=topic_id, days=HINT_DAYS, contents=len(reacted), enough=True,
        works=[TasteHintWork(entity_id=w, name=works[w].name, percent=work_pct[w]) for w in top_works],
        genres=top_tags("genre"), themes=top_tags("theme"),
        types=[TasteHintType(answer=a, percent=type_pct[a]) for a in ranked_types],
    )
