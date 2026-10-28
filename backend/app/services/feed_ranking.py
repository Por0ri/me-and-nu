"""Order one Tap's home feed from the day's recommendation (EXP-30 visit rules).

The model ranks up to 100 contents once a day. At each visit the feed keeps
that order, continues with the remaining public contents newest first, drops
contents the user already opened or marked "그만볼래요", and moves everything
already shown today to the back. A card feed shows only a few cards per visit,
so a card whose main work repeats the previous card's yields to the next card
about another work. Callers pass one snapshot time for every page so the order
does not shift while the user scrolls.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FeedSlot:
    content_id: int
    recommendation_item_id: int | None = None
    reason: str | None = None


def spread_works(
    slots: list[FeedSlot], main_work: dict[int, int], *, previous: int | None = None
) -> list[FeedSlot]:
    """Avoid the same main work twice in a row when another card can go between.

    A card without a main work never counts as a repeat. When every remaining
    card shares the previous work, the order is kept.
    """
    result, pending = [], list(slots)
    while pending:
        pick = next(
            (index for index, slot in enumerate(pending)
             if previous is None or main_work.get(slot.content_id) != previous),
            0,
        )
        slot = pending.pop(pick)
        result.append(slot)
        previous = main_work.get(slot.content_id)
    return result


def order_feed(
    ranked: list[FeedSlot],
    latest: list[int],
    *,
    opened: set[int],
    negative: set[int],
    shown_today: set[int],
    main_work: dict[int, int] | None = None,
) -> list[FeedSlot]:
    in_ranked = {slot.content_id for slot in ranked}
    hidden = opened | negative
    candidates = ranked + [FeedSlot(content_id) for content_id in latest if content_id not in in_ranked]
    candidates = [slot for slot in candidates if slot.content_id not in hidden]
    works = main_work or {}
    # Shown cards stay behind fresh ones; spreading never pulls one forward.
    fresh = spread_works([slot for slot in candidates if slot.content_id not in shown_today], works)
    shown = spread_works(
        [slot for slot in candidates if slot.content_id in shown_today], works,
        previous=works.get(fresh[-1].content_id) if fresh else None,
    )
    return fresh + shown
