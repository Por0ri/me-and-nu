import csv
import json
from pathlib import Path

from ani_menu_agent import work_catalog
from ani_menu_agent.models import StructuredWorkReference
from ani_menu_agent.sources.models import Source, SourceRegistry
from ani_menu_agent.work_catalog_registration import register_unmatched_work_from_kitsu


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


def test_exact_kitsu_title_registers_next_id_and_future_title_matching(monkeypatch, tmp_path):
    _write_catalog(tmp_path)
    monkeypatch.setattr(work_catalog, "_CATALOG_DIRECTORY", tmp_path)
    work_catalog.load_work_catalog.cache_clear()
    work_catalog._aliases.cache_clear()
    _install_fake_kitsu(monkeypatch, _kitsu_payload(("9901", "Mysterious Anime")))

    evidence = register_unmatched_work_from_kitsu(
        ["Mysterious Anime announces its October debut"]
    )

    assert evidence is not None
    assert evidence.menu_work_id == 11
    assert evidence.basis == "kitsu_catalog_title"
    assert work_catalog.resolve_title_work_ids("Mysterious Anime") == [11]
    with (tmp_path / "menu_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        registered = next(row for row in csv.DictReader(stream) if row["menu_work_id"] == "11")
    assert registered["title_english"] == "Mysterious Anime"
    assert registered["anilist_ids"] == ""
    with (tmp_path / "menu_work_external_entries.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        external = list(csv.DictReader(stream))
    assert external[0]["external_id"] == "9901"
    assert external[0]["menu_work_id"] == "11"


def test_unknown_work_is_not_registered_without_full_catalog_title_match(
    monkeypatch, tmp_path
):
    _write_catalog(tmp_path)
    monkeypatch.setattr(work_catalog, "_CATALOG_DIRECTORY", tmp_path)
    work_catalog.load_work_catalog.cache_clear()
    work_catalog._aliases.cache_clear()
    _install_fake_kitsu(monkeypatch, _kitsu_payload(("9902", "Mysterious Anime")))

    evidence = register_unmatched_work_from_kitsu(["A mysterious new anime was announced"])

    assert evidence is None
    with (tmp_path / "menu_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 1
    assert not (tmp_path / "menu_work_external_entries.csv").exists()


def test_ambiguous_kitsu_title_results_are_not_registered(monkeypatch, tmp_path):
    _write_catalog(tmp_path)
    monkeypatch.setattr(work_catalog, "_CATALOG_DIRECTORY", tmp_path)
    work_catalog.load_work_catalog.cache_clear()
    work_catalog._aliases.cache_clear()
    _install_fake_kitsu(
        monkeypatch,
        _kitsu_payload(("9903", "Mysterious Anime"), ("9904", "Mysterious Anime")),
    )

    evidence = register_unmatched_work_from_kitsu(["Mysterious Anime starts this fall"])

    assert evidence is None
    with (tmp_path / "menu_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 1


def test_structured_about_title_can_register_catalog_confirmed_new_work(
    monkeypatch, tmp_path
):
    _write_catalog(tmp_path)
    monkeypatch.setattr(work_catalog, "_CATALOG_DIRECTORY", tmp_path)
    work_catalog.load_work_catalog.cache_clear()
    work_catalog._aliases.cache_clear()
    _install_fake_kitsu(monkeypatch, _kitsu_payload(("9905", "Mysterious Anime")))

    evidence = register_unmatched_work_from_kitsu(
        ["Official event announcement"],
        structured_references=[
            StructuredWorkReference(relation="about", name="Mysterious Anime")
        ],
    )

    assert evidence is not None
    assert evidence.menu_work_id == 11
