"""Identify the edition or cast member to which a dictionary fact actually applies."""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ani_rag.archive_identity import official_works_path
from ani_rag.catalog import DEFAULT_REFERENCE_DIR
from ani_rag.menu_dict_reference import DEFAULT_MENU_DICT_DIR
from ani_rag.work_registry import title_key


def _rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


_TITLES = ("title_ko", "title_native", "title_english", "title_romaji")
_SEASON = re.compile(r"(?:시즌|season|第)\s*(\d+)(?:기|期)?|(\d+)\s*(?:기|期|(?:st|nd|rd|th) season)", re.I)
_PART = re.compile(r"(?:파트|part)\W*(\d+)|(\d+)부(?!작)", re.I)
_MOVIE = re.compile(r"극장판|영화|劇場版|\b(?:film|movie)\b", re.I)
_FINAL = re.compile(r"파이널|최종\s*시즌|final\s*season|ファイナル", re.I)
_AGGREGATE = re.compile(r"(?:시리즈|시즌|전편).{0,8}(?:합산|통틀|합치|합쳐|전체|총합)|(?:모든|전체|전)\s*시즌|all seasons|entire series", re.I)
_PER_EPISODE = re.compile(r"한\s*(?:화|편|회)|회당|편당|episode|방영", re.I)
_TV = re.compile(r"TV\s*(?:판|방영|アニメ)|\bTV\b|텔레비전", re.I)
_QUALIFIER = re.compile(r"극장판|극장용|OVA|OAD|특별편|외전|시즌|파이널|[가-힣]{2,}편|\b(?:arc|season|film|movie|special)\b", re.I)


def _number(pattern, value):
    match = pattern.search(value)
    return int(next(v for v in match.groups() if v)) if match else None


@dataclass(frozen=True)
class Edition:
    entity: str
    names: tuple[str, ...]
    format: str
    year: int


@lru_cache(maxsize=2)
def _catalog(root: Path, stamp: tuple[int, ...]):
    works = {int(r["menu_work_id"]): tuple(r[k] for k in _TITLES if r[k])
             for r in _rows(root / "menu_works.csv")}
    historic = defaultdict(set)
    for row in _rows(DEFAULT_MENU_DICT_DIR / "external_id.csv"):
        if row["source_id"] == "anilist" and row["id_kind"] == "anime":
            historic[row["external_id"]].add(row["entity_id"])
    editions = defaultdict(list)
    owners = defaultdict(set)
    for row in _rows(root / "menu_work_entries.csv"):
        work = int(row["menu_work_id"])
        owners[row["anilist_id"]].add(work)
        candidates = historic.get(row["anilist_id"], ())
        if len(candidates) == 1:
            entity = next(iter(candidates))
            editions[work].append(Edition(entity, tuple(row[k] for k in _TITLES if row[k]),
                                          row["format"], int(row["year"] or 9999)))
    people = defaultdict(lambda: defaultdict(set))
    for row in _rows(DEFAULT_REFERENCE_DIR / "work_characters.csv"):
        work = int(row["menu_work_id"])
        if row["wikidata_id"] and owners.get(row["anilist_id"]) == {work}:
            people[work][row["wikidata_id"]].add(row["name"])
    return works, editions, people


def _edition_ids(query, roots, entries, fields):
    if _AGGREGATE.search(query):
        # The snapshot gives counts per release; one release is not a franchise total.
        return set()
    full = title_key(query)
    roots = sorted((title_key(x) for x in roots), key=len, reverse=True)
    season, part = _number(_SEASON, query), _number(_PART, query)
    movie, final = bool(_MOVIE.search(query)), bool(_FINAL.search(query))
    only_tv = bool(season or final or _TV.search(query) or ("duration_min" in fields and _PER_EPISODE.search(query)))
    # Two editions can have the same native title and different localized names
    # (for example a film re-edited for TV). Let the medium in the query disambiguate.
    alternate_names = defaultdict(set)
    for e in entries:
        for name in e.names:
            key = title_key(re.sub(r"\s*\(TV\)\s*$", "", name, flags=re.I))
            if key not in roots:
                alternate_names[key].update(e.names)
    year_match = re.search(r"\b((?:19|20)\d{2})(?:년|판|\b)", query)
    year = int(year_match[1]) if year_match else None
    pool = [e for e in entries if (not movie or e.format == "MOVIE") and (not year or e.year == year)
            and (movie or not only_tv or e.format in {"TV", "TV_SHORT", "ONA"})]
    scored = []
    for entry in pool:
        joined = unicodedata.normalize("NFKC", " | ".join(entry.names))
        if final and not _FINAL.search(joined):
            continue
        if season and (_number(_SEASON, joined) or 1) != season:
            continue
        if part and _number(_PART, joined) != part:
            continue
        keys = {title_key(name) for name in entry.names}
        specific = 0
        alias_keys = set(keys)
        for name in entry.names:
            alias = title_key(re.sub(r"\s*\(TV\)\s*$", "", name, flags=re.I))
            alias_keys.update(title_key(n) for n in alternate_names[alias])
        for key in alias_keys:
            suffix = key
            for root in roots:
                suffix = suffix.replace(root, "")
            suffix = re.sub(r"극장판|劇場版|themovie", "", suffix)
            if len(suffix) >= 2 and suffix in full:
                specific = max(specific, len(suffix))
        representative = bool(keys.intersection(roots))
        if not (specific or movie or final or season or part) and _QUALIFIER.search(query):
            continue
        # A bare title asks about the representative TV edition when one exists.
        score = (specific, entry.format in {"TV", "TV_SHORT"}, representative,
                 not bool(_PART.search(joined)), -entry.year)
        scored.append((score, entry))
    if not scored:
        return set()
    most_specific = max(s[0] for s, _ in scored)
    contenders = [(score, entry) for score, entry in scored if score[0] == most_specific]
    if most_specific and not movie and not only_tv and len({e.format for _, e in contenders}) > 1:
        return set()
    if movie and not most_specific and len(contenders) > 1:
        return set()
    best = max(score for score, _ in contenders)
    return {entry.entity for score, entry in contenders if score == best}


def _character_titles(query, roots, characters, fields):
    compact = title_key(query)
    for name in sorted(roots, key=len, reverse=True):
        # A work and a character can share a title. A separate name in the
        # remaining question is needed to limit a cast request to one person.
        compact = compact.replace(title_key(name), "", 1)
    named = [(max((len(title_key(name)) for name in names
                   if len(title_key(name)) >= 2 and title_key(name) in compact), default=0), names)
             for names in characters.values()]
    longest = max((size for size, _ in named), default=0)
    if longest:
        matches = [names for size, names in named if size == longest]
        if len(matches) != 1:
            return set()
        aliases = matches[0]
        return {name + " — " + field for name in aliases for field in fields}
    residue = compact
    residue = re.sub(r"한국어?|일본어?|한국판|일본판|더빙|성우(?:진|들)?|목소리|담당|누구|알려줘|작품|캐릭터|주요|주연|등장인물|"
                     r"일람|명단|목록|소개|영어|배우|korean|japanese|voiceactors?|voicecast|声優|[이가은는야의와?]", "", residue)
    # Unnamed cast request allows different characters; a missing named character does not.
    return None if not residue else set()


@dataclass(frozen=True)
class FactSubjects:
    edition_ids: frozenset[str] | None = None
    character_titles: frozenset[str] | None = None
    external_subjects: frozenset[str] | None = None
    varied_cast: bool = False
    edition_formats: frozenset[str] = frozenset()

    def cast_key(self, document):
        if not self.varied_cast or document.metadata.get("dataset") != "menu_dict":
            return None
        if document.metadata.get("menu_dict_field") not in {"voice_actor", "voice_actor_ko", "jp_cast"}:
            return None
        return document.metadata.get("menu_dict_entity_id") or document.title.split(" — ")[0]

    def accepts_external(self, document):
        if self.external_subjects is None:
            return True
        heading = re.split(r"\s*[-|]\s*Anime News Network", document.title, flags=re.I)[0]
        medium = re.search(r"\s+\((TV|movie|OVA|ONA)\)$", heading, flags=re.I)
        if medium:
            if self.edition_formats and medium[1].upper() not in self.edition_formats:
                return False
            heading = heading[:medium.start()]
        return title_key(heading) in self.external_subjects

    def accepts(self, document, fields):
        field = document.metadata.get("menu_dict_field")
        if field not in fields:
            return False
        if field in {"episodes", "duration_min"}:
            return self.edition_ids is None or str(document.metadata.get("menu_dict_entity_id")) in self.edition_ids
        return self.character_titles is None or document.title in self.character_titles

    def sql_condition(self, fields):
        """Return the same positive subject test used to select and rank dictionary facts."""
        parts, params = [], []
        for group, matches, column in (
            ({"episodes", "duration_min"}, self.edition_ids, "CAST(json_extract(d.metadata_json,'$.menu_dict_entity_id') AS TEXT)"),
            ({"voice_actor", "voice_actor_ko", "jp_cast"}, self.character_titles, "d.title"),
        ):
            selected = tuple(f for f in fields if f in group)
            if not selected or matches == frozenset():
                continue
            expr = "json_extract(d.metadata_json,'$.menu_dict_field') IN (" + ",".join("?" for _ in selected) + ")"
            params.extend(selected)
            if matches is not None:
                expr += " AND " + column + " IN (" + ",".join("?" for _ in matches) + ")"
                params.extend(sorted(matches))
            parts.append("(" + expr + ")")
        return ("(json_extract(d.metadata_json,'$.dataset')='menu_dict' AND (" +
                (" OR ".join(parts) or "0") + "))"), params


def fact_subjects(query, work_ids, fields, *, article=False):
    if not fields:
        return FactSubjects()
    catalog = official_works_path().parent
    paths = (catalog / "menu_works.csv", catalog / "menu_work_entries.csv",
             DEFAULT_MENU_DICT_DIR / "external_id.csv", DEFAULT_REFERENCE_DIR / "work_characters.csv")
    try:
        works, editions, characters = _catalog(catalog, tuple(p.stat().st_mtime_ns for p in paths))
    except FileNotFoundError:
        # Deployments can omit the local archive. Never guess an archive entity.
        return FactSubjects(frozenset(), frozenset(), None if article else frozenset())
    query = unicodedata.normalize("NFKC", query)
    ids, titles = set(), set()
    any_character = False
    unrestricted_cast = False
    external = set()
    formats = set()
    qualified = False
    for wid in work_ids:
        if {"episodes", "duration_min"}.intersection(fields):
            matched = _edition_ids(query, works.get(wid, ()), editions[wid], fields)
            ids.update(matched)
            if wid in works:
                qualified = True
                for edition in editions[wid]:
                    if edition.entity in matched:
                        formats.add("TV" if edition.format == "TV_SHORT" else edition.format)
                        external.update(title_key(name) for name in edition.names)
        if any(f.startswith("voice_") or f == "jp_cast" for f in fields):
            names = _character_titles(query, works.get(wid, ()), characters[wid], fields)
            if names is None:
                any_character = True
                if characters[wid]:
                    titles.update(name + " — " + field for group in characters[wid].values()
                                  for name in group for field in fields)
                else:
                    unrestricted_cast = True
            else:
                titles.update(names)
                if wid in works:
                    qualified = True
                    external.update(title_key(name.split(" — ")[0]) for name in names)
    return FactSubjects(frozenset(ids), None if unrestricted_cast else frozenset(titles),
                        frozenset(external) if qualified and not any_character and not article else None,
                        any_character, frozenset(formats))
