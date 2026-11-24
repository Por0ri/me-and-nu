"""Resolve an ANN page's own title and edition against the team catalog."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ani_rag.menu_dict_reference import DEFAULT_MENU_DICT_DIR
from ani_rag.season_crosswalk import provider_season_owners
from ani_rag.work_registry import title_key


_MEDIA = re.compile(
    r"\s*\((TV(?: short)?|movie|OAV|OVA|ONA|web|special|manga|light novel|novel|live[- ]action[^()]*)"
    r"(?:\s+([0-9]+))?\)$",
    re.I,
)
_LANG = re.compile(
    r"\s*\((?:Japanese|English|German|French|Spanish|Korean|Chinese|Russian|Italian|Portuguese)\)$", re.I
)
_FORMAT = {
    "tv": {"TV", "TV_SHORT"},
    "tv short": {"TV_SHORT"},
    "movie": {"MOVIE"},
    "ova": {"OVA"},
    "oav": {"OVA"},
    "ona": {"ONA"},
    "web": {"ONA"},
    "special": {"SPECIAL"},
}


def _key(value: str) -> str:
    return title_key(value.replace("×", "x"))


def _own_heading(title: str, lines: list[str]) -> str:
    for i, line in enumerate(lines[:40]):
        if line.startswith("# "):
            return line[2:].strip()
        if i and line in {"Have you seen this?", "Have you read this?"}:
            return lines[i - 1]
    return re.sub(r"\s*[-–|]\s*Anime News Network.*$", "", title, flags=re.I).strip()


@dataclass(frozen=True)
class AnnIdentity:
    confirmed: int | None
    candidates: tuple[int, ...]
    primary_matches: tuple[int, ...]
    method: str = "ann_page_edition_exact"


class AnnTitleIndex:
    def __init__(self, catalog_dir: Path, registry_dir: Path, external_ids: Path | None = None) -> None:
        crosswalk = external_ids if external_ids is not None else DEFAULT_MENU_DICT_DIR / "external_id.csv"
        self.external_owners = (
            provider_season_owners(catalog_dir / "menu_work_entries.csv", crosswalk, "ann")
            if crosswalk.is_file()
            else {}
        )
        self.records: dict[str, set[tuple[int, str, str]]] = defaultdict(set)
        for path, entry in (
            (catalog_dir / "menu_works.csv", False),
            (catalog_dir / "menu_work_entries.csv", True),
            (registry_dir / "agent_works.csv", False),
        ):
            if path.name == "agent_works.csv" and not path.is_file():
                continue
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    work_id = int(row["menu_work_id"])
                    fmt, year = (row.get("format", ""), row.get("year", "")) if entry else ("", "")
                    for field in ("title_ko", "title_native", "title_english", "title_romaji"):
                        title = row.get(field) or ""
                        if title:
                            for name in {title, re.sub(r"\s*\((?:19|20)\d\d\)$", "", title)}:
                                self.records[_key(name)].add((work_id, fmt, year))

    def resolve(self, title: str, body: str, url: str, *, stored_id: str | None = None) -> AnnIdentity:
        result = self._resolve_title(title, body, url)
        page = urlsplit(url)
        if (
            not body.strip()
            or page.scheme != "https"
            or page.hostname not in {"www.animenewsnetwork.com", "animenewsnetwork.com"}
            or page.path != "/encyclopedia/anime.php"
        ):
            return result
        ids = parse_qs(page.query).get("id", [])
        if len(ids) != 1 or not re.fullmatch(r"[1-9][0-9]*", ids[0]) or stored_id not in (None, ids[0]):
            return result
        owner = self.external_owners.get(ids[0])
        if owner is None:
            return result
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        heading = _own_heading(title, lines)
        if re.search(r"\([^)]*(?:manga|live[- ]action|novel)[^)]*\)$", heading, re.I):
            return result
        if result.confirmed is not None and result.confirmed != owner:
            return AnnIdentity(None, tuple(sorted({result.confirmed, owner})), result.primary_matches)
        return AnnIdentity(owner, (owner,), result.primary_matches, "ann_anilist_season_id")

    def _resolve_title(self, title: str, body: str, url: str) -> AnnIdentity:
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        heading = _own_heading(title, lines)
        marker = _MEDIA.search(heading)
        base = heading[: marker.start()] if marker else heading
        media, edition = (marker[1].casefold(), marker[2]) if marker else ("", None)
        primary = self.records.get(_key(base), set())
        candidates = {row[0] for row in primary}
        alternatives: list[set[tuple[int, str, str]]] = []
        for i, line in enumerate(lines):
            if line.strip("*").casefold() not in {"alternative title:", "alternative titles:"}:
                continue
            for alternate in lines[i + 1 : i + 16]:
                alternate = alternate.strip("*")
                if not _LANG.search(alternate):
                    break
                found = self.records.get(_key(_LANG.sub("", alternate)), set())
                if found:
                    alternatives.append(found)
                    candidates.update(r[0] for r in found)
            break
        empty = AnnIdentity(None, tuple(sorted(candidates)), tuple(sorted({r[0] for r in primary})))
        page = urlsplit(url)
        if (
            not body.strip()
            or page.hostname not in {"animenewsnetwork.com", "www.animenewsnetwork.com"}
            or page.path != "/encyclopedia/anime.php"
            or (media and media not in _FORMAT)
            or re.search(r"\([^)]*\blive[- ]action\b[^)]*\)$", heading, re.I)
        ):
            return empty
        vintage = re.search(r"(?im)^\**Vintage:\**\s*((?:19|20)\d\d)\b", body)
        year = edition if edition and len(edition) == 4 else vintage[1] if vintage else None
        if year and edition and len(edition) == 4 and vintage and vintage[1] != edition:
            return empty
        # ANN's "(TV 2)" is an edition, not a year and not the original TV series.
        if edition and len(edition) != 4:
            suffix = {"1": "1st", "2": "2nd", "3": "3rd"}.get(edition, edition + "th")
            primary = set().union(
                *(
                    self.records.get(_key(name), set())
                    for name in (f"{base} Season {edition}", f"{base} {suffix} Season", f"{base} {edition}")
                )
            )
            if not primary:
                return empty
        matches = [rows for rows in (primary, *alternatives) if rows]
        if not matches:
            return empty
        compatible = set.intersection(*({r[0] for r in group} for group in matches))
        eligible = set().union(*matches)
        if year:
            eligible = {r for r in eligible if r[2] == year and (not media or r[1] in _FORMAT[media])}
        elif media:
            eligible = {r for r in eligible if r[1] in _FORMAT[media]}
        compatible &= {r[0] for r in eligible}
        primary_ids = tuple(sorted({r[0] for r in primary} & compatible))
        return AnnIdentity(
            next(iter(compatible)) if len(compatible) == 1 else None,
            tuple(sorted(compatible or candidates)),
            primary_ids,
        )
