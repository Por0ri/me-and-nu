"""Read selected facts from the teammate's PostgreSQL custom-format archive.

The archive is parsed read-only. Only sources explicitly listed in
``config/menu_dict_import_policy.json`` become RAG evidence. Each fact remains
individually locatable and keeps its original source URL, fetch time, entity ID,
work links, and source terms. This module never connects to PostgreSQL or opens
an ani_me;nu database.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping
from urllib.parse import urlsplit

from ani_rag.models import (
    CollectionMethod,
    EvidenceDocument,
    EvidenceExcerpt,
    EvidenceType,
    RagContentType,
    WorkLinkStatus,
)

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "menu_dict_import_policy.json"
_SONG_FIELDS = {"theme_song", "theme_song_ja", "song_title_ja", "song_credit", "performer", "song_link"}
_PEOPLE_FIELDS = {"staff", "jp_staff", "cast", "jp_cast", "voice_actor", "voice_actor_ko", "director", "screenwriter"}


@dataclass(frozen=True)
class ArchiveEntity:
    entity_id: int
    kind: str
    menu_work_id: int | None
    parent_id: int | None
    names: tuple[str, ...]

    @property
    def display_name(self) -> str:
        return next((name for name in self.names if name.strip()), f"entity:{self.entity_id}")


@dataclass(frozen=True)
class ArchiveSource:
    source_id: str
    name: str
    license: str
    attribution: str
    notes: str


class MenuDictArchiveReader:
    """Stream eligible reference facts as RAG documents without importing the archive."""

    def __init__(self, archive_path: Path, *, policy_path: Path = DEFAULT_POLICY_PATH) -> None:
        self.archive_path = archive_path.resolve()
        self.policy_path = policy_path.resolve()
        if not self.archive_path.is_file():
            raise FileNotFoundError(self.archive_path)
        if self.policy_path != DEFAULT_POLICY_PATH.resolve():
            raise ValueError("Import policy must be the versioned ani_rag policy file")
        self.archive_sha256 = _sha256_file(self.archive_path)
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        declared_hash = str(policy.get("snapshot_sha256", "")).casefold().strip()
        if declared_hash and declared_hash != self.archive_sha256:
            raise ValueError("menu_dict archive hash does not match the import policy snapshot")
        self.allowed_sources: Mapping[str, Mapping[str, object]] = policy["import_only_sources"]
        self.excluded_fields = frozenset(str(value) for value in policy["excluded_fields"])

        try:
            import pgdumplib
        except ImportError as error:
            raise RuntimeError("Install the optional archive-import dependency: pip install 'ani-rag[archive-import]'") from error
        self._dump = pgdumplib.load(str(self.archive_path))
        if self._dump._format != "Custom":
            raise ValueError("menu_dict input must be a PostgreSQL custom-format dump")
        self.entities = self._read_entities()
        self.sources = self._read_sources()
        self._source_work_links = self._derive_work_links()

    def _rows(self, table: str):
        if not any(e.desc == "TABLE DATA" and e.namespace == "dict" and e.tag == table for e in self._dump.entries):
            raise ValueError(f"menu_dict archive is missing dict.{table}")
        return self._dump.table_data("dict", table)

    def _read_entities(self) -> dict[int, ArchiveEntity]:
        entities: dict[int, ArchiveEntity] = {}
        for row in self._rows("entity"):
            entity_id, kind, work_id, parent_id, name_ko, name_native, name_romaji = row
            identifier = int(entity_id)
            if identifier in entities:
                raise ValueError(f"Duplicate menu_dict entity_id {identifier}")
            names = tuple(str(value).strip() for value in (name_ko, name_native, name_romaji) if value and str(value).strip())
            entities[identifier] = ArchiveEntity(
                entity_id=identifier,
                kind=str(kind),
                menu_work_id=int(work_id) if work_id is not None else None,
                parent_id=int(parent_id) if parent_id is not None else None,
                names=names,
            )
        for entity in entities.values():
            if entity.kind == "entry":
                parent = entities.get(entity.parent_id or -1)
                if parent is None or parent.kind != "work" or parent.menu_work_id is None:
                    raise ValueError(f"Entry entity {entity.entity_id} has no work parent")
        return entities

    def _read_sources(self) -> dict[str, ArchiveSource]:
        result: dict[str, ArchiveSource] = {}
        for row in self._rows("source"):
            source_id, name, license_text, attribution, _recheck, notes = row
            result[str(source_id)] = ArchiveSource(
                str(source_id), str(name), str(license_text), str(attribution or ""), str(notes or ""),
            )
        return result

    def _derive_work_links(self) -> dict[int, set[int]]:
        links: dict[int, set[int]] = {}
        for entity in self.entities.values():
            if entity.kind == "work" and entity.menu_work_id is not None:
                links.setdefault(entity.entity_id, set()).add(entity.menu_work_id)
            elif entity.kind == "entry" and entity.parent_id is not None:
                parent = self.entities[entity.parent_id]
                if parent.menu_work_id is not None:
                    links.setdefault(entity.entity_id, set()).add(parent.menu_work_id)
        for row in self._rows("fact"):
            _fact_id, entity_id, _field, _value, value_entity_id, _source_id, _url, _fetched = row
            source_entity = self.entities.get(int(entity_id))
            if source_entity is None or source_entity.kind != "entry" or value_entity_id is None:
                continue
            parent = self.entities.get(source_entity.parent_id or -1)
            if parent and parent.menu_work_id is not None:
                links.setdefault(int(value_entity_id), set()).add(parent.menu_work_id)
        return links

    def iter_documents(self) -> Iterator[EvidenceDocument]:
        """Yield one evidence document per eligible source fact; never schedule generation."""
        for row in self._rows("fact"):
            fact_id, entity_id, field_name, raw_value, value_entity_id, source_id, source_url, fetched_at = row
            source_id = str(source_id)
            source_policy = self.allowed_sources.get(source_id)
            field_name = str(field_name)
            if source_policy is None or field_name in self.excluded_fields or not source_url:
                continue
            entity = self.entities.get(int(entity_id))
            source = self.sources.get(source_id)
            if entity is None or source is None:
                continue
            content_type = _content_type(entity, field_name, value_entity_id, self.entities)
            if content_type is None or content_type.value not in source_policy["content_types"]:
                continue
            parsed_url = urlsplit(str(source_url))
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                continue
            allowed_hosts = {str(host).casefold() for host in source_policy.get("allowed_hosts", ())}
            if parsed_url.hostname.casefold() not in allowed_hosts:
                continue
            fetched = _aware_datetime(fetched_at)
            value = _decode_json_value(raw_value)
            related = self.entities.get(int(value_entity_id)) if value_entity_id is not None else None
            fact_text = _render_fact(entity, field_name, value, related)
            work_ids = tuple(sorted(self._source_work_links.get(entity.entity_id, set())))
            # A person or studio linked to many franchises must not be falsely
            # presented as belonging to a single requested work.
            if len(work_ids) > 50:
                work_ids = ()
            link_status = WorkLinkStatus.MATCHED if work_ids else WorkLinkStatus.UNRESOLVED
            fact_key = hashlib.sha256(
                f"{self.archive_sha256}:{fact_id}:{source_id}".encode("utf-8")
            ).hexdigest()[:30]
            language = _language(source_id, value)
            metadata = {
                "dataset": "menu_dict",
                "dataset_version": "2026-11-09",
                "archive_sha256": self.archive_sha256,
                "menu_dict_entity_id": str(entity.entity_id),
                "menu_dict_entity_kind": entity.kind,
                "menu_dict_fact_id": str(fact_id),
                "menu_dict_field": field_name,
                "source_license": source.license,
                "source_attribution": source.attribution,
                "source_notes": source.notes,
                "import_policy": str(source_policy["license_basis"]),
                "permission_confirmation": str(source_policy.get("permission_confirmation", "")),
            }
            if work_ids:
                metadata["work_match_method"] = "menu_dict_entry_parent_or_explicit_entity_relation"
            yield EvidenceDocument(
                document_id=f"md_{fact_key}",
                content_type=content_type,
                title=f"{entity.display_name} — {field_name}",
                source_url=str(source_url),
                source_id=source_id,
                retrieved_at=fetched,
                evidence_type=EvidenceType.REFERENCE_FACT,
                collection_method=CollectionMethod.MANUAL_IMPORT,
                language=language,
                source_native_id=str(fact_id),
                publisher=source.name,
                excerpts=(EvidenceExcerpt(
                    text=fact_text,
                    locator=f"dict.fact/{fact_id}/{field_name}@{fetched.isoformat()}",
                ),),
                work_link_status=link_status,
                menu_work_ids=work_ids,
                metadata=metadata,
                raw_payload={
                    "dataset": "menu_dict",
                    "fact_id": int(fact_id),
                    "entity_id": entity.entity_id,
                    "entity_kind": entity.kind,
                    "field": field_name,
                    "value": value,
                    "value_entity_id": int(value_entity_id) if value_entity_id is not None else None,
                },
            )


def _content_type(
    entity: ArchiveEntity,
    field_name: str,
    value_entity_id: object,
    entities: Mapping[int, ArchiveEntity],
) -> RagContentType | None:
    related = entities.get(int(value_entity_id)) if value_entity_id is not None else None
    if (related and related.kind == "song") or field_name in _SONG_FIELDS:
        return RagContentType.MUSIC
    if (related and related.kind in {"person", "studio"}) or field_name in _PEOPLE_FIELDS:
        return RagContentType.PEOPLE
    return {
        "character": RagContentType.CHARACTER,
        "person": RagContentType.PEOPLE,
        "studio": RagContentType.PEOPLE,
        "song": RagContentType.MUSIC,
        "entry": RagContentType.GUIDE,
        "work": RagContentType.GUIDE,
    }.get(entity.kind)


def _decode_json_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _render_fact(entity: ArchiveEntity, field_name: str, value: object, related: ArchiveEntity | None) -> str:
    parts = [f"Entity: {entity.display_name} ({entity.kind}; menu_work_id={entity.menu_work_id})"]
    if related:
        parts.append(f"Linked entity: {related.display_name} ({related.kind}; entity_id={related.entity_id})")
    parts.append(f"Fact field: {field_name}")
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        rendered = value["text"]
    elif isinstance(value, dict):
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    elif isinstance(value, (list, tuple)):
        rendered = json.dumps(value, ensure_ascii=False)
    else:
        rendered = str(value)
    parts.append(f"Fact value: {rendered}")
    return "\n".join(parts)


def _language(source_id: str, value: object) -> str:
    if isinstance(value, dict) and isinstance(value.get("lang"), str) and value["lang"].strip():
        return value["lang"].strip().casefold()
    return {
        "enwiki": "en",
        "kowiki": "ko",
        "kmrb": "ko",
        "madb": "ja",
        "wikidata": "mul",
    }.get(source_id, "und")


def _aware_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
