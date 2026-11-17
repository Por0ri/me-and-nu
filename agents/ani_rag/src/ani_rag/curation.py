"""Conservative, source-grounded planning for cross-work editorial collections.

This module proposes curation angles from already-resolved entity facts. It does
not collect facts, mint IDs, rank opinions, enqueue generation, or assert that a
group is comprehensive.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Sequence

from ani_rag.models import RagContentType


class CurationKind(StrEnum):
    DIRECTOR_WORKS = "director_works"
    VOICE_ACTOR_CHARACTERS = "voice_actor_characters"
    SHARED_BIRTHDAY = "shared_birthday"


@dataclass(frozen=True)
class CurationFact:
    """One catalog-resolved relation/fact with the chunk that supports it."""

    kind: CurationKind
    group_entity_id: int
    group_name: str
    item_entity_id: int
    item_name: str
    source_document_id: str
    source_chunk_id: str
    source_id: str
    source_url: str
    menu_work_id: int | None = None
    menu_work_title: str | None = None
    fact_value: str | None = None
    published_at: datetime | None = None
    relation_type: str | None = None

    def __post_init__(self) -> None:
        group_id_required = self.kind is not CurationKind.SHARED_BIRTHDAY
        if (group_id_required and self.group_entity_id <= 0) or self.item_entity_id <= 0:
            raise ValueError("Curation facts require catalog-resolved entity IDs")
        if not all(value.strip() for value in (
            self.group_name, self.item_name, self.source_document_id,
            self.source_chunk_id, self.source_id, self.source_url,
        )):
            raise ValueError("Curation facts require names and complete source/chunk provenance")
        if self.menu_work_id is not None and self.menu_work_id <= 0:
            raise ValueError("Curation work IDs must be positive")
        if self.published_at is not None and (
            self.published_at.tzinfo is None or self.published_at.utcoffset() is None
        ):
            raise ValueError("Curation publication timestamps must be timezone-aware")
        if self.kind is CurationKind.DIRECTOR_WORKS and self.menu_work_id is None:
            raise ValueError("Director filmography items need a resolved work ID")
        if self.kind is CurationKind.DIRECTOR_WORKS and self.relation_type not in {"directed", "director_credit"}:
            raise ValueError("Director curation facts need a normalized directed relation")
        if self.kind is CurationKind.VOICE_ACTOR_CHARACTERS and (
            self.menu_work_id is None or self.relation_type not in {"voice_cast", "voice_actor_credit"}
        ):
            raise ValueError("Voice cast items need a work ID and normalized voice-credit relation")
        if self.kind is CurationKind.SHARED_BIRTHDAY and not self.fact_value:
            raise ValueError("Birthday groups need the source-preserved birthday value")


@dataclass(frozen=True)
class CurationPlan:
    plan_id: str
    content_type: RagContentType
    kind: CurationKind
    title: str
    query: str
    group_key: str
    item_names: tuple[str, ...]
    entity_ids: tuple[int, ...]
    menu_work_ids: tuple[int, ...]
    evidence_ids: tuple[str, ...]
    chunk_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_urls: tuple[str, ...]
    sample_size: int
    aggregation_method: str
    publication_window_start: datetime | None
    publication_window_end: datetime | None
    source_context: str


class EvidenceCurationPlanner:
    """Group only explicit, provenance-complete facts; never infer missing relations."""

    _MINIMUM_ITEMS = 3

    def plan(self, facts: Sequence[CurationFact]) -> tuple[CurationPlan, ...]:
        groups: dict[tuple[CurationKind, int, str], list[CurationFact]] = {}
        for fact in facts:
            key = self._key(fact)
            groups.setdefault(key, []).append(fact)
        plans: list[CurationPlan] = []
        for (kind, group_id, value), rows in groups.items():
            distinct: dict[tuple[int, int | None], CurationFact] = {}
            for row in sorted(rows, key=lambda item: (item.item_entity_id, item.menu_work_id or 0,
                                                       item.source_id, item.source_chunk_id)):
                identity = (row.item_entity_id, row.menu_work_id) if row.kind is CurationKind.VOICE_ACTOR_CHARACTERS else (row.item_entity_id, None)
                distinct.setdefault(identity, row)
            items = tuple(distinct.values())
            if len(items) < self._MINIMUM_ITEMS:
                continue
            plans.append(self._build_plan(kind, group_id, value, items))
        return tuple(sorted(plans, key=lambda item: (item.kind.value, item.group_key.casefold(), item.plan_id)))

    @staticmethod
    def _key(fact: CurationFact) -> tuple[CurationKind, int, str]:
        if fact.kind is CurationKind.DIRECTOR_WORKS:
            return fact.kind, fact.group_entity_id, f"directed-by:{fact.group_entity_id}"
        if fact.kind is CurationKind.VOICE_ACTOR_CHARACTERS:
            return fact.kind, fact.group_entity_id, f"voiced-by:{fact.group_entity_id}"
        return fact.kind, 0, f"birthday:{fact.fact_value}"

    @staticmethod
    def _build_plan(
        kind: CurationKind, group_id: int, group_key: str, rows: tuple[CurationFact, ...],
    ) -> CurationPlan:
        group_name = rows[0].group_name
        item_names = tuple(dict.fromkeys(
            f"{row.item_name} ({row.menu_work_title})" if row.menu_work_title else row.item_name
            for row in rows
        ))
        if kind is CurationKind.DIRECTOR_WORKS:
            title = f"{group_name}가 연출한 작품들"
            query = f"{group_name} 감독의 작품별 특징과 연출 이력"
            context = "출처에 명시된 감독-작품 관계의 확인된 일부 모음. 전체 필모그래피로 단정하지 말 것."
        elif kind is CurationKind.VOICE_ACTOR_CHARACTERS:
            title = f"{group_name}의 목소리로 만난 캐릭터들"
            query = f"성우 {group_name}의 작품별 캐릭터 배역과 캐릭터 소개"
            context = "출처에 명시된 성우-캐릭터-작품 크레딧의 확인된 일부 모음. 대표작 순위로 표현하지 말 것."
        else:
            birthday = rows[0].fact_value or ""
            title = f"같은 생일을 가진 애니 캐릭터들"
            query = f"생일이 {birthday}로 기록된 캐릭터 소개와 작품"
            context = f"출처가 같은 생일로 기록한 캐릭터의 일부 모음({birthday}); 공식성·표본 완전성을 과장하지 말 것."

        evidence_ids = tuple(sorted({row.source_document_id for row in rows}))
        chunk_ids = tuple(sorted({row.source_chunk_id for row in rows}))
        source_ids = tuple(sorted({row.source_id for row in rows}))
        source_urls = tuple(sorted({row.source_url for row in rows}))
        work_ids = tuple(sorted({row.menu_work_id for row in rows if row.menu_work_id is not None}))
        entity_ids = tuple(sorted({identifier for row in rows for identifier in (
            row.group_entity_id, row.item_entity_id,
        ) if identifier > 0}))
        dates = sorted(row.published_at for row in rows if row.published_at is not None)
        fingerprint = "\n".join((kind.value, group_key, *evidence_ids, *chunk_ids))
        plan_id = "cur_" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
        return CurationPlan(
            plan_id=plan_id, content_type=RagContentType.CURATION, kind=kind,
            title=title, query=query, group_key=group_key, item_names=item_names,
            entity_ids=entity_ids, menu_work_ids=work_ids,
            evidence_ids=evidence_ids, chunk_ids=chunk_ids,
            source_ids=source_ids, source_urls=source_urls, sample_size=len(item_names),
            aggregation_method="evidence_backed_relationship_grouping",
            publication_window_start=dates[0] if dates else None,
            publication_window_end=dates[-1] if dates else None, source_context=context,
        )
