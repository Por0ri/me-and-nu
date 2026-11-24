"""Check historical work IDs against the current official catalog before importing links."""

import csv
import os
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from ani_rag.catalog import DEFAULT_CATALOG_DIR
from ani_rag.work_registry import title_key


def official_works_path() -> Path:
    return Path(os.environ.get("ME_NU_WORK_CATALOG_DIR") or DEFAULT_CATALOG_DIR) / "menu_works.csv"


def archive_work_links(
    works: dict[int, tuple[int, tuple[str, ...]]],
    entries: dict[int, int],
    anilist_entries: Iterable[tuple[int, str]],
    official_works: Path,
    official_entries: Path,
) -> tuple[dict[int, tuple[int, str]], set[int]]:
    """Resolve archive entities to the current work owning each exact AniList season."""
    catalog = OfficialWorkIdentities(official_works)
    confirmed = {work_id for work_id, titles in works.values() if catalog.confirms(work_id, titles)}
    unconfirmed = {work_id for work_id, titles in works.values() if not catalog.confirms(work_id, titles)}
    confirmed -= unconfirmed
    by_anilist: dict[str, set[int]] = defaultdict(set)
    with official_entries.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["anilist_id"]:
                by_anilist[row["anilist_id"]].add(int(row["menu_work_id"]))
    entry_keys: dict[int, set[str]] = defaultdict(set)
    for entity_id, anilist_id in anilist_entries:
        entry_keys[entity_id].add(anilist_id)
    mapping = {entity_id: (record[0], "menu_dict_work_identity") for entity_id, record in works.items()}
    for entity_id, parent_id in entries.items():
        if parent_id not in works:
            continue
        historical_parent = works[parent_id][0]
        keys = entry_keys.get(entity_id)
        if not keys:
            mapping[entity_id] = (historical_parent, "menu_dict_entry_parent")
            continue
        if len(keys) != 1:
            continue
        owners = by_anilist.get(next(iter(keys)), set())
        if len(owners) != 1:
            continue
        owner = next(iter(owners))
        if owner not in catalog.anilist_ids or owner in unconfirmed:
            continue
        confirmed.add(owner)
        method = "menu_dict_entry_parent" if owner == historical_parent else "menu_dict_official_anilist_entry"
        mapping[entity_id] = (owner, method)
    return mapping, confirmed


class OfficialWorkIdentities:
    def __init__(self, path: Path):
        self.by_title: dict[str, set[int]] = defaultdict(set)
        self.anilist_ids: dict[int, set[str]] = {}
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                work_id = int(row["menu_work_id"])
                self.anilist_ids[work_id] = set((row.get("anilist_ids") or "").split(";")) - {""}
                for field in ("title_ko", "title_native", "title_romaji", "title_english"):
                    if key := title_key(row.get(field) or ""):
                        self.by_title[key].add(work_id)

    def confirms(self, work_id: int, titles: tuple[str, ...], anilist_ids: str = "") -> bool:
        matches = [self.by_title[key] for title in titles if (key := title_key(title)) in self.by_title]
        if not matches:
            return False
        candidates = set.intersection(*matches)
        historical = set(anilist_ids.split(";")) - {""}
        if historical and self.anilist_ids.get(work_id):
            candidates = {candidate for candidate in candidates if historical & self.anilist_ids[candidate]}
        return candidates == {work_id}
