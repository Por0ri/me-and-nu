"""Read-only cover history admission and selection; no network or model calls."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ani_menu_agent.config import get_settings
from ani_menu_agent.cover_history import (
    CoverHistoryError,
    MAX_HISTORY_BYTES,
    load_cover_history,
    validate_cover_history_environment,
)
from ani_menu_agent.image_selection import (
    cover_usable_editorial_images,
    distinct_editorial_images,
    image_visual_keys,
)
from ani_menu_agent.models import ContentKind, ContentPackage, ImageAsset, RightsStatus, SourceEvidence


CONSUMER_JOB = "17e543c0-3d66-47e2-b4b3-955903e17fbd"
CONSUMER_ATTEMPT = "43185983-33b2-4b11-a442-2816e8594dc4"
ORIGIN_JOB = "a2719a87-a5db-4007-9a23-8b1eafc3f2db"
ORIGIN_ATTEMPT = "2bd65a75-768d-4519-b8bc-caf279dff30b"
ENV = ("ANI_MENU_COVER_HISTORY_FILE", "ANI_MENU_COVER_HISTORY_SHA256",
       "ANI_MENU_COVER_HISTORY_JOB_ID", "ANI_MENU_COVER_HISTORY_ATTEMPT_ID")


@pytest.fixture(autouse=True)
def isolated_cover_configuration(monkeypatch):
    for name in (*ENV, "OPENAI_API_KEY", "LLM_KEY"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def source():
    return SourceEvidence(id="official", title="Official announcement",
                          url="https://example.com/news/1", publisher="Official", is_official=True)


def image(name="old", fingerprint="a" * 64):
    return ImageAsset(id=name, url=f"https://example.com/images/{name}.jpg", source_url=source().url,
                      storage_key=f"asset-{name}", fingerprint=fingerprint, official=True,
                      rights_status=RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
                      rights_evidence_url=source().url, rights_checked_at="2026-11-16T00:00:00Z",
                      attribution_text="Official image", width=1200, height=1200,
                      download_status="downloaded")


def entry(request_id="previous-request", asset=None):
    return {"request_id": request_id, "image": (asset or image()).model_dump(mode="json"),
            "origin": {"job_id": ORIGIN_JOB, "generation_attempt_id": ORIGIN_ATTEMPT,
                       "result_sha256": "b" * 64}}


def document(entries=None):
    return {"schema_version": 1,
            "consumer": {"job_id": CONSUMER_JOB, "generation_attempt_id": CONSUMER_ATTEMPT},
            "entries": [entry()] if entries is None else entries}


def configure(monkeypatch, tmp_path, payload=None, *, raw=None):
    raw = raw if raw is not None else json.dumps(payload if payload is not None else document()).encode("utf-8")
    path = tmp_path / "cover-history.json"
    path.write_bytes(raw)
    for name, value in zip(ENV, (str(path), hashlib.sha256(raw).hexdigest(), CONSUMER_JOB, CONSUMER_ATTEMPT), strict=True):
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    return path


def assert_error(code):
    return pytest.raises(CoverHistoryError, match=code)


def test_unconfigured_and_explicit_empty_snapshot_preserve_db_only_behavior(monkeypatch, tmp_path):
    assert load_cover_history() == ()
    configure(monkeypatch, tmp_path, document([]))
    assert validate_cover_history_environment() == ()


def test_valid_metadata_is_read_only_and_never_opens_image_location(monkeypatch, tmp_path):
    asset = image().model_copy(update={"local_path": str(tmp_path / "does-not-exist.png")})
    path = configure(monkeypatch, tmp_path, document([entry(asset=asset)]))
    before = path.read_bytes()
    result, = validate_cover_history_environment()
    assert result.request_id == "previous-request"
    assert result.visual_keys == frozenset(image_visual_keys(asset))
    assert path.read_bytes() == before
    assert not Path(asset.local_path).exists()


@pytest.mark.parametrize("missing", ENV)
def test_partial_configuration_is_rejected(monkeypatch, tmp_path, missing):
    configure(monkeypatch, tmp_path)
    monkeypatch.delenv(missing)
    get_settings.cache_clear()
    with assert_error("incomplete_configuration"):
        load_cover_history()


@pytest.mark.parametrize("empty", ENV)
def test_explicit_empty_configuration_is_rejected(monkeypatch, tmp_path, empty):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv(empty, "")
    get_settings.cache_clear()
    with assert_error("incomplete_configuration"):
        load_cover_history()


def test_missing_file_and_hash_change_fail_closed(monkeypatch, tmp_path):
    path = configure(monkeypatch, tmp_path)
    path.write_bytes(path.read_bytes() + b" ")
    with assert_error("hash_mismatch"):
        load_cover_history()
    path.unlink()
    with assert_error("file_unavailable"):
        load_cover_history()


@pytest.mark.parametrize("location", ["relative.json", "directory"])
def test_relative_path_and_directory_rejected(monkeypatch, tmp_path, location):
    configure(monkeypatch, tmp_path)
    target = location if location == "relative.json" else str(tmp_path)
    monkeypatch.setenv(ENV[0], target)
    get_settings.cache_clear()
    with assert_error("absolute_path_required" if location == "relative.json" else "not_regular_file"):
        load_cover_history()


@pytest.mark.parametrize("parent_link", [False, True])
def test_symlink_file_or_parent_rejected(monkeypatch, tmp_path, parent_link):
    path = configure(monkeypatch, tmp_path)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(tmp_path if parent_link else path, target_is_directory=parent_link)
    except OSError:
        pytest.skip("This host cannot create symlinks; the Linux Worker suite must run this case")
    monkeypatch.setenv(ENV[0], str(alias / path.name if parent_link else alias))
    get_settings.cache_clear()
    with assert_error("symlink_not_allowed"):
        load_cover_history()


def test_file_and_entry_limits_are_enforced_before_parsing_images(monkeypatch, tmp_path):
    path = configure(monkeypatch, tmp_path)
    with path.open("wb") as stream:
        stream.truncate(MAX_HISTORY_BYTES + 1)
    with assert_error("file_too_large"):
        load_cover_history()
    configure(monkeypatch, tmp_path, document([{}] * 10_001))
    with assert_error("invalid_entry_count"):
        load_cover_history()


@pytest.mark.parametrize("field,value", [
    ("job_id", ORIGIN_JOB), ("generation_attempt_id", ORIGIN_ATTEMPT),
    ("job_id", CONSUMER_JOB.upper()), ("generation_attempt_id", CONSUMER_ATTEMPT.replace("-", "")),
])
def test_consumer_mismatch_and_noncanonical_uuid_rejected(monkeypatch, tmp_path, field, value):
    payload = document()
    payload["consumer"][field] = value
    configure(monkeypatch, tmp_path, payload)
    with assert_error("consumer_mismatch|invalid_uuid"):
        load_cover_history()


@pytest.mark.parametrize("mutate,code", [
    (lambda obj: obj.update(schema_version=True), "unsupported_schema_version"),
    (lambda obj: obj.update(unexpected=1), "invalid_schema"),
    (lambda obj: obj["entries"][0].update(request_id=""), "invalid_request_id"),
    (lambda obj: obj["entries"][0]["origin"].update(job_id="not-a-uuid"), "invalid_uuid"),
    (lambda obj: obj["entries"][0]["origin"].update(result_sha256="B" * 64), "invalid_sha256"),
    (lambda obj: obj["entries"][0]["image"].update(width=-1), "invalid_image"),
    (lambda obj: obj["entries"][0]["image"].update(source_url="not-http"), "invalid_image"),
    (lambda obj: obj["entries"][0]["image"].update(unknown_field="x"), "invalid_image"),
    (lambda obj: obj["entries"][0]["image"].update(content_bytes="secret"), "invalid_image"),
])
def test_invalid_schema_origin_and_image_rejected(monkeypatch, tmp_path, mutate, code):
    payload = document()
    mutate(payload)
    configure(monkeypatch, tmp_path, payload)
    with assert_error(code):
        load_cover_history()


@pytest.mark.parametrize("raw,code", [
    (b'{"schema_version":1,"schema_version":1}', "duplicate_json_key"),
    (b'{"nested":{"id":1,"id":2}}', "duplicate_json_key"),
    (b'{"value":NaN}', "nonfinite_json_number"),
    (b'{"value":Infinity}', "nonfinite_json_number"),
    (b'{"value":1e999}', "nonfinite_json_number"),
    (b'not json', "invalid_json"),
    (b'\xff', "invalid_json"),
])
def test_strict_json_rejects_duplicate_keys_nonfinite_and_invalid_encoding(monkeypatch, tmp_path, raw, code):
    configure(monkeypatch, tmp_path, raw=raw)
    with assert_error(code):
        load_cover_history()


def insert_cover(repository, request_id, asset, *, detail_asset=None):
    images = [asset, *([detail_asset] if detail_asset else [])]
    package = {"cards": [{"page": 1, "image_id": asset.id}],
               "images": [item.model_dump(mode="json") for item in images]}
    if detail_asset:
        package["cards"].append({"page": 2, "image_id": detail_asset.id})
    with repository.connect() as connection:
        connection.execute("INSERT INTO feed_queue(request_id,topic,package_json,queued_at) VALUES(?,?,?,?)",
                           (request_id, "test-only", json.dumps(package), "2026-11-16T00:00:00Z"))


def test_repository_combines_db_and_snapshot_and_excludes_own_request(monkeypatch, tmp_path):
    from ani_menu_agent.storage import AgentRepository
    previous, own, db_cover, db_detail = (image(name, value * 64) for name, value in
                                         (("previous", "a"), ("own", "b"), ("db", "c"), ("detail", "d")))
    configure(monkeypatch, tmp_path, document([entry("previous", previous), entry("current", own)]))
    repository = AgentRepository(tmp_path / "history.sqlite3")
    insert_cover(repository, "database-old", db_cover, detail_asset=db_detail)
    insert_cover(repository, "current", own)
    keys = set(repository.prior_cover_image_fingerprints(exclude_request_id="current"))
    assert keys == image_visual_keys(previous) | image_visual_keys(db_cover)
    assert keys.isdisjoint(image_visual_keys(own) | image_visual_keys(db_detail))


def test_same_cover_is_blocked_different_cover_and_body_reuse_remain_allowed(monkeypatch, tmp_path):
    from ani_menu_agent.storage import AgentRepository
    previous = image()
    alias = previous.model_copy(update={"id": "same-bytes-new-cdn", "url": "https://example.com/images/alias.jpg",
                                       "storage_key": "alias-key"})
    different = image("different", "c" * 64)
    configure(monkeypatch, tmp_path, document([entry(asset=previous)]))
    package = ContentPackage(request_id="new-request", topic="Official announcement", content_kind=ContentKind.INFORMATION,
                             sources=[source()], images=[alias, different])
    package.prior_image_fingerprints = AgentRepository(tmp_path / "selection.sqlite3").prior_cover_image_fingerprints(
        exclude_request_id=package.request_id)
    assert [asset.id for asset in cover_usable_editorial_images(package.images, package.sources, package)] == [different.id]
    assert {asset.id for asset in distinct_editorial_images(package.images, package.sources, package, card_role="detail")} == {
        alias.id, different.id}


def test_cli_checks_snapshot_before_registry_collection_or_llm(monkeypatch, tmp_path):
    from ani_menu_agent import cli
    from typer.testing import CliRunner

    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid history must stop before any registry, DB, collection, or LLM operation")

    path = configure(monkeypatch, tmp_path)
    path.unlink()
    for name in ("load_source_registry", "AgentRepository", "collect_official_page",
                 "extract_page_payload", "extract_page_image_text", "resolve_unmatched_work_with_llm"):
        monkeypatch.setattr(cli, name, forbidden)
    result = CliRunner().invoke(cli.app, ["crawl-generate", "--source", "one_piece_official",
        "--url", "https://one-piece.com/news/83418/index.html", "--content-type", "goods", "--expected-work-id", "8"])
    assert result.exit_code == 2
    assert "file_unavailable" in result.output


def test_generate_copy_deck_revalidates_snapshot_before_model_creation(monkeypatch, tmp_path):
    from ani_menu_agent import generation

    path = configure(monkeypatch, tmp_path)
    assert validate_cover_history_environment()
    path.write_bytes(path.read_bytes() + b" ")
    monkeypatch.setattr(generation, "get_settings", lambda: SimpleNamespace(openai_api_key="offline-fixture-never-sent"))
    monkeypatch.setattr(generation, "attach_menu_work_ids", lambda _package: None)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Tampered snapshot must stop before model construction")

    monkeypatch.setattr(generation, "ChatOpenAI", forbidden)
    package = ContentPackage(request_id="new-request", topic="Offline", content_kind=ContentKind.INFORMATION)
    with assert_error("hash_mismatch"):
        generation.generate_copy_deck(package)
