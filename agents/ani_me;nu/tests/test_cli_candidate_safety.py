import json

from typer.testing import CliRunner

from ani_menu_agent import cli
from ani_menu_agent.agents.crawlers.base import CrawlerRun, SourceCrawlResult
from ani_menu_agent.discovery import DiscoveryItem
from ani_menu_agent.models import ContentType, StructuredWorkReference
from ani_menu_agent.sources.models import Source, SourceRegistry
from ani_menu_agent.sources.page_collector import SourcePageCapture


def _source() -> Source:
    return Source(
        id="kitsu_anime_catalog",
        name="Kitsu catalog",
        url="https://kitsu.io/api/edge/anime",
        category="kitsu_anime_catalog",
        content_types=[ContentType.GUIDE, ContentType.CHARACTER, ContentType.CURATION],
        role="reference",
        collection="api",
        status="active",
        region="global",
    )


def _install_candidate(monkeypatch, content_types: list[str]) -> None:
    candidate = {
        "id": "candidate-1",
        "source_id": "kitsu_anime_catalog",
        "content_types": content_types,
        "title": "Naruto character profile",
        "url": "https://kitsu.io/anime/123",
    }

    class Repository:
        def load_discovery_candidate(self, candidate_id: str):
            assert candidate_id == "candidate-1"
            return candidate

    monkeypatch.setattr(cli, "AgentRepository", Repository)
    monkeypatch.setattr(
        cli,
        "load_source_registry",
        lambda: SourceRegistry(version=1, sources=[_source()]),
    )


def test_candidate_rejects_requested_type_outside_its_type_hint(monkeypatch) -> None:
    _install_candidate(monkeypatch, ["character"])

    result = CliRunner().invoke(
        cli.app,
        ["crawl-generate", "--candidate-id", "candidate-1", "--content-type", "goods"],
    )

    assert result.exit_code != 0
    assert "요청 유형" in result.output


def test_candidate_with_multiple_type_hints_requires_explicit_type(monkeypatch) -> None:
    _install_candidate(monkeypatch, ["guide", "character"])

    result = CliRunner().invoke(
        cli.app,
        ["crawl-generate", "--candidate-id", "candidate-1"],
    )

    assert result.exit_code != 0
    assert "--content-type" in result.output


def test_candidate_type_hint_is_expanded_from_registered_host_types(monkeypatch) -> None:
    _install_candidate(monkeypatch, ["character"])

    def stop_after_validation(*_args, **_kwargs):
        raise ValueError("collection reached")

    monkeypatch.setattr(cli, "collect_reference_page", stop_after_validation)
    result = CliRunner().invoke(
        cli.app,
        ["crawl-generate", "--candidate-id", "candidate-1", "--content-type", "guide"],
    )

    assert result.exit_code != 0
    assert "collection reached" in result.output


def test_candidate_without_catalog_work_match_is_skipped_after_identity_capture(
    monkeypatch, tmp_path
) -> None:
    _install_candidate(monkeypatch, ["guide", "character"])
    candidate = {
        "id": "candidate-1",
        "source_id": "kitsu_anime_catalog",
        "content_types": ["guide"],
        "title": "Octopath Traveler game review",
        "url": "https://kitsu.io/anime/123",
    }

    class Repository:
        def load_discovery_candidate(self, candidate_id: str):
            assert candidate_id == "candidate-1"
            return candidate

    monkeypatch.setattr(cli, "AgentRepository", Repository)
    monkeypatch.setattr(
        cli,
        "collect_reference_page",
        lambda *_args, **_kwargs: SourcePageCapture(
            source_id="kitsu_anime_catalog",
            title="Octopath Traveler game review",
            url="https://kitsu.io/anime/123",
            fetched_at="2026-10-23T00:00:00Z",
            text="A page about a game with no anime work identifier.",
            image_urls=[],
        ),
    )
    output = tmp_path / "candidate.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "crawl-generate",
            "--candidate-id",
            "candidate-1",
            "--content-type",
            "guide",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert '"status": "work_id_unresolved_skipped"' in output.read_text(encoding="utf-8")


def test_candidate_does_not_resolve_work_from_unrelated_summary_mentions(
    monkeypatch, tmp_path
) -> None:
    _install_candidate(monkeypatch, ["curation"])
    candidate = {
        "id": "candidate-1",
        "source_id": "kitsu_anime_catalog",
        "content_types": ["curation"],
        "title": "Kitsu rating ranking",
        "summary": "Top titles include Naruto, Frieren, and Chainsaw Man.",
        "url": "https://kitsu.io/api/edge/anime?sort=-averageRating",
    }

    class Repository:
        def load_discovery_candidate(self, candidate_id: str):
            assert candidate_id == "candidate-1"
            return candidate

    monkeypatch.setattr(cli, "AgentRepository", Repository)
    monkeypatch.setattr(
        cli,
        "collect_reference_page",
        lambda *_args, **_kwargs: SourcePageCapture(
            source_id="kitsu_anime_catalog",
            title="Kitsu rating ranking",
            url="https://kitsu.io/api/edge/anime?sort=-averageRating",
            fetched_at="2026-10-23T00:00:00Z",
            text='[{"work_title":"Naruto"},{"work_title":"Frieren"}]',
            image_urls=[],
        ),
    )
    output = tmp_path / "summary-only-work.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "crawl-generate",
            "--candidate-id",
            "candidate-1",
            "--content-type",
            "curation",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "work_id_unresolved_skipped"


def test_candidate_uses_structured_work_metadata_before_rejecting_unmatched_title(
    monkeypatch, tmp_path
) -> None:
    _install_candidate(monkeypatch, ["guide"])
    candidate = {
        "id": "candidate-1",
        "source_id": "kitsu_anime_catalog",
        "content_types": ["guide"],
        "title": "A profile with no franchise in its headline",
        "url": "https://kitsu.io/anime/123",
    }
    persisted = []

    class Repository:
        def load_discovery_candidate(self, candidate_id: str):
            return candidate

        def persist_page_capture(self, payload, region):
            persisted.append((payload, region))

    capture = SourcePageCapture(
        source_id="kitsu_anime_catalog",
        title="Anime profile",
        url="https://kitsu.io/anime/123",
        fetched_at="2026-10-23T00:00:00Z",
        text="A catalog profile with structured work metadata.",
        image_urls=[],
        structured_work_references=[
            StructuredWorkReference(relation="about", name="Naruto")
        ],
    )
    monkeypatch.setattr(cli, "AgentRepository", Repository)
    monkeypatch.setattr(cli, "collect_reference_page", lambda *_args, **_kwargs: capture)
    monkeypatch.setattr(
        cli,
        "extract_page_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("extraction reached")),
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "crawl-generate",
            "--candidate-id",
            "candidate-1",
            "--content-type",
            "guide",
            "--output",
            str(tmp_path / "structured-match.json"),
        ],
    )

    assert result.exit_code != 0
    assert "extraction reached" in result.output
    assert len(persisted) == 1
    payload = persisted[0][0]
    assert payload["menu_work_ids"] == [11]
    assert payload["work_match_evidence"][0]["basis"] == "structured_about"


def test_empty_source_body_is_skipped_before_image_persistence(monkeypatch, tmp_path) -> None:
    _install_candidate(monkeypatch, ["guide"])
    capture = SourcePageCapture(
        source_id="kitsu_anime_catalog",
        title="Naruto character profile",
        url="https://kitsu.io/anime/123",
        fetched_at="2026-10-23T00:00:00Z",
        text="  \n",
        image_urls=["https://kitsu.io/image.jpg"],
    )

    class Repository:
        def load_discovery_candidate(self, candidate_id: str):
            assert candidate_id == "candidate-1"
            return {
                "id": candidate_id,
                "source_id": "kitsu_anime_catalog",
                "content_types": ["guide"],
                "title": "Naruto character profile",
                "url": "https://kitsu.io/anime/123",
            }

        def persist_page_capture(self, *_args, **_kwargs):
            raise AssertionError("empty capture must not be persisted")

    monkeypatch.setattr(cli, "AgentRepository", Repository)
    monkeypatch.setattr(cli, "collect_reference_page", lambda *_args, **_kwargs: capture)
    output = tmp_path / "empty-source.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "crawl-generate",
            "--candidate-id",
            "candidate-1",
            "--content-type",
            "guide",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "needs_crawler_update"
    assert payload["qa_report"][0]["code"] == "SOURCE_BODY_EMPTY"


def test_missing_type_keywords_are_advisory_and_candidate_reaches_extraction(
    monkeypatch, tmp_path
) -> None:
    _install_candidate(monkeypatch, ["guide"])
    candidate = {
        "id": "candidate-1",
        "source_id": "kitsu_anime_catalog",
        "content_types": ["guide"],
        "title": "Naruto overview",
        "url": "https://kitsu.io/anime/123",
    }
    persisted = []

    class Repository:
        def load_discovery_candidate(self, candidate_id: str):
            return candidate

        def persist_page_capture(self, payload, region):
            persisted.append((payload, region))

    capture = SourcePageCapture(
        source_id="kitsu_anime_catalog",
        title="Overview",
        url="https://kitsu.io/anime/123",
        fetched_at="2026-10-23T00:00:00Z",
        text="A general page about the adaptation and its release.",
        image_urls=[],
    )
    monkeypatch.setattr(cli, "AgentRepository", Repository)
    monkeypatch.setattr(cli, "_candidate_menu_work_ids", lambda *_args: [123])
    monkeypatch.setattr(cli, "collect_reference_page", lambda *_args, **_kwargs: capture)
    def assert_extraction_reached(*_args, **_kwargs):
        raise RuntimeError("extraction reached")

    monkeypatch.setattr(cli, "extract_page_payload", assert_extraction_reached)
    output = tmp_path / "type-signal-mismatch.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "crawl-generate",
            "--candidate-id",
            "candidate-1",
            "--content-type",
            "guide",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code != 0
    assert "extraction reached" in result.output
    assert len(persisted) == 1
    assert persisted[0][0]["content_type"] == "guide"
    assert not any(persisted[0][0]["type_capture_checks"].values())
    assert persisted[0][0]["type_signal_review"]["status"] == "needs_semantic_review"


def test_crawl_type_routes_typed_candidates_through_existing_review_pipeline(
    monkeypatch, tmp_path
) -> None:
    candidate = DiscoveryItem(
        id="event-candidate-1",
        title="Naruto official exhibition",
        url="https://example.com/events/naruto",
        source_id="official_event_feed",
        content_types=["event"],
        source_role="primary",
    )
    second_candidate = candidate.model_copy(
        update={
            "id": "event-candidate-2",
            "title": "One Piece official exhibition",
            "url": "https://example.com/events/one-piece",
        }
    )
    crawler_run = CrawlerRun(
        content_type=ContentType.EVENT,
        items=[candidate, second_candidate],
        errors=[],
        persisted_count=1,
        source_results=[
            SourceCrawlResult(
                source_id="official_event_feed",
                status="succeeded_with_candidates",
                attempted=True,
                candidate_count=2,
            )
        ],
    )

    class Crawler:
        def run(self, **kwargs):
            assert kwargs["capture_images"] is False
            return crawler_run

    calls = []

    def run_full_candidate_pipeline(**kwargs):
        calls.append(kwargs)
        kwargs["output"].write_text(
            json.dumps({"status": "ready_for_feed", "request_id": "event-123"}),
            encoding="utf-8",
        )

    monkeypatch.setattr(cli, "get_type_crawler", lambda _content_type: Crawler())
    monkeypatch.setattr(cli, "crawl_generate", run_full_candidate_pipeline)
    output_dir = tmp_path / "crawl_runs"
    result = CliRunner().invoke(
        cli.app,
        [
            "crawl-type",
            "--content-type",
            "event",
            "--target-passed",
            "1",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["candidate_id"] == candidate.id
    assert calls[0]["content_type"] == "event"
    summaries = list(output_dir.rglob("run_summary.json"))
    assert len(summaries) == 1
    payload = json.loads(summaries[0].read_text(encoding="utf-8"))
    assert payload["outcomes"][0]["status"] == "ready_for_feed"


def test_crawl_type_preserves_duplicate_skip_instead_of_counting_pipeline_error(
    monkeypatch, tmp_path
) -> None:
    candidate = DiscoveryItem(
        id="duplicate-candidate",
        title="Naruto official exhibition",
        url="https://example.com/events/naruto",
        source_id="official_event_feed",
        content_types=["event"],
        source_role="primary",
    )
    crawler_run = CrawlerRun(
        content_type=ContentType.EVENT,
        items=[candidate],
        errors=[],
        persisted_count=1,
        source_results=[],
    )

    class Crawler:
        def run(self, **kwargs):
            return crawler_run

    def write_duplicate_then_raise(**kwargs):
        kwargs["output"].write_text(
            json.dumps({"status": "duplicate_skipped", "duplicate_of": {"request_id": "old"}}),
            encoding="utf-8",
        )
        raise ValueError("duplicate candidate")

    monkeypatch.setattr(cli, "get_type_crawler", lambda _content_type: Crawler())
    monkeypatch.setattr(cli, "crawl_generate", write_duplicate_then_raise)
    output_dir = tmp_path / "duplicate_run"
    result = CliRunner().invoke(
        cli.app,
        [
            "crawl-type",
            "--content-type",
            "event",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    summary_path = next(output_dir.rglob("run_summary.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["outcomes"][0]["status"] == "duplicate_skipped"


def test_humor_candidate_uses_trend_only_unverified_package_and_shared_graph(
    monkeypatch, tmp_path
) -> None:
    source = Source(
        id="public_fandom_trends",
        name="Public fandom trends",
        url="https://social.example/api/trends",
        category="community_trend",
        content_types=[ContentType.HUMOR],
        role="trend",
        collection="api",
        status="trend_only",
        region="global",
    )
    captured = {}

    class Repository:
        def find_duplicate_content(self, _package):
            return None

        def prepare_lifecycle(self, package):
            captured["prepared"] = package
            return package

    repository = Repository()
    monkeypatch.setattr(
        cli,
        "load_source_registry",
        lambda: SourceRegistry(version=1, sources=[source]),
    )
    monkeypatch.setattr(cli, "AgentRepository", lambda: repository)
    monkeypatch.setattr(cli, "_candidate_menu_work_ids", lambda *_args: [123])
    monkeypatch.setattr(cli, "_add_same_work_catalog_images", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        cli,
        "research_humor",
        lambda state: captured.update(package=state["package"]),
    )
    monkeypatch.setattr(
        cli,
        "image_curator",
        lambda state: captured.update(image_repository=state["repository"]),
    )

    def fake_generate(request_id, kind, requested_content_type):
        captured["generation_args"] = (request_id, kind, requested_content_type)
        return captured["prepared"], {
            "status": "ready_for_feed",
            "usage_records": [],
            "package": captured["prepared"].model_dump(mode="json"),
        }

    monkeypatch.setattr(cli, "_run_generation_from_db", fake_generate)
    monkeypatch.setattr(cli, "_export_rendered_cards", lambda *_args, **_kwargs: [])
    candidate = {
        "id": "trend-1",
        "title": "나루토 팬덤 토론",
        "url": "https://social.example/@fan/123",
        "source_id": source.id,
        "published_at": "2026-10-23T10:00:00Z",
        "summary": "나루토와 사스케의 대결 조건을 두고 팬들이 재미있게 의견을 나눴다.",
    }

    result = cli._crawl_generate_humor_candidate(candidate, tmp_path / "humor.json")
    package = captured["prepared"]

    assert result["status"] == "ready_for_feed"
    assert package.content_kind.value == "humor"
    assert package.content_type == "humor"
    assert package.facts[0].verified is False
    assert package.sources[0].role == "trend"
    assert captured["image_repository"] is repository
    assert captured["generation_args"] == (
        package.request_id,
        cli.ContentKind.HUMOR,
        "humor",
    )

