"""Resolve generated content to the canonical menu_work_id dataset."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from ani_menu_agent.models import ContentPackage
from ani_menu_agent.sources import load_source_registry

_KOREAN_TITLE_PARTICLES = (
    "에서",
    "으로",
    "처럼",
    "까지",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "과",
    "와",
    "도",
    "만",
    "의",
    "로",
)


@lru_cache(maxsize=1)
def _aliases() -> list[tuple[str, int]]:
    catalog = Path(__file__).resolve().parents[2] / "data" / "reference"
    aliases: dict[str, int] = {}
    canonical = catalog / "menu_works.csv"
    if not canonical.is_file():
        return []
    with canonical.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            work_id = int(row["menu_work_id"])
            # Canonical work titles take precedence over entry-level title variants.
            for key in ("title_ko", "title_romaji", "title_english", "title_native"):
                value = (row.get(key) or "").strip()
                if value:
                    aliases[value.casefold()] = work_id
    return sorted(((name, work_id) for name, work_id in aliases.items()), key=lambda x: -len(x[0]))


@lru_cache(maxsize=1)
def _registered_work_titles() -> dict[str, str]:
    return {
        source.id: source.work_title_ko
        for source in load_source_registry().sources
        if source.work_title_ko
    }


def resolve_menu_work_ids(package: ContentPackage) -> list[int]:
    """Resolve works from publication/article titles, never from body prose.

    A source is tied to an official work in the source registry; that work is the
    representative fallback when its page title omits the franchise name. Any
    additional works must be explicitly named in a source/article title. Facts
    and generated copy are intentionally excluded.
    """
    aliases = _aliases()
    registered_titles = _registered_work_titles()
    ordered: list[int] = []

    def append_title_matches(title: str) -> None:
        normalized_title = title.casefold()
        found: list[tuple[int, int, int]] = []
        for alias, work_id in aliases:
            if len(alias.strip()) < 3:
                continue
            start = normalized_title.find(alias)
            while start >= 0:
                end = start + len(alias)
                left_boundary = start == 0 or not (
                    normalized_title[start - 1].isalnum() or normalized_title[start - 1] == "_"
                )
                right_boundary = end == len(normalized_title) or not (
                    normalized_title[end].isalnum() or normalized_title[end] == "_"
                ) or normalized_title.startswith(_KOREAN_TITLE_PARTICLES, end)
                if left_boundary and right_boundary:
                    found.append((start, -len(alias), work_id))
                    break
                start = normalized_title.find(alias, start + 1)
        for _, _, work_id in sorted(found):
            if work_id not in ordered:
                ordered.append(work_id)

    for evidence in package.sources:
        # Match all explicit works in the actual publisher title first.
        append_title_matches(evidence.title)
        # Source registry mapping supplies a reliable representative when an
        # official article's headline does not repeat the franchise title.
        registered_title = registered_titles.get(evidence.id)
        if registered_title:
            append_title_matches(registered_title)
    if not ordered:
        append_title_matches(package.topic)
    return ordered


def attach_menu_work_ids(package: ContentPackage) -> ContentPackage:
    package.menu_work_ids = resolve_menu_work_ids(package)
    return package
