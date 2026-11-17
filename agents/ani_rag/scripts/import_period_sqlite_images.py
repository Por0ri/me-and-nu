"""Import work-linked image assets from the period lane SQLite archive.

The source database is opened read-only. The target must already be provisioned;
this script never creates a schema, activates a source, or starts a worker.
Run without --apply to inspect the bounded import plan.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DB = ROOT.parent / "ani_me;nu" / "data" / "generation_continuous_recovery_20261101.sqlite3"
DEFAULT_TARGET_DB = ROOT / "data" / "ani_rag_live_20261109.sqlite3"
DEFAULT_REFERENCE = ROOT / "data" / "reference" / "menu_works.csv"
DEFAULT_ASSET_DIR = ROOT / "data" / "image_assets" / "ani_me_sqlite_import"
IMAGE_RECORD_QUERY = """SELECT request_id,payload_json,status FROM agent_records
    WHERE agent_type='image' AND record_kind='image_asset'
      AND status NOT IN ('rejected','expired')"""


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def _catalog_ids(path: Path) -> set[int]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return {int(row["menu_work_id"]) for row in csv.DictReader(stream)}


def _valid_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password


def _json_object(value: object) -> dict[str, Any]:
    parsed = json.loads(value) if isinstance(value, str) else value
    return parsed if isinstance(parsed, dict) else {}


def _origin_for_image(
    image: dict[str, Any], request_id: str, work_ids: set[int],
    sources_by_request: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    provenance = image.get("provenance_source")
    if isinstance(provenance, dict) and provenance.get("id"):
        return provenance
    for source in sources_by_request.get(request_id, ()):
        source_work_ids = {item for item in source.get("menu_work_ids", ()) if isinstance(item, int)}
        if source.get("id") and source_work_ids.intersection(work_ids):
            return source
    raise ValueError(f"Image has no source identity for work-linked request {request_id!r}")


def _image_alt_text(group: dict[str, Any]) -> str:
    data = group["representative"]
    parts: list[str] = []
    for label, value in (
        ("Image type", data.get("asset_kind")),
        ("Visual role", data.get("visual_match")),
        ("Description", data.get("source_description") or data.get("korean_explanation")),
    ):
        if isinstance(value, str) and value.strip():
            parts.append(f"{label}: {value.strip()}")
    for label, key in (("Depicted", "depicted_entities"), ("Visible text", "visible_text")):
        values = data.get(key)
        if isinstance(values, list):
            clean = [str(item).strip() for item in values if str(item).strip()]
            if clean:
                parts.append(f"{label}: {', '.join(dict.fromkeys(clean))}")
    if data.get("used_in_previous_content"):
        parts.append("Previously used in period-lane content")
    return "; ".join(parts)[:1200] or "Imported work-linked image candidate"


def _build_plan(source: sqlite3.Connection, target: sqlite3.Connection, reference: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    work_ids = {
        int(row[0]) for row in target.execute(
            "SELECT menu_work_id FROM rag_work_link WHERE status='matched' GROUP BY menu_work_id"
        ) if row[0] is not None
    }
    work_ids.intersection_update(_catalog_ids(reference))
    if not work_ids:
        raise RuntimeError("No confirmed RAG work IDs were found in the local reference snapshot")

    sources_by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source.execute(
        """SELECT request_id,payload_json FROM agent_records
           WHERE record_kind='source' AND agent_type IN ('information','humor')
             AND status NOT IN ('rejected','expired')"""
    ):
        sources_by_request[str(row["request_id"])].append(_json_object(row["payload_json"]))

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in source.execute(IMAGE_RECORD_QUERY):
        image = _json_object(row["payload_json"])
        digest = str(image.get("storage_key") or "").casefold()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            continue
        linked_ids = {
            int(item) for item in (image.get("menu_work_ids") or ())
            if isinstance(item, int) and item > 0 and item in work_ids
        }
        if not linked_ids:
            continue
        image_url = str(image.get("url") or "")
        page_url = str(image.get("source_url") or "")
        if not _valid_url(image_url) or not _valid_url(page_url):
            raise ValueError("Work-linked image is missing a valid asset URL or source page URL")
        request_id = str(row["request_id"])
        origin = _origin_for_image(image, request_id, linked_ids, sources_by_request)
        source_id = str(origin["id"])
        origin_url = str(origin.get("url") or page_url)
        if not _valid_url(origin_url):
            origin_url = page_url
        key = (source_id, image_url)
        group = groups.get(key)
        if group is None:
            group = {
                "source_id": source_id,
                "source_url": image_url,
                "source_page_url": origin_url,
                "source_native_id": str(image.get("id") or digest),
                "sha256": digest,
                "work_ids": set(),
                "source_pages": set(),
                "representative": image,
                "source_metadata": origin,
                "rights_basis": image.get("rights_basis"),
                "rights_evidence_url": image.get("rights_evidence_url"),
            }
            groups[key] = group
        elif group["sha256"] != digest:
            raise ValueError(f"One source image URL has conflicting content hashes: {source_id}")
        group["work_ids"].update(linked_ids)
        group["source_pages"].add(page_url)
        if not group.get("rights_basis") and image.get("rights_basis"):
            group["rights_basis"] = image["rights_basis"]
        if not group.get("rights_evidence_url") and image.get("rights_evidence_url"):
            group["rights_evidence_url"] = image["rights_evidence_url"]

    if not groups:
        raise RuntimeError("No work-linked image assets are eligible for import")

    blob_info: dict[str, tuple[str | None, int]] = {}
    hashes = sorted({group["sha256"] for group in groups.values()})
    for offset in range(0, len(hashes), 400):
        batch = hashes[offset:offset + 400]
        marks = ",".join("?" for _ in batch)
        for row in source.execute(
            f"SELECT sha256,media_type,file_size_bytes FROM image_blobs WHERE sha256 IN ({marks})", batch
        ):
            blob_info[str(row["sha256"])] = (row["media_type"], int(row["file_size_bytes"] or 0))
    missing_blobs = set(hashes) - blob_info.keys()
    if missing_blobs:
        raise RuntimeError(f"{len(missing_blobs)} referenced image blobs are missing")

    existing_sources = {str(row[0]) for row in target.execute("SELECT source_id FROM rag_source")}
    existing_assets = {
        (str(row["source_id"]), str(row["source_url"])): dict(row)
        for row in target.execute("SELECT asset_id,source_id,source_url,content_sha256,local_path FROM rag_image_asset")
    }
    existing_hash_paths: dict[str, str] = {}
    for row in target.execute(
        "SELECT content_sha256,local_path FROM rag_image_asset WHERE content_sha256 IS NOT NULL"
    ):
        digest, path = str(row["content_sha256"]), str(row["local_path"] or "")
        if path and Path(path).is_file():
            existing_hash_paths.setdefault(digest, path)

    metadata: dict[tuple[str, str], dict[str, Any]] = {}
    new_asset_count = 0
    linked_count = 0
    source_metadata: dict[str, dict[str, Any]] = {}
    for key, group in groups.items():
        source_id, image_url = key
        existing = existing_assets.get(key)
        if existing:
            if existing["content_sha256"] != group["sha256"]:
                raise RuntimeError(f"Existing RAG image URL has a different SHA-256: {source_id}")
            group["asset_id"] = str(existing["asset_id"])
            group["local_path"] = str(existing["local_path"])
        else:
            group["asset_id"] = "img_" + hashlib.sha256(
                f"{source_id}\n{image_url}\n{group['sha256']}".encode("utf-8")
            ).hexdigest()[:32]
            group["local_path"] = existing_hash_paths.get(group["sha256"], "")
            new_asset_count += 1
        for work_id in group["work_ids"]:
            linked_count += 1
        if source_id not in existing_sources:
            source_metadata.setdefault(source_id, group["source_metadata"])
        metadata[key] = group

    copy_hashes = {
        group["sha256"] for group in groups.values()
        if not existing_hash_paths.get(group["sha256"])
    }
    copy_bytes = sum(blob_info[digest][1] for digest in copy_hashes)
    return {
        "work_ids": work_ids,
        "groups": groups,
        "metadata": metadata,
        "blob_info": blob_info,
        "copy_hashes": copy_hashes,
        "copy_bytes": copy_bytes,
        "new_assets": new_asset_count,
        "existing_pairs": len(groups) - new_asset_count,
        "linked_rows": linked_count,
        "new_sources": source_metadata,
    }, {"existing_sources": existing_sources, "existing_assets": existing_assets}


def _extension(media_type: str | None) -> str:
    normalized = (media_type or "").split(";", 1)[0].strip().casefold()
    known = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif",
        "image/bmp": ".bmp", "image/tiff": ".tiff",
    }
    return known.get(normalized) or mimetypes.guess_extension(normalized) or ".img"


def _write_blob_files(
    source: sqlite3.Connection, plan: dict[str, Any], asset_dir: Path,
) -> tuple[dict[str, str], list[Path]]:
    files: dict[str, str] = {}
    created: list[Path] = []
    root = ROOT.resolve()
    asset_dir = asset_dir.resolve()
    if root not in asset_dir.parents:
        raise ValueError("Image destination must stay inside the ani_rag project")
    for digest, path in plan["metadata"].items():
        if path.get("local_path"):
            files[path["sha256"]] = path["local_path"]
    for digest in sorted(plan["copy_hashes"]):
        media_type, expected_size = plan["blob_info"][digest]
        row = source.execute(
            "SELECT binary_data FROM image_blobs WHERE sha256=?", (digest,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Image blob disappeared during import: {digest}")
        payload = bytes(row[0])
        if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != digest:
            raise RuntimeError(f"Image blob size or SHA-256 check failed: {digest}")
        destination = asset_dir / digest[:2] / f"{digest}{_extension(media_type)}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.stat().st_size != expected_size or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"Existing destination file failed hash verification: {destination.name}")
        else:
            temporary = destination.with_suffix(destination.suffix + ".part")
            temporary.write_bytes(payload)
            os.replace(temporary, destination)
            created.append(destination)
        files[digest] = str(destination.resolve())
    return files, created


def _apply_import(
    source: sqlite3.Connection, target_path: Path, plan: dict[str, Any],
    files: dict[str, str], asset_dir: Path,
) -> tuple[int, int, int]:
    target = sqlite3.connect(target_path, timeout=60)
    target.row_factory = sqlite3.Row
    target.execute("PRAGMA foreign_keys=ON")
    required_tables = {"rag_source", "rag_image_asset", "rag_image_work_link"}
    present = {row[0] for row in target.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required_tables <= present:
        target.close()
        raise RuntimeError("Target RAG database lacks the pre-provisioned image/source tables")
    now = datetime.now(timezone.utc).isoformat()
    source_count = 0
    asset_count = 0
    link_count = 0
    try:
        target.execute("BEGIN IMMEDIATE")
        for source_id, metadata in sorted(plan["new_sources"].items()):
            title = str(metadata.get("title") or metadata.get("publisher") or source_id)
            site_url = str(metadata.get("url") or "")
            if not _valid_url(site_url):
                site_url = next(
                    group["source_page_url"] for group in plan["groups"].values()
                    if group["source_id"] == source_id
                )
            configuration = json.dumps({
                "origin_project": "ani_me;nu",
                "origin_source_id": source_id,
                "imported_from_db": "generation_continuous_recovery_20261101.sqlite3",
                "provenance_only": True,
                "new_collection_approved": False,
            }, ensure_ascii=False, sort_keys=True)
            target.execute(
                """INSERT OR IGNORE INTO rag_source(
                    source_id,content_types_json,media_name,source_kind,collect_method,site_url,
                    source_grade,copyright_status,license_basis,is_collecting,configuration_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_id, "[]", title, "historical_image_provenance", "read_only_sqlite_import",
                 site_url, "unreviewed", "unknown", "historical_import_only; no new collection approval",
                 0, configuration, now, now),
            )
            source_count += target.execute("SELECT changes()").fetchone()[0]

        for (source_id, image_url), group in sorted(plan["groups"].items()):
            asset_id = group["asset_id"]
            existing = target.execute(
                "SELECT asset_id,content_sha256,local_path FROM rag_image_asset WHERE source_id=? AND source_url=? LIMIT 1",
                (source_id, image_url),
            ).fetchone()
            if existing:
                if existing["content_sha256"] != group["sha256"]:
                    raise RuntimeError(f"Existing image URL hash changed during import: {source_id}")
                asset_id = str(existing["asset_id"])
            else:
                path = files[group["sha256"]]
                if not Path(path).is_file():
                    raise RuntimeError(f"Prepared image file is missing: {group['sha256']}")
                data = group["representative"]
                blob_type, blob_size = plan["blob_info"][group["sha256"]]
                role = str(data.get("asset_kind") or "unknown")
                visual_match = data.get("visual_match")
                if visual_match:
                    role = f"{role}:{visual_match}"
                width = data.get("width")
                height = data.get("height")
                width = int(width) if isinstance(width, int) and width > 0 else None
                height = int(height) if isinstance(height, int) and height > 0 else None
                rights_basis = group["rights_basis"]
                rights_basis = str(rights_basis) if rights_basis else None
                rights_url = group["rights_evidence_url"]
                rights_url = str(rights_url) if rights_url else None
                target.execute(
                    """INSERT INTO rag_image_asset(
                        asset_id,source_id,source_url,source_page_url,source_native_id,image_role,alt_text,
                        local_path,content_sha256,media_type,byte_size,width,height,rights_basis,
                        rights_evidence_url,work_link_status,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (asset_id, source_id, image_url, group["source_page_url"], group["source_native_id"],
                     role, _image_alt_text(group), path, group["sha256"], blob_type, blob_size,
                     width, height, rights_basis, rights_url, "matched", now),
                )
                asset_count += 1
            for work_id in sorted(group["work_ids"]):
                link_id = f"iw_import_{asset_id}_{work_id}"
                cursor = target.execute(
                    """INSERT OR IGNORE INTO rag_image_work_link(
                        image_work_link_id,asset_id,menu_work_id,candidate_menu_work_id,status
                    ) VALUES(?,?,?,NULL,'matched')""",
                    (link_id, asset_id, work_id),
                )
                link_count += cursor.rowcount
        target.commit()
    except BaseException:
        target.rollback()
        raise
    finally:
        target.close()
    return source_count, asset_count, link_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--asset-dir", type=Path, default=DEFAULT_ASSET_DIR)
    parser.add_argument("--apply", action="store_true", help="write matching image assets into the temporary ani_rag SQLite database")
    args = parser.parse_args()
    if not args.target_db.is_file():
        raise FileNotFoundError(args.target_db)
    source = _read_only(args.source_db)
    target = _read_only(args.target_db)
    created_files: list[Path] = []
    database_committed = False
    try:
        plan, _existing = _build_plan(source, target, args.reference)
        target.rollback()
        target.close()
        target = None
        print(f"matched_RAG_work_ids={len(plan['work_ids'])}")
        print(f"image_metadata_rows={len(plan['groups'])}")
        print(f"new_image_rows={plan['new_assets']}")
        print(f"existing_image_rows={plan['existing_pairs']}")
        print(f"new_provenance_source_rows={len(plan['new_sources'])}")
        print(f"unique_work_links={plan['linked_rows']}")
        print(f"new_image_bytes={plan['copy_bytes']} ({plan['copy_bytes'] / (1024 ** 3):.3f} GiB)")
        if not args.apply:
            print("dry_run=true; no files or database rows were changed")
            return 0
        free_bytes = shutil.disk_usage(args.asset_dir.parent if args.asset_dir.parent.exists() else ROOT).free
        if free_bytes < plan["copy_bytes"] + 128 * 1024 * 1024:
            raise OSError("Insufficient free space for the bounded image import and safety margin")
        files, created_files = _write_blob_files(source, plan, args.asset_dir)
        source_count, asset_count, link_count = _apply_import(source, args.target_db, plan, files, args.asset_dir)
        database_committed = True
        print(f"imported_provenance_sources={source_count}")
        print(f"imported_image_rows={asset_count}")
        print(f"added_work_links={link_count}")
        print(f"new_image_files={len(created_files)}")
        print("source_collection_flags_changed=false")
        return 0
    except BaseException:
        if not database_committed:
            for path in created_files:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        raise
    finally:
        if target is not None:
            target.rollback()
            target.close()
        source.rollback()
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
