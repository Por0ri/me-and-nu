import csv
import zlib

from ani_rag import catalog
from ani_rag.storage import sqlite_core as storage
from ani_rag.models import WorkRegistration


def test_temporary_work_id_uses_kitsu_namespace_when_available():
    assert catalog.temporary_work_id(
        "Example Anime", {"kitsu_anime_catalog": "50362"}
    ) == 1_050_362


def test_temporary_work_id_is_stable_from_normalized_title():
    title = "  Ｎｅｗ　Anime!  "
    normalized = catalog._temporary_title_key(title)
    expected = 2_000_000 + zlib.crc32(normalized.encode("utf-8")) % 1_000_000_000

    assert catalog.temporary_work_id(title) == expected
    assert catalog.temporary_work_id("new-anime") == expected


def test_rag_registration_writes_shared_temp_catalog_and_kitsu_crosswalk(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DEFAULT_REFERENCE_DIR", tmp_path)
    registration = WorkRegistration(
        name="새 작품",
        aliases=("New Work",),
        external_ids={"kitsu_anime_catalog": "50362"},
        evidence_quote="새 작품 공식 발표",
    )

    storage._persist_agent_work(1_050_362, registration)

    with (tmp_path / "agent_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        work = next(csv.DictReader(stream))
    assert work["menu_work_id"] == "1050362"
    assert work["title_ko"] == "새 작품"
    assert work["title_english"] == "New Work"
    with (tmp_path / "menu_work_external_entries.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        external = next(csv.DictReader(stream))
    assert external["menu_work_id"] == "1050362"
    assert external["external_id"] == "50362"
