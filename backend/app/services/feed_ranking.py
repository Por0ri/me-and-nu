"""한 Tap 의 홈 피드 순서 (EXP-30 방문 규칙 · 11/25 하루 300장).

모델이 하루 한 번 글을 300장까지 줄 세운다. 방문할 때마다 그 순서를 지키고, 그 뒤에 다른 글을 잇지 않는다.
추천이 없는 날은 비상 목록 (최신 글을 Q3 묶음이 돌아가며 나오게 섞은 300장) 을 같은 규칙으로 쓴다.
14일 안에 끝까지 본 글과 "그만볼래요" 글은 빼고, 오늘 본 카드는 맨 뒤로 보낸다. 카드 피드는 방문마다
몇 장만 보이니까, 대표 작품이 바로 앞 카드와 같은 카드는 다른 작품 카드에 자리를 내준다. 순서는 첫 쪽에서
한 번 정해 cursor 에 담으니까, 스크롤하는 동안 바뀌지 않는다.
"""

from collections import deque
from dataclasses import dataclass

from app.services.taste_hint_service import ANSWER_TYPES

# 글 유형 → Q3 답 묶음. 모델 algo/service.GROUP_OF (같은 유형 연속 금지에 쓰는 표) 와 같음
GROUP_OF = {kind: answer for answer, kinds in ANSWER_TYPES.items() for kind in kinds}


@dataclass(frozen=True)
class FeedSlot:
    content_id: int
    recommendation_item_id: int | None = None
    reason: str | None = None


def mix_groups(contents: list[tuple[int, str | None]], limit: int) -> list[int]:
    """비상 목록 순서: 최신순 (content_id, 글 유형) 을 Q3 묶음마다 나눠, 묶음을 돌아가며 한 장씩 limit 장까지.

    묶음 순서는 묶음의 가장 새 글 순서로 고정한다. 그래서 한 묶음만 남기 전까지는 같은 묶음 글이 이어지지 않고,
    한 묶음만 남으면 그 묶음을 최신순으로 잇는다 (모델 compose 와 같음). 표에 없는 유형은 그 유형 하나가 한 묶음,
    유형 없는 글끼리도 한 묶음.
    """
    queues: dict[str | None, deque[int]] = {}
    for content_id, kind in contents:
        queues.setdefault(GROUP_OF.get(kind, kind), deque()).append(content_id)
    mixed: list[int] = []
    while queues and len(mixed) < limit:
        for group in list(queues):
            mixed.append(queues[group].popleft())
            if not queues[group]:
                del queues[group]
            if len(mixed) == limit:
                break
    return mixed


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
    fully_read: set[int],
    negative: set[int],
    shown_today: set[int],
    main_work: dict[int, int] | None = None,
) -> list[FeedSlot]:
    """ranked 뒤에 latest 를 이어 규칙을 적용한 순서. 피드는 추천이 있으면 ranked 만, 없으면 latest (비상 목록) 만 넘김."""
    in_ranked = {slot.content_id for slot in ranked}
    hidden = fully_read | negative
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
