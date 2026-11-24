import json

import pytest
from typer.testing import CliRunner

from ani_menu_agent import cli
from ani_menu_agent.agents.crawlers.base import CrawlerRun, SourceCrawlResult
from ani_menu_agent.discovery import DiscoveryItem
from ani_menu_agent.discovery import parse_anime_schedule
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


@pytest.mark.parametrize(("limit", "selected", "lookups"), [(1, [1], 2), (2, [1, 3], 4)])
def test_diverse_turn_stops_matching_once_its_first_round_is_full(monkeypatch, limit, selected, lookups):
    source = _source()
    candidates = [
        DiscoveryItem(id=str(i), source_id=source.id, title=f"Candidate {i}", url=f"https://kitsu.io/anime/{i}")
        for i in range(235)
    ]
    calls = []

    def match(candidate, _source):
        i = int(candidate["id"])
        calls.append(i)
        if i == 0:
            return []
        return [11] if i in (1, 2) else [12]

    monkeypatch.setattr(cli, "load_source_registry", lambda: SourceRegistry(version=1, sources=[source]))
    monkeypatch.setattr(cli, "_candidate_menu_work_ids", match)
    assert cli._diversify_candidate_items(candidates, limit) == [candidates[i] for i in selected]
    assert calls == list(range(lookups))


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


def _mock_unmatched_catalog_registration(monkeypatch):
    queries = []

    def no_exact_match(titles, **_kwargs):
        queries.append(list(titles))
        return []

    monkeypatch.setattr(cli, "resolve_unmatched_work_with_llm", no_exact_match)
    return queries


@pytest.mark.parametrize("stamp, expected", [
    ("Sun, 22 Nov 2026 15:00:00 +0000", "2026-11-22T15:00:00+00:00"),
    (None, None),
])
def test_episode_feed_notice_is_persisted_separately_from_next_episode_schedule(monkeypatch, tmp_path, stamp, expected):
    source = Source(id="anime_schedule_rss", name="AnimeSchedule RAW RSS", url="https://animeschedule.net/jpnrss.xml",
                    category="schedule", content_types=[ContentType.NEWS], role="discovery", collection="rss",
                    status="active", region="global")
    title = "Episode 9 of Naruto is out!"
    url = "https://animeschedule.net/anime/naruto"
    rss_entry = {"id": "rss-9", "source_id": source.id, "content_types": ["news"], "title": title,
                 "summary": "Episode 9 has been released natively!", "url": url, "published_at": stamp}
    capture = SourcePageCapture(source_id=source.id, title="Naruto", url=url, fetched_at="2026-11-23T00:00:00Z",
                                text="Ep 10 Raw: 6d 2h 26m", image_urls=[],
                                structured_work_references=[StructuredWorkReference(relation="about", name="Naruto")])
    repo = cli.AgentRepository(path=tmp_path / "feed.sqlite3")
    monkeypatch.setattr(cli, "AgentRepository", lambda: repo)
    monkeypatch.setattr(repo, "load_discovery_candidate", lambda key: rss_entry)
    monkeypatch.setattr(cli, "load_source_registry", lambda: SourceRegistry(version=1, sources=[source]))
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: capture)
    persist = repo.persist_page_capture
    def stop_after_persist(*args, **kwargs):
        persist(*args, **kwargs)
        raise RuntimeError("end of storage check")
    monkeypatch.setattr(repo, "persist_page_capture", stop_after_persist)
    result = CliRunner().invoke(cli.app, ["crawl-generate", "--candidate-id", "rss-9", "--content-type", "news",
                                         "--prepare-only", "--output", str(tmp_path / "result.json")])
    assert "end of storage check" in result.output, (result.output, result.exception)
    with repo.connect() as db:
        stored = db.execute("SELECT title,raw_text,published_at,raw_payload FROM raw_document WHERE url=?", (url,)).fetchone()
    assert stored[0] == title
    assert stored[1] == title + "\n" + rss_entry["summary"]
    assert stored[2] == expected
    raw = json.loads(stored[3])
    assert raw["menu_work_ids"] == [11]
    assert raw["discovery_feed_url"] == str(source.url)
    assert raw["series_profile_text"] == capture.text


def test_candidate_rejects_requested_type_outside_its_type_hint(monkeypatch) -> None:
    _install_candidate(monkeypatch, ["character"])

    result = CliRunner().invoke(
        cli.app,
        ["crawl-generate", "--candidate-id", "candidate-1", "--content-type", "goods"],
    )

    assert result.exit_code != 0
    assert "요청 유형" in result.output


def test_two_rss_episodes_keep_separate_candidates_documents_and_generation_jobs(monkeypatch, tmp_path):
    from datetime import UTC, datetime
    from email.utils import format_datetime
    from hashlib import sha1

    from ani_menu_agent import storage
    from ani_menu_agent.pipelines.common.jobs import PipelineLane, PipelineStage

    url = "https://animeschedule.net/anime/naruto"
    published = format_datetime(datetime.now(UTC))
    feed = "".join(
        f"<item><title>Episode {n} of Naruto is out!</title><link>{url}</link>"
        f"<guid isPermaLink='false'>Naruto Episode {n}</guid><pubDate>{published}</pubDate>"
        f"<description>Episode {n} released natively!</description></item>" for n in (8, 9)
    )
    def parse(items):
        return parse_anime_schedule(f"<rss version='2.0'><channel>{items}</channel></rss>".encode())

    first, second = parse(feed)
    assert first.id != second.id
    assert [item.id for item in parse(feed)] == [first.id, second.id]
    legacy = parse(f"<item><title>{first.title}</title><link>{url}</link></item>")[0]
    assert legacy.feed_entry_guid is None
    assert legacy.id == "anime_schedule_rss-" + sha1(url.encode()).hexdigest()[:12]
    source = Source(id="anime_schedule_rss", name="AnimeSchedule", url="https://animeschedule.net/jpnrss.xml",
                    category="schedule", content_types=[ContentType.NEWS], role="discovery",
                    collection="rss", status="active", region="global")
    profile = SourcePageCapture(source_id=source.id, title="Naruto", url=url, fetched_at=datetime.now(UTC).isoformat(),
                                text="Ep 10 Raw: 6d 2h", image_urls=[],
                                structured_work_references=[StructuredWorkReference(relation="about", name="Naruto")])
    repo = cli.AgentRepository(path=tmp_path / "episodes.sqlite3")
    monkeypatch.setattr(cli, "AgentRepository", lambda: repo)
    monkeypatch.setattr(storage.get_settings(), "duplicate_baseline_path", None)
    monkeypatch.setattr(cli, "load_source_registry", lambda: SourceRegistry(version=1, sources=[source]))
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: profile)
    monkeypatch.setattr(cli, "_add_same_work_catalog_images", lambda *_a, **_kw: 0)
    monkeypatch.setattr(cli, "image_curator", lambda state: state)
    repo.persist_discovery([item.model_dump(mode="json") for item in (first, second)], "all")
    outcomes = []
    output = tmp_path / "result.json"
    for item in (first, second, first):
        result = CliRunner().invoke(cli.app, ["crawl-generate", "--candidate-id", item.id, "--content-type", "news",
                                             "--prepare-only", "--output", str(output)])
        assert result.exit_code == 0, (result.output, result.exception)
        outcomes.append(json.loads(output.read_text(encoding="utf-8"))["request_id"])
    assert outcomes[0] != outcomes[1]
    assert outcomes[0] == outcomes[2]
    with repo.connect() as db:
        rows = db.execute("SELECT title, external_id, raw_text FROM raw_document WHERE url=? ORDER BY title", (url,)).fetchall()
        jobs = db.execute("SELECT request_id FROM pipeline_jobs WHERE stage='generate'").fetchall()
    assert [row[0] for row in rows] == [first.title, second.title]
    assert len({row[1] for row in rows}) == 2
    assert [row[2] for row in rows] == [item.title + "\n" + item.summary for item in (first, second)]
    assert sorted(row[0] for row in jobs) == sorted(outcomes[:2])

    # An old queue entry recorded under the season URL only blocks its original episode.
    repo.persist_discovery([legacy.model_dump(mode="json")], "all")
    old_request = "crawl-" + sha1(url.encode()).hexdigest()[:14]
    repo.enqueue_pipeline_job(lane=PipelineLane.TIME_SENSITIVE, stage=PipelineStage.GENERATE,
                              idempotency_key=f"{old_request}:generate:0", request_id=old_request, payload={})
    repo.persist_candidate_attempt("news", legacy.model_dump(mode="json"),
                                   {"status": "generation_pending", "request_id": old_request})
    for item, expected in ((first, "duplicate_evidence_persisted"), (second, "generation_pending")):
        result = CliRunner().invoke(cli.app, ["crawl-generate", "--candidate-id", item.id, "--content-type", "news",
                                             "--prepare-only", "--output", str(output)])
        assert result.exit_code == 0, (result.output, result.exception)
        assert json.loads(output.read_text(encoding="utf-8"))["status"] == expected
    selected = []
    def submit(**kwargs):
        selected.append(kwargs["candidate_id"])
        kwargs["output"].write_text(json.dumps({"status": "generation_pending", "request_id": outcomes[1]}),
                                    encoding="utf-8")
    monkeypatch.setattr(cli, "crawl_generate", submit)
    result = CliRunner().invoke(cli.app, ["crawl-type", "--content-type", "news", "--stored-candidates-only",
                                         "--enqueue-only", "--limit", "5", "--no-discover-sources",
                                         "--output-dir", str(tmp_path / "crawl")])
    assert result.exit_code == 0, (result.output, result.exception)
    assert selected == [second.id]


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
    catalog_queries = _mock_unmatched_catalog_registration(monkeypatch)
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
    assert catalog_queries == [[candidate["title"], candidate["title"]]]


def test_candidate_does_not_resolve_work_from_unrelated_summary_mentions(
    monkeypatch, tmp_path
) -> None:
    _install_candidate(monkeypatch, ["curation"])
    catalog_queries = _mock_unmatched_catalog_registration(monkeypatch)
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
    assert catalog_queries == [["Kitsu rating ranking", "Kitsu rating ranking"]]


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
    extracted = []

    class Repository:
        def load_discovery_candidate(self, candidate_id: str):
            return candidate

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
    def extract(payload):
        extracted.append(payload)
        raise RuntimeError("extraction reached")

    monkeypatch.setattr(cli, "extract_page_payload", extract)

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
    assert len(extracted) == 1
    payload = extracted[0]
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
    extracted = []

    class Repository:
        def load_discovery_candidate(self, candidate_id: str):
            return candidate

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
    def assert_extraction_reached(payload):
        extracted.append(payload)
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
    assert len(extracted) == 1
    assert extracted[0]["content_type"] == "guide"
    assert not any(extracted[0]["type_capture_checks"].values())
    assert extracted[0]["type_signal_review"]["status"] == "needs_semantic_review"


@pytest.mark.parametrize("enqueue_only", [False, True])
def test_crawl_type_routes_typed_candidates_through_existing_review_pipeline(
    monkeypatch, tmp_path, enqueue_only
) -> None:
    candidate = DiscoveryItem(
        id="event-candidate-1",
        title="Naruto official exhibition",
        url="https://example.com/events/naruto",
        source_id="official_event_feed",
        content_types=["event"],
        source_role="primary",
    )
    second_candidate = DiscoveryItem.model_validate(
        {
            **candidate.model_dump(),
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
        assert kwargs["prepare_only"] is enqueue_only
        status = "generation_pending" if enqueue_only else "ready_for_feed"
        kwargs["output"].write_text(
            json.dumps({"status": status, "request_id": "event-123", "menu_work_ids": [11],
                        "fact_count": 1, "image_count": 6, "source_count": 1}),
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
            *(["--enqueue-only", "--limit", "1"] if enqueue_only else ["--target-passed", "1"]),
            "--no-discover-sources",
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
    outcome = payload["outcomes"][0]
    status = "generation_pending" if enqueue_only else "ready_for_feed"
    assert payload["funnel"]["status_counts"] == {status: 1}
    assert payload["passed_this_run"] == (0 if enqueue_only else 1)
    assert outcome["failure_stage"] is None
    assert payload["funnel"]["failure_stage_counts"] == {}
    if enqueue_only:
        assert "생성 대기 1건" in result.output
        assert outcome["terminal_stage"] == "generation_queue"
        assert outcome["menu_work_ids"] == [11]
        assert (outcome["fact_count"], outcome["image_count"], outcome["source_count"]) == (1, 6, 1)


def test_discover_summarizes_dataclass_source_results_without_crashing(
    monkeypatch, tmp_path
) -> None:
    source = Source(
        id="recal_kr",
        name="RE:CAL offline events",
        url="https://recal.kr/ko/c/offline",
        category="event",
        content_types=[ContentType.EVENT],
        role="editorial",
        collection="html",
        status="active",
        region="KR",
        discovery_enabled=True,
    )
    crawler_run = CrawlerRun(
        content_type=ContentType.EVENT,
        items=[],
        errors=[],
        persisted_count=0,
        source_results=[
            SourceCrawlResult(
                source_id="recal_kr",
                status="succeeded_empty",
                attempted=True,
                pages_scanned=1,
                page_candidate_counts=[0],
                pagination_stop_reason="no_new_candidates",
            )
        ],
    )

    class Crawler:
        def run(self, **_kwargs):
            return crawler_run

    class Repository:
        pass

    monkeypatch.setattr(cli, "get_type_crawler", lambda _content_type: Crawler())
    monkeypatch.setattr(cli, "AgentRepository", Repository)
    monkeypatch.setattr(cli, "run_source_discovery", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        cli,
        "load_source_registry",
        lambda: SourceRegistry(version=1, sources=[source]),
    )
    output = tmp_path / "discover.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "discover",
            "--content-type",
            "event",
            "--output",
            str(output),
            "--no-capture-images",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source_run_results"][0]["source_id"] == "recal_kr"
    assert payload["source_coverage"][0]["candidates"] == 0


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
            "--no-discover-sources",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    summary_path = next(output_dir.rglob("run_summary.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["outcomes"][0]["status"] == "duplicate_skipped"
    assert summary["funnel"]["failure_stage_counts"] == {"duplicate_check": 1}


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

    def fake_generate(request_id, kind, requested_content_type, *, initial_usage_records, repository):
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
        "community_posts": [
            {"url": "https://social.example/@fan/123", "text": "나는 나루토가 마지막에 어떤 선택을 하는지부터 다시 보고 싶었어."},
            {"url": "https://social.example/@reader/456", "text": "나는 같은 장면에서 사스케가 먼저 손을 내밀지 고민하는 모습이 기억나."},
        ],
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
