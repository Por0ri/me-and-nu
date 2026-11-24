"""Resolve a character's explicit Wikidata appearances through official anime entries."""

import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from ani_rag.archive_identity import OfficialWorkIdentities, official_works_path
from ani_rag.interfaces import WorkResolution
from ani_rag.menu_dict_reference import DEFAULT_MENU_DICT_DIR
from ani_rag.models import WorkLinkStatus
from ani_rag.season_crosswalk import provider_season_owners
from ani_rag.work_registry import title_key


@lru_cache(maxsize=4)
def _owners(entries: Path, external: Path, signatures: tuple[tuple[int, int], ...]) -> dict[str, int]:
    return provider_season_owners(entries, external, "wikidata", provider_id_kind="item")


def appearance_owners(
    entries: Path | None = None, external: Path = DEFAULT_MENU_DICT_DIR / "external_id.csv",
) -> Mapping[str, int]:
    entries = (entries or official_works_path().with_name("menu_work_entries.csv")).resolve()
    external = external.resolve()
    try:
        stats = (entries.stat(), external.stat())
    except FileNotFoundError:
        return {}
    return _owners(entries, external, tuple((s.st_mtime_ns, s.st_size) for s in stats))


def _appearance_values(payload: Mapping):
    for statement in payload.get("statements", ()):
        if not isinstance(statement, dict) or statement.get("property_id") != "P1441":
            continue
        qualifiers = statement.get("qualifiers")
        if isinstance(qualifiers, dict) and qualifiers.get("rank") == "deprecated":
            continue
        value = statement.get("value")
        qid = value.get("entity_id") if isinstance(value, dict) else None
        if isinstance(qid, str) and re.fullmatch(r"Q[1-9][0-9]*", qid):
            yield qid, value.get("label")


def character_appearance(payload: Mapping, owners: Mapping[str, int]) -> tuple[WorkResolution, tuple[str, ...]]:
    appearances = {qid for qid, _ in _appearance_values(payload) if qid in owners}
    ids = tuple(sorted({owners[qid] for qid in appearances}))
    method = "wikidata_appearance_anilist_entry"
    if len(ids) == 1:
        resolution = WorkResolution(WorkLinkStatus.MATCHED, ids, matched_on=method)
    elif ids:
        resolution = WorkResolution(WorkLinkStatus.AMBIGUOUS, candidate_menu_work_ids=ids, matched_on=method)
    else:
        resolution = WorkResolution(WorkLinkStatus.UNRESOLVED)
    return resolution, tuple(sorted(appearances))


@dataclass(frozen=True)
class CharacterCastMatch:
    resolution: WorkResolution
    appearance_qid: str
    evidence_document_ids: tuple[str, ...]


@lru_cache(maxsize=4)
def _official_titles(path: Path, signature: tuple[int, int]) -> OfficialWorkIdentities:
    return OfficialWorkIdentities(path)


def archived_character_cast(db: sqlite3.Connection, payload: Mapping) -> CharacterCastMatch | None:
    """Corroborate a franchise P1441 with an exact character ID and an official entry's cast."""
    qid = payload.get("item_id")
    if not isinstance(qid, str) or not re.fullmatch(r"Q[1-9][0-9]*", qid):
        return None
    values = {(work_qid, title_key(label) if isinstance(label, str) else "")
              for work_qid, label in _appearance_values(payload)}
    if len(values) != 1:
        return None
    work_qid, label_key = values.pop()
    path = official_works_path()
    stat = path.stat()
    catalog = _official_titles(path, (stat.st_mtime_ns, stat.st_size))
    works = catalog.by_title.get(label_key, set())
    if len(works) != 1:
        return None
    work_id = next(iter(works))
    urls = sorted(f"https://anilist.co/anime/{season}" for season in catalog.anilist_ids[work_id] if season.isdecimal())
    if not urls:
        return None
    # Archive character IDs are meaningful only inside their versioned dataset.
    identities = db.execute(
        """SELECT json_extract(raw_payload_json,'$.entity_id'),json_extract(metadata_json,'$.dataset_version'),
                  min(document_id) FROM rag_document
           WHERE source_id='wikidata' AND canonical_url=? AND parse_status IN ('parsed','partial')
             AND json_extract(raw_payload_json,'$.dataset')='menu_dict'
             AND json_extract(raw_payload_json,'$.entity_kind')='character'
             AND json_type(raw_payload_json,'$.entity_id')='integer'
             AND length(json_extract(metadata_json,'$.dataset_version'))>0
           GROUP BY 1,2""", (f"https://www.wikidata.org/wiki/{qid}",),
    ).fetchall()
    if len(identities) != 1:
        return None
    character, version, identity_doc = identities[0]
    placeholders = ",".join("?" for _ in urls)
    cast = db.execute(
        f"""SELECT d.document_id FROM rag_document d
            WHERE d.source_id='anilist' AND d.canonical_url IN ({placeholders})
              AND d.parse_status IN ('parsed','partial')
              AND json_extract(d.metadata_json,'$.dataset_version')=?
              AND json_extract(d.raw_payload_json,'$.dataset')='menu_dict'
              AND json_extract(d.raw_payload_json,'$.entity_kind')='entry'
              AND json_extract(d.raw_payload_json,'$.field')='character'
              AND json_extract(d.raw_payload_json,'$.value_entity_id')=?
              AND EXISTS (SELECT 1 FROM rag_work_link w WHERE w.document_id=d.document_id
                            AND w.status='matched' AND w.menu_work_id=?)
              AND NOT EXISTS (SELECT 1 FROM rag_work_link w WHERE w.document_id=d.document_id
                                AND (w.status<>'matched' OR w.menu_work_id IS NOT ?))
            ORDER BY d.raw_document_id LIMIT 4""", (*urls, version, character, work_id, work_id),
    ).fetchall()
    if not cast:
        return None
    return CharacterCastMatch(
        WorkResolution(WorkLinkStatus.MATCHED, (work_id,), matched_on="wikidata_appearance_anilist_cast"),
        work_qid, (identity_doc, *(row[0] for row in cast)),
    )
