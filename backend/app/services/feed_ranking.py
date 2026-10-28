"""Order one Tap's home feed from the day's recommendation (EXP-30 visit rules).

The model ranks up to 100 contents once a day. At each visit the feed keeps
that order, continues with the remaining public contents newest first, drops
contents the user already opened or marked "그만볼래요", and moves everything
already shown today to the back. Callers pass one snapshot time for every page
so the order does not shift while the user scrolls.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FeedSlot:
    content_id: int
    recommendation_item_id: int | None = None
    reason: str | None = None


def order_feed(
    ranked: list[FeedSlot],
    latest: list[int],
    *,
    opened: set[int],
    negative: set[int],
    shown_today: set[int],
) -> list[FeedSlot]:
    in_ranked = {slot.content_id for slot in ranked}
    candidates = ranked + [FeedSlot(content_id) for content_id in latest if content_id not in in_ranked]
    candidates = [slot for slot in candidates if slot.content_id not in opened | negative]
    return (
        [slot for slot in candidates if slot.content_id not in shown_today]
        + [slot for slot in candidates if slot.content_id in shown_today]
    )
