import csv
import json
import zlib
from pathlib import Path

import pytest

from ani_menu_agent import work_catalog
from ani_menu_agent.models import ContentPackage, Fact, SourceEvidence, StructuredWorkReference
from ani_menu_agent.sources.models import Source, SourceRegistry
from ani_menu_agent.storage import AgentRepository
from ani_menu_agent.work_catalog_registration import (
    register_new_work_from_source_title,
    register_unmatched_work_from_kitsu,
)


@pytest.fixture(autouse=True)
def isolated_work_lists(monkeypatch, tmp_path):
    monkeypatch.delenv("ME_NU_WORK_REGISTRY_DIR", raising=False)
    monkeypatch.delenv("ME_NU_WORK_CATALOG_DIR", raising=False)
    monkeypatch.setattr(work_catalog, "_CATALOG_DIRECTORY", tmp_path)
    monkeypatch.setattr(work_catalog, "_WORK_LIST_DIRECTORY", tmp_path / "catalog")
    _write_catalog(tmp_path / "catalog")
    work_catalog.refresh_work_catalog_caches()
    yield
    work_catalog.refresh_work_catalog_caches()


def _write_catalog(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    row = {
        "menu_work_id": "10",
        "title_ko": "Existing Work",
        "title_ko_source": "manual",
        "title_ko_check": "",
        "title_romaji": "Existing Work",
        "title_english": "Existing Work",
        "title_native": "",
        "first_year": "2020",
        "last_year": "2020",
        "n_entries": "1",
        "popularity": "1",
        "anilist_ids": "100",
    }
    with (directory / "menu_works.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row), lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)


def _kitsu_source_registry():
    return SourceRegistry(
        version=1,
        sources=[
            Source(
                id="kitsu_anime_catalog",
                name="Kitsu Anime Catalog API",
                url="https://kitsu.io/api/edge/anime",
                category="kitsu_anime_catalog",
                role="reference",
                collection="api",
                status="active",
                region="global",
                min_interval_seconds=0,
            )
        ],
    )


def _kitsu_payload(*rows: tuple[str, str]) -> dict:
    return {
        "data": [
            {
                "id": kitsu_id,
                "attributes": {
                    "canonicalTitle": title,
                    "titles": {
                        "en": title,
                        "en_jp": title,
                        "ja_jp": "新作アニメ",
                    },
                    "subtype": "TV",
                    "startDate": "2026-10-01",
                    "endDate": None,
                },
            }
            for kitsu_id, title in rows
        ]
    }


def _install_fake_kitsu(monkeypatch, payload: dict) -> None:
    class FakeResponse:
        content = json.dumps(payload).encode()

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return payload

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url):
            return FakeResponse()

    monkeypatch.setattr("ani_menu_agent.work_catalog_registration.httpx.Client", FakeClient)
    monkeypatch.setattr(
        "ani_menu_agent.work_catalog_registration.load_source_registry",
        _kitsu_source_registry,
    )
    monkeypatch.setattr("ani_menu_agent.work_catalog_registration.pace_host", lambda *_args: None)


def test_exact_new_kitsu_work_persists_title_and_repeatable_temporary_id(monkeypatch, tmp_path):
    _install_fake_kitsu(monkeypatch, _kitsu_payload(("50362", "Mysterious Anime")))
    official = tmp_path / "catalog" / "menu_works.csv"
    original = official.read_bytes()
    url = "https://example.org/news/new-work"
    audit = {}

    for _ in range(2):
        evidence = register_unmatched_work_from_kitsu(
            ["Mysterious Anime announces its October debut"], source_url=url, audit=audit
        )
        assert evidence is not None and evidence.menu_work_id == 1_050_362

    assert official.read_bytes() == original
    assert work_catalog.exact_catalog_work_id("Mysterious Anime") == 1_050_362
    assert not (tmp_path / "agent_works.csv").exists()
    assert not (official.parent / "menu_work_external_entries.csv").exists()
    with (official.parent / "agent_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        (row,) = csv.DictReader(stream)
    assert row["menu_work_id"] == "1050362" and row["title_english"] == "Mysterious Anime"
    with (tmp_path / "menu_work_external_entries.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        (link,) = csv.DictReader(stream)
    assert (link["external_id"], link["menu_work_id"]) == ("50362", "1050362")


def test_exact_existing_kitsu_title_reuses_the_only_matching_id(monkeypatch, tmp_path):
    _install_fake_kitsu(monkeypatch, _kitsu_payload(("9907", "Existing Work")))

    audit = {}
    evidence = register_unmatched_work_from_kitsu(["Existing Work"], audit=audit)

    assert evidence is not None
    assert evidence.menu_work_id == 10
    assert audit["status"] == "mapped_existing_work"


@pytest.mark.parametrize("registry_override", [None, "catalog", "registry"])
def test_mounted_catalog_and_separate_registry_override(monkeypatch, tmp_path, registry_override):
    catalog = tmp_path / "mount" / "catalog"
    _write_catalog(catalog)
    monkeypatch.setenv("ME_NU_WORK_CATALOG_DIR", str(catalog))
    registry = catalog
    if registry_override:
        registry = catalog.parent / registry_override
        monkeypatch.setenv("ME_NU_WORK_REGISTRY_DIR", str(registry))

    assert work_catalog.work_registry_directory() == registry
    external_dir = tmp_path if registry == catalog else registry
    assert work_catalog.work_external_entries_path() == external_dir / "menu_work_external_entries.csv"


def test_external_registration_is_visible_to_running_catalog_reader(tmp_path):
    title = "Freshly Discovered Work"
    assert work_catalog.exact_catalog_work_id(title) is None
    registry = tmp_path / "catalog" / "agent_works.csv"
    registry.write_text(f"menu_work_id,title_english\n1050362,{title}\n", encoding="utf-8")
    assert work_catalog.exact_catalog_work_id(title) == 1_050_362


def test_previous_kitsu_alias_yields_to_newly_registered_official_id(monkeypatch, tmp_path):
    title = "Mysterious Anime"
    _install_fake_kitsu(monkeypatch, _kitsu_payload(("50362", title)))
    assert register_unmatched_work_from_kitsu([title]).menu_work_id == 1_050_362
    official = tmp_path / "catalog" / "menu_works.csv"
    with official.open(encoding="utf-8-sig", newline="") as stream:
        (row,) = csv.DictReader(stream)
    row.update(menu_work_id="2569", title_ko=title, title_romaji=title, title_english=title)
    with official.open("a", encoding="utf-8", newline="") as stream:
        csv.DictWriter(stream, fieldnames=list(row), lineterminator="\n").writerow(row)
    assert register_unmatched_work_from_kitsu([title]).menu_work_id == 2569
    assert work_catalog.exact_catalog_work_id(title) == 2569


def test_kitsu_numeric_collision_keeps_prior_work_and_records_candidate(monkeypatch, tmp_path):
    path = tmp_path / "catalog" / "agent_works.csv"
    path.write_text("menu_work_id,title_english\n1050362,Different Anime\n", encoding="utf-8")
    original = path.read_bytes()
    _install_fake_kitsu(monkeypatch, _kitsu_payload(("50362", "Mysterious Anime")))
    assert register_unmatched_work_from_kitsu(["Mysterious Anime"]) is None
    assert original == path.read_bytes()
    with (path.parent / "discovered_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        (candidate,) = csv.DictReader(stream)
    assert (candidate["kitsu_id"], candidate["canonical_title"]) == ("50362", "Mysterious Anime")


def test_kitsu_confirmed_movie_title_can_map_to_existing_parent_id(monkeypatch, tmp_path):
    _install_fake_kitsu(monkeypatch, _kitsu_payload(("9906", "Existing Work: The Movie")))

    evidence = register_unmatched_work_from_kitsu(
        ["Existing Work: The Movie announces a new theatrical release"],
        existing_menu_work_id=10,
    )

    assert evidence is not None
    assert evidence.menu_work_id == 10
    assert evidence.basis == "kitsu_catalog_entry_title"
    with (tmp_path / "catalog" / "menu_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 1
    with (tmp_path / "menu_work_external_entries.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        external = list(csv.DictReader(stream))
    assert external[0]["external_id"] == "9906"
    assert external[0]["menu_work_id"] == "10"
    assert work_catalog.resolve_title_work_ids("Existing Work: The Movie") == [10]


def test_unknown_work_is_not_registered_without_full_catalog_title_match(monkeypatch, tmp_path):
    _install_fake_kitsu(monkeypatch, _kitsu_payload(("9902", "Mysterious Anime")))

    evidence = register_unmatched_work_from_kitsu(["A mysterious new anime was announced"])

    assert evidence is None
    with (tmp_path / "catalog" / "menu_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 1
    assert not (tmp_path / "menu_work_external_entries.csv").exists()


def test_ambiguous_kitsu_title_results_are_not_registered(monkeypatch, tmp_path):
    _install_fake_kitsu(
        monkeypatch,
        _kitsu_payload(("9903", "Mysterious Anime"), ("9904", "Mysterious Anime")),
    )

    evidence = register_unmatched_work_from_kitsu(["Mysterious Anime starts this fall"])

    assert evidence is None
    assert not (tmp_path / "catalog" / "agent_works.csv").exists()
    assert not (tmp_path / "discovered_works.csv").exists()
    with (tmp_path / "catalog" / "discovered_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert {row["kitsu_id"] for row in csv.DictReader(stream)} == {"9903", "9904"}


def test_structured_about_title_can_identify_new_kitsu_work(monkeypatch, tmp_path):
    _install_fake_kitsu(monkeypatch, _kitsu_payload(("9905", "Mysterious Anime")))

    evidence = register_unmatched_work_from_kitsu(
        ["Official event announcement"],
        structured_references=[StructuredWorkReference(relation="about", name="Mysterious Anime")],
    )

    assert evidence is not None and evidence.menu_work_id == 1_009_905


def test_title_temporary_ids_use_normalized_text_and_hold_crc_collisions(tmp_path):
    canonical = tmp_path / "catalog" / "menu_works.csv"
    original = canonical.read_bytes()
    expected = 2_000_000 + zlib.crc32(b"newtitle") % 1_000_000_000
    for title in ("New Title!", "Ｎｅｗ　Ｔｉｔｌｅ"):
        assert register_new_work_from_source_title(title, "https://example.org/release") == expected
    assert register_new_work_from_source_title("Existing Work", "https://example.org/release") == 10
    path = canonical.parent / "agent_works.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        (row,) = csv.DictReader(stream)
    assert row["menu_work_id"] == str(expected)
    # Force an occupied CRC without monkeypatching Python's global zlib module.
    original_row = path.read_text(encoding="utf-8-sig")
    other = 2_000_000 + zlib.crc32(b"otherwork") % 1_000_000_000
    path.write_text(original_row.replace(str(expected), str(other)), encoding="utf-8")
    work_catalog.refresh_work_catalog_caches()
    assert register_new_work_from_source_title("Other Work", "https://example.org/other") is None
    with (canonical.parent / "discovered_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        (held,) = csv.DictReader(stream)
    assert held["canonical_title"] == "Other Work"
    assert canonical.read_bytes() == original


@pytest.mark.parametrize("kitsu_id", ["50362", None])
def test_temporary_id_reaches_sqlite_generation_queue_with_article_and_erd_entity(
    monkeypatch, tmp_path, kitsu_id
):
    title = "Mysterious Anime"
    url = "https://eeo.today/media/news/fixture"
    if kitsu_id is not None:
        _install_fake_kitsu(monkeypatch, _kitsu_payload((kitsu_id, title)))
        evidence = register_unmatched_work_from_kitsu([title], source_url=url)
        assert evidence is not None
        work_id = evidence.menu_work_id
    else:
        work_id = register_new_work_from_source_title(title, url)
    assert work_id is not None
    source = SourceEvidence(
        id="eeo_events", title=title, url=url, publisher="EEO", role="editorial"
    )
    fact = Fact(
        id="release",
        statement="A new collection was announced.",
        source_ids=[source.id],
        verified=True,
    )
    package = ContentPackage(
        request_id="fixture-request",
        topic=title,
        content_kind="information",
        content_type="goods",
        menu_work_ids=[work_id],
        sources=[source],
        facts=[fact],
    )
    repository = AgentRepository(path=tmp_path / "queue.sqlite3")
    repository.persist_page_capture(
        {"source_id": source.id, "url": url, "title": title, "text": fact.statement},
        "JP",
        package=package,
    )
    repository.persist_information(package)
    repository.enqueue_pipeline_job(
        lane="time_sensitive",
        stage="generate",
        request_id=package.request_id,
        idempotency_key=f"{package.request_id}:generate:0",
        payload={"content_type": "goods"},
    )
    job = repository.claim_pipeline_job(stage="generate", lane="time_sensitive", worker_id="test")
    assert job is not None and job.request_id == package.request_id
    loaded = repository.load_content_package(job.request_id, "information", "goods")
    assert loaded.menu_work_ids == [work_id]
    assert loaded.facts[0].statement == fact.statement
    repository.persist_generated_content(loaded, "generated_pending_review")
    with repository.connect() as conn:
        entity = conn.execute(
            "SELECT e.name, e.external_id FROM entity e "
            "JOIN content_entity_link l ON e.entity_id=l.entity_id"
        ).fetchone()
        capture = conn.execute("SELECT raw_text FROM raw_document WHERE url=?", (url,)).fetchone()
    assert entity["name"] == title and json.loads(entity["external_id"])["menu_work_id"] == work_id
    assert capture["raw_text"] == fact.statement
