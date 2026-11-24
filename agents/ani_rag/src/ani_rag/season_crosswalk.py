"""Resolve provider anime IDs through the archive entity and official AniList season owner."""

import csv
from collections import defaultdict
from pathlib import Path


def provider_season_owners(
    official_entries: Path, external_ids: Path, provider: str, *, provider_id_kind: str = "anime",
) -> dict[str, int]:
    seasons: dict[str, set[int]] = defaultdict(set)
    with official_entries.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("anilist_id"):
                seasons[row["anilist_id"]].add(int(row["menu_work_id"]))
    anilist_by_entity: dict[str, set[str]] = defaultdict(set)
    provider_by_id: dict[str, set[str]] = defaultdict(set)
    with external_ids.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["source_id"] == "anilist" and row["id_kind"] == "anime":
                anilist_by_entity[row["entity_id"]].add(row["external_id"])
            if row["source_id"] == provider and row["id_kind"] == provider_id_kind:
                provider_by_id[row["external_id"]].add(row["entity_id"])
    result = {}
    for native_id, entities in provider_by_id.items():
        if len(entities) == 1:
            entries = anilist_by_entity[next(iter(entities))]
            if len(entries) == 1:
                owners = seasons[next(iter(entries))]
                if len(owners) == 1:
                    result[native_id] = next(iter(owners))
    return result
