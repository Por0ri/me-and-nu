"""Read-only crosswalk for the separately versioned menu_dict CSV export.

This module is intentionally not an evidence repository and never opens a DB,
calls a model, or reads source-project files. Exact external IDs take priority;
alias lookup is exact and ignores rows marked as candidates.
"""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_MENU_DICT_DIR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "reference"
    / "menu_dict_20261109"
)


@dataclass(frozen=True)
class MenuDictEntity:
    entity_id: int
    kind: str
    menu_work_id: int | None
    parent_id: int | None
    name_ko: str | None
    name_native: str | None
    name_romaji: str | None


@dataclass(frozen=True)
class MenuDictResolution:
    status: str
    matched_on: str | None
    entity: MenuDictEntity | None = None
    candidates: tuple[MenuDictEntity, ...] = ()

    @property
    def menu_work_ids(self) -> tuple[int, ...]:
        """Return work IDs represented by this exact match or its candidates."""
        rows = (self.entity,) if self.entity is not None else self.candidates
        return tuple(sorted({row.menu_work_id for row in rows if row.menu_work_id is not None}))


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(value.split())


def _optional_int(value: str | None, field: str) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        result = int(value)
    except ValueError as error:
        raise ValueError(f"Invalid integer in menu_dict {field}: {value!r}") from error
    if result <= 0:
        raise ValueError(f"menu_dict {field} must be positive when present")
    return result


def _read_rows(directory: Path, filename: str, required: Iterable[str]) -> list[dict[str, str]]:
    path = (directory / filename).resolve()
    if directory not in path.parents:
        raise ValueError("menu_dict reference path escaped its snapshot directory")
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not set(required) <= set(reader.fieldnames):
            raise ValueError(f"Unexpected columns in menu_dict {filename}")
        return list(reader)


class MenuDictReference:
    """Exact, read-only lookups over the 2026-11-09 reference snapshot."""

    def __init__(self, directory: Path = DEFAULT_MENU_DICT_DIR) -> None:
        self.directory = directory.resolve()
        if self.directory != DEFAULT_MENU_DICT_DIR.resolve():
            raise ValueError("menu_dict lookup is restricted to its versioned ani_rag snapshot")

        entity_rows = _read_rows(
            self.directory,
            "entity.csv",
            ("entity_id", "kind", "menu_work_id", "parent_id", "name_ko", "name_native", "name_romaji"),
        )
        entities: dict[int, MenuDictEntity] = {}
        for row in entity_rows:
            entity_id = _optional_int(row.get("entity_id"), "entity_id")
            if entity_id is None or entity_id in entities:
                raise ValueError("menu_dict entity IDs must be present and unique")
            kind = row.get("kind", "").strip()
            if kind not in {"work", "entry"}:
                raise ValueError(f"Unsupported entity kind in this export: {kind!r}")
            entities[entity_id] = MenuDictEntity(
                entity_id=entity_id,
                kind=kind,
                menu_work_id=_optional_int(row.get("menu_work_id"), "menu_work_id"),
                parent_id=_optional_int(row.get("parent_id"), "parent_id"),
                name_ko=(row.get("name_ko") or "").strip() or None,
                name_native=(row.get("name_native") or "").strip() or None,
                name_romaji=(row.get("name_romaji") or "").strip() or None,
            )

        for entity in entities.values():
            if entity.kind == "work" and entity.parent_id is not None:
                raise ValueError(f"Work entity {entity.entity_id} unexpectedly has a parent")
            if entity.kind == "entry":
                parent = entities.get(entity.parent_id or -1)
                if parent is None or parent.kind != "work":
                    raise ValueError(f"Entry entity {entity.entity_id} has no valid work parent")

        alias_rows = _read_rows(
            self.directory, "alias.csv", ("entity_id", "alias", "lang", "is_candidate"),
        )
        external_rows = _read_rows(
            self.directory, "external_id.csv", ("source_id", "id_kind", "external_id", "entity_id"),
        )
        aliases: dict[tuple[str, str], set[int]] = {}
        aliases_by_name: dict[str, set[int]] = {}
        external_ids: dict[tuple[str, str, str], set[int]] = {}
        for row in alias_rows:
            entity_id = _optional_int(row.get("entity_id"), "alias.entity_id")
            if entity_id not in entities:
                raise ValueError(f"Alias references missing entity {entity_id}")
            candidate = row.get("is_candidate", "").strip().casefold()
            if candidate not in {"t", "f"}:
                raise ValueError("menu_dict alias is_candidate must be t or f")
            alias = _normalize(row.get("alias", ""))
            language = _normalize(row.get("lang", ""))
            if alias and candidate == "f":
                aliases.setdefault((alias, language), set()).add(entity_id)
                aliases_by_name.setdefault(alias, set()).add(entity_id)
        for row in external_rows:
            entity_id = _optional_int(row.get("entity_id"), "external_id.entity_id")
            if entity_id not in entities:
                raise ValueError(f"External ID references missing entity {entity_id}")
            key = tuple(_normalize(row.get(field, "")) for field in ("source_id", "id_kind", "external_id"))
            if all(key):
                external_ids.setdefault(key, set()).add(entity_id)

        self._entities = entities
        self._aliases = aliases
        self._aliases_by_name = aliases_by_name
        self._external_ids = external_ids
        self.max_menu_work_id = max(
            (entity.menu_work_id or 0 for entity in entities.values()), default=0,
        )

    def get_entity(self, entity_id: int) -> MenuDictEntity | None:
        return self._entities.get(entity_id)

    def parent_work(self, entity_id: int) -> MenuDictEntity | None:
        """Return the work row for a work/entry entity without collapsing entry identity."""
        entity = self.get_entity(entity_id)
        if entity is None:
            return None
        return entity if entity.kind == "work" else self.get_entity(entity.parent_id or -1)

    def resolve_external_id(
        self, *, source_id: str, id_kind: str, external_id: str,
    ) -> MenuDictResolution:
        key = tuple(_normalize(value) for value in (source_id, id_kind, external_id))
        return self._resolve(self._external_ids.get(key, set()), "external_id")

    def resolve_alias(self, alias: str, *, lang: str | None = None) -> MenuDictResolution:
        normalized = _normalize(alias)
        if not normalized:
            return MenuDictResolution("unresolved", None)
        if lang is not None:
            ids = self._aliases.get((normalized, _normalize(lang)), set())
        else:
            ids = self._aliases_by_name.get(normalized, set())
        return self._resolve(ids, "confirmed_alias")

    def _resolve(self, entity_ids: set[int], matched_on: str) -> MenuDictResolution:
        candidates = tuple(self._entities[entity_id] for entity_id in sorted(entity_ids))
        if len(candidates) == 1:
            return MenuDictResolution("matched", matched_on, entity=candidates[0])
        if candidates:
            return MenuDictResolution("ambiguous", matched_on, candidates=candidates)
        return MenuDictResolution("unresolved", None)
