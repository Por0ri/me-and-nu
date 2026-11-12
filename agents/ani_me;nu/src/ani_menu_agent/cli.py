"""개발 환경 점검과 구조화 초안 검수 CLI."""

import hashlib
import importlib.metadata
import json
import re
import signal
import sys
import threading
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Annotated
from urllib.parse import urlparse

import httpx
import typer
from rich.console import Console
from rich.table import Table

from ani_menu_agent.agents.content_generation.renderer import render_package_cards
from ani_menu_agent.agents.crawlers import get_type_crawler
from ani_menu_agent.agents.crawlers.base import profile_signal_checks
from ani_menu_agent.agents.humor import research_humor
from ani_menu_agent.agents.image import image_curator
from ani_menu_agent.agents.information import research_information
from ani_menu_agent.classification import infer_strong_content_type, infer_toei_press_type
from ani_menu_agent.config import get_settings
from ani_menu_agent.discovery import (
    DiscoveryItem,
    collect_candidates,
    rank_candidates,
    supports_discovery_adapter,
)
from ani_menu_agent.extraction import extract_page_capture, extract_page_payload
from ani_menu_agent.generation import estimate_gpt_6_luna_cost
from ani_menu_agent.graph import build_graph
from ani_menu_agent.markdown_export import render_markdown
from ani_menu_agent.models import (
    CONTENT_TYPE_LABELS,
    ContentKind,
    ContentPackage,
    CopyDeck,
    Fact,
    ImageAsset,
    RightsStatus,
    SourceEvidence,
)
from ani_menu_agent.policy import POLICY
from ani_menu_agent.quality import run_quality_checks, score_quality
from ani_menu_agent.readiness import (
    is_automated_humor_post,
    supported_verified_fact_type,
)
from ani_menu_agent.source_discovery import run_source_discovery
from ani_menu_agent.source_freshness import (
    KST,
    candidate_is_stale,
    expiry_evidence,
    immediate_news_action_expiry,
)
from ani_menu_agent.sources import load_source_registry
from ani_menu_agent.sources.image_ocr import extract_page_image_text
from ani_menu_agent.sources.kitsu_images import collect_kitsu_work_images
from ani_menu_agent.sources.page_collector import collect_official_page, collect_reference_page
from ani_menu_agent.storage import AgentRepository, discovery_candidate_fingerprint
from ani_menu_agent.work_aliases import find_named_works
from ani_menu_agent.work_catalog import (
    load_work_catalog,
    resolve_menu_work_ids,
    resolve_work_matches,
)
from ani_menu_agent.work_match_fallback import resolve_unmatched_work_with_llm
from ani_menu_agent.work_type_coverage import build_work_type_coverage, find_work_ids

app = typer.Typer(no_args_is_help=True)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
console = Console(legacy_windows=False)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PATH = PROJECT_ROOT / "examples" / "sample_content.json"
RECAL_EXPIRY_REVISION = "recal-event-period-v2"


def _minimal_period_capture(payload: dict, temporal: dict) -> dict:
    """Keep provenance and the expiry decision while dropping body and assets."""
    retained = {
        key: payload.get(key)
        for key in (
            "source_id", "title", "url", "fetched_at", "published_at",
            "content_type", "discovery_candidate_id", "discovery_url",
            "discovery_title", "menu_work_ids", "work_match_evidence",
        )
        if payload.get(key) is not None
    }
    retained.update(
        {
            "main_text": "",
            "text": "",
            "image_urls": [],
            "image_alt_text": {},
            "image_source_pages": {},
            "image_source_titles": {},
            "image_asset_kinds": {},
            "image_depicted_entities": {},
            "images": [],
            "linked_page_evidence": [],
            "temporal_expiry": temporal,
        }
    )
    if temporal.get("end_date"):
        retained["event_end" if payload.get("content_type") == "event" else "sale_end"] = temporal[
            "end_date"
        ]
    return retained


def _load_package(path: Path) -> ContentPackage:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ContentPackage.model_validate(payload)


def _enrich_candidate_outcome(outcome: dict) -> dict:
    """Attach compact stage and blocker diagnostics to a candidate attempt."""
    enriched = dict(outcome)
    result_path = Path(str(enriched.get("output") or ""))
    result: dict = {}
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result = {}

    trace = result.get("trace") or enriched.get("trace") or []
    qa_items = result.get("qa_report") or enriched.get("qa_report") or []
    if isinstance(qa_items, dict):
        qa_items = qa_items.get("issues") or qa_items.get("hard_blockers") or []
    quality_report = result.get("quality_report") or enriched.get("quality_report") or {}
    if not qa_items and isinstance(quality_report, dict):
        qa_items = quality_report.get("issues") or quality_report.get("hard_blockers") or []
    if not isinstance(qa_items, list):
        qa_items = []

    reason_codes = [
        str(item.get("code"))
        for item in qa_items
        if isinstance(item, dict) and item.get("code")
    ]
    reason_messages = [
        str(item.get("message"))
        for item in qa_items
        if isinstance(item, dict) and item.get("message")
    ]
    status = str(enriched.get("status") or result.get("status") or "unknown")
    fallback_stage = {
        "stale_candidate_skipped": "freshness_preflight",
        "work_id_unresolved_skipped": "work_matching",
        "out_of_scope_skipped": "work_scope_check",
        "original_source_unresolved": "source_resolution",
        "content_type_mismatch_skipped": "type_classification",
        "insufficient_discussion_evidence": "candidate_evidence_preflight",
        "duplicate_skipped": "duplicate_check",
        "candidate_pipeline_error": "pipeline_error",
        "ready_for_feed": "feed_queue",
    }.get(status, "generation_or_review")
    terminal_stage = trace[-1] if isinstance(trace, list) and trace else fallback_stage
    enriched.update(
        {
            "status": status,
            "trace": trace,
            "terminal_stage": terminal_stage,
            "failure_stage": None if status == "ready_for_feed" else terminal_stage,
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "reason_messages": list(dict.fromkeys(reason_messages))[:8],
            "stage_timings_seconds": result.get("stage_timings_seconds") or {},
        }
    )
    for key in (
        "work_match_audit",
        "candidate_title",
        "captured_title",
        "source_url",
        "reason",
        "structured_work_references",
        "outbound_links",
    ):
        if key in result:
            enriched[key] = result[key]
    package = result.get("package") or {}
    if isinstance(package, dict):
        enriched["menu_work_ids"] = package.get("menu_work_ids") or []
        enriched["fact_count"] = len(package.get("facts") or [])
        enriched["image_count"] = len(package.get("images") or [])
        enriched["source_count"] = len(package.get("sources") or [])
    return enriched


def _candidate_menu_work_ids(
    candidate: dict, source, content_type: str | None = None
) -> list[int]:
    """Resolve candidate title and any explicitly linked work title.

    Discovery records such as character birthdays retain the publisher's direct
    character-to-anime link as ``linked_work_title``. That field is structured
    identity evidence, unlike free-form summaries, and should not be discarded.
    """
    identity_titles = _candidate_identity_titles(candidate)
    if not identity_titles:
        return []
    headline = identity_titles[0]
    evidence = SourceEvidence(
        id=source.id,
        title=headline,
        url=str(candidate.get("url") or source.url),
        publisher=source.name,
        is_official=source.role == "primary",
        role=source.role,
        region=source.region,
    )
    package = ContentPackage(
        request_id="discovery-work-preflight",
        topic=headline,
        content_kind=ContentKind.INFORMATION,
        content_type=content_type,
        sources=[evidence],
    )
    matched = resolve_work_matches(
        titles=identity_titles,
        source_ids=[source.id],
        content_type=content_type,
        source_url=str(candidate.get("url") or source.url),
    )
    if matched:
        return list(dict.fromkeys(item.menu_work_id for item in matched))
    return resolve_menu_work_ids(package)


def _candidate_identity_titles(candidate: dict) -> list[str]:
    """Return titles explicitly identifying the candidate's work.

    Keep the headline first. Only include ``linked_work_title`` when a discovery
    adapter supplied it as a dedicated field; never infer identity from summary
    prose, which may contain incidental titles.
    """
    return list(
        dict.fromkeys(
            value
            for value in (
                str(candidate.get("title") or "").strip(),
                str(candidate.get("linked_work_title") or "").strip(),
            )
            if value
        )
    )


def _rotating_catalog_work_titles(limit: int = 3) -> list[str]:
    """Select different popular catalog works across daily guide/character passes."""
    works = [
        row
        for row in load_work_catalog()
        if str(
            row.get("title_ko") or row.get("title_romaji") or row.get("title_english") or ""
        ).strip()
    ]
    works.sort(key=lambda row: (-int(row.get("popularity") or 0), int(row["menu_work_id"])))
    if not works:
        return []
    offset = datetime.now(UTC).date().toordinal() % len(works)
    rotated = works[offset:] + works[:offset]
    selected = []
    for row in rotated:
        title = str(
            row.get("title_ko") or row.get("title_romaji") or row.get("title_english") or ""
        ).strip()
        if title and title not in selected:
            selected.append(title)
        if len(selected) >= limit:
            break
    return selected


def _latest_first_candidate_items(items: list, limit: int) -> list:
    """Process the newest dated leads first, keeping undated leads after them."""
    return rank_candidates(items)[:limit]


def _candidate_host_matches_source(candidate_url: str, source_url: str) -> bool:
    candidate_host = (urlparse(candidate_url).hostname or "").lower().rstrip(".")
    registered_host = (urlparse(source_url).hostname or "").lower().rstrip(".")
    return bool(
        candidate_host
        and registered_host
        and (candidate_host == registered_host or candidate_host.endswith(f".{registered_host}"))
    )


def _registered_original_source_link(links, sources, content_type: str):
    """Resolve an article's clearly-labelled outbound link to an active primary source.

    Discovery feeds are never treated as fact evidence. Ambiguous link lists fail closed.
    """
    official_terms = re.compile(
        r"official|press release|announcement|公式|公式サイト|公式発表|発表元|原文|"
        r"보도자료|공식|원문|발표",
        re.IGNORECASE,
    )
    matches = []
    for link in links:
        link_url = str(link.url)
        parsed = urlparse(link_url)
        if parsed.scheme != "https" or not parsed.hostname:
            continue
        host = parsed.hostname.lower().rstrip(".")
        label = f"{link.text} {parsed.path}"
        score = 1 if official_terms.search(label) else 0
        for source in sources:
            registered_host = (urlparse(str(source.url)).hostname or "").lower().rstrip(".")
            if (
                source.status != "active"
                or source.role != "primary"
                or source.collection != "html"
                or content_type not in {item.value for item in source.content_types}
                or not registered_host
                or not (host == registered_host or host.endswith(f".{registered_host}"))
            ):
                continue
            matches.append((score, source, link_url, link.text))

    if not matches:
        return None
    best_score = max(item[0] for item in matches)
    best = [item[1:] for item in matches if item[0] == best_score]
    if len({(source.id, url) for source, url, _ in best}) != 1:
        return None
    source, link_url, label = best[0]
    return source, link_url, label


def _add_same_work_catalog_images(
    package: ContentPackage,
    sources,
    max_images: int,
) -> int:
    """Add exact-work catalog art for supporting cards when article art is sparse."""
    package.menu_work_ids = resolve_menu_work_ids(package)
    if not package.menu_work_ids or len(package.images) >= max_images:
        return 0
    if package.content_type == "curation" and len(set(package.menu_work_ids)) > 1:
        # A package-level union of work IDs is not enough to safely choose an
        # image for each curated item. Keep source-page art; don't mix catalog
        # images from different works until card-level image associations exist.
        return 0
    kitsu_source = next(
        (
            item
            for item in sources
            if item.id == "kitsu_anime_catalog"
            and item.status == "active"
            and item.role == "reference"
        ),
        None,
    )
    if kitsu_source is None or len(package.images) >= max_images:
        return 0
    preferred_character_names = []
    if package.content_type == "character":
        preferred_character_names = list(
            dict.fromkeys(
                [
                    match.name
                    for match in find_named_works(package.topic)
                    if match.kind == "character"
                    and match.menu_work_id in package.menu_work_ids
                ]
                + [
                    name
                    for term in package.term_glossary
                    if term.category == "character"
                    for name in (term.korean, term.original)
                    if name and name.strip()
                ]
            )
        )
    catalog_images = collect_kitsu_work_images(
        package.menu_work_ids,
        kitsu_source,
        max_images=min(max_images - len(package.images), max(8, 3 * len(package.menu_work_ids))),
        preferred_character_names=preferred_character_names,
    )
    package.images.extend(catalog_images)
    return len(catalog_images)


def _add_usage_summary(result: dict) -> dict:
    records = result.get("usage_records", [])
    result["usage_summary"] = {
        "llm_calls": len(records),
        "input_tokens": sum(int(item.get("input_tokens", 0)) for item in records),
        "cached_input_tokens": sum(int(item.get("cached_input_tokens", 0)) for item in records),
        "cache_write_tokens": sum(int(item.get("cache_write_tokens", 0)) for item in records),
        "output_tokens": sum(int(item.get("output_tokens", 0)) for item in records),
        "total_tokens": sum(int(item.get("total_tokens", 0)) for item in records),
        "estimated_cost_usd": round(
            sum(float(item.get("estimated_cost_usd", 0)) for item in records), 8
        ),
        "per_content_budget_usd": POLICY.max_content_llm_cost_usd,
        "budget_exceeded": sum(
            float(item.get("estimated_cost_usd", 0)) for item in records
        ) > POLICY.max_content_llm_cost_usd,
    }
    return result


def _print_run_metrics(result: dict) -> None:
    for stage, seconds in result.get("stage_timings_seconds", {}).items():
        console.print(f"시간 · {stage}: {seconds:.2f}초")
    summary = result.get("usage_summary", {})
    console.print(
        "LLM 합계 · "
        f"{summary.get('llm_calls', 0)}회 · "
        f"입력 {summary.get('input_tokens', 0):,} / "
        f"출력 {summary.get('output_tokens', 0):,} tokens · "
        f"추정 ${summary.get('estimated_cost_usd', 0):.6f}"
    )
    for record in result.get("usage_records", []):
        console.print(
            f"  {record.get('stage', 'llm')} · {record.get('elapsed_seconds', 0):.2f}초 · "
            f"${record.get('estimated_cost_usd', 0):.6f}"
        )
    if result.get("render_elapsed_seconds") is not None:
        rendered_count = result.get("rendered_card_count")
        if rendered_count is None:
            rendered_count = len(result.get("package", {}).get("cards", []))
        console.print(
            f"카드 PNG 렌더링 · {rendered_count}장 · {result['render_elapsed_seconds']:.2f}초"
        )


def _export_rendered_cards(package_data: dict, output_dir: Path) -> list[Path]:
    package = ContentPackage.model_validate(package_data)
    if not package.cards or any(not card.rendered_image_key for card in package.cards):
        return []
    repository = AgentRepository()
    output_dir.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []
    for card in package.cards:
        stored = repository.get_image_bytes(card.rendered_image_key or "")
        if stored is None or stored[1] != "image/png":
            raise RuntimeError(f"{card.page}장 렌더 PNG를 DB에서 찾을 수 없습니다.")
        path = output_dir / f"card_{card.page:02d}.png"
        path.write_bytes(stored[0])
        exported.append(path)
    return exported


def _run_generation_from_db(
    request_id: str,
    kind: ContentKind,
    fixture: Path | None = None,
    requested_content_type: str | None = None,
    initial_usage_records: list[dict] | None = None,
    repository: AgentRepository | None = None,
) -> tuple[ContentPackage, dict]:
    """Load crawler records from PostgreSQL and run the existing generation graph."""
    repository = repository or AgentRepository()
    package = repository.load_content_package(
        request_id, kind.value, content_type=requested_content_type
    )
    if requested_content_type:
        package.content_type = requested_content_type
    curation_match_audit: dict = {}
    work_match_usage_records = list(initial_usage_records or [])
    if package.content_type == "curation" and not package.menu_work_ids:
        source_titles = [source.title for source in package.sources if source.title]
        source_quotes = [
            str(fact.details.get("source_quote") or "").strip()
            for fact in package.facts
            if str(fact.details.get("source_quote") or "").strip()
        ]
        evidence = resolve_unmatched_work_with_llm(
            titles=source_titles or [package.topic],
            source_url=str(package.sources[0].url) if package.sources else None,
            body_excerpt="\n".join(source_quotes)[:2_000],
            content_type="curation",
            usage_records=work_match_usage_records,
            audit=curation_match_audit,
        )
        package.work_match_evidence = evidence
        package.menu_work_ids = list(dict.fromkeys(item.menu_work_id for item in evidence))
        if not package.menu_work_ids:
            curation_match_audit.setdefault(
                "status", "curation_work_list_unresolved"
            )
    if any(
        image.url and image.download_status in {"pending", "blocked_rights", "failed"}
        for image in package.images
    ):
        curated = image_curator(
            {
                "package": package.model_dump(mode="json"),
                "trace": [],
                "download_images": True,
                "repository": repository,
            }
        )
        package = ContentPackage.model_validate(curated["package"])
    offline_cards: list[dict] = []
    if fixture is not None:
        fixture_payload = json.loads(fixture.read_text(encoding="utf-8"))
        offline_cards = [
            card.model_dump(mode="json") for card in CopyDeck.model_validate(fixture_payload).cards
        ]
    started = perf_counter()
    result = build_graph().invoke(
        {
            "request_id": package.request_id,
            "topic": package.topic,
            "content_kind": package.content_kind.value,
            "requested_content_type": requested_content_type,
            "package": package.model_dump(mode="json"),
            "usage_records": work_match_usage_records,
            "trace": [],
            "revision_count": 0,
            "max_revision": get_settings().max_revision,
            "allow_llm": fixture is None,
            "offline_cards": offline_cards,
            "download_images": False,
            "render_required": fixture is None,
        }
    )
    result["stage_timings_seconds"] = {"generation_graph": round(perf_counter() - started, 3)}
    if curation_match_audit:
        result["work_match_audit"] = curation_match_audit
    _add_usage_summary(result)
    return package, result


@app.command()
def doctor() -> None:
    """패키지와 환경변수 준비 상태를 확인합니다."""
    settings = get_settings()
    table = Table(title="ani_me;nu 환경 점검")
    table.add_column("항목")
    table.add_column("상태")
    for package in ("langgraph", "langchain-openai", "pydantic", "httpx", "trafilatura"):
        table.add_row(package, importlib.metadata.version(package))
    table.add_row("실행 환경", settings.environment)
    table.add_row("LLM 모델", settings.model_name)
    if settings.database_backend == "sqlite":
        sqlite_path = settings.database_path or "data/ani_menu_local.sqlite3"
        table.add_row("저장소", f"SQLite · {sqlite_path}")
    elif settings.database_backend == "postgresql":
        table.add_row(
            "저장소",
            "PostgreSQL · 연결 주소 설정됨" if settings.database_url else "PostgreSQL · URL 미설정",
        )
    elif settings.database_url:
        table.add_row("저장소", "자동 선택 · PostgreSQL URL 설정됨")
    else:
        sqlite_path = settings.database_path or "data/ani_menu_local.sqlite3"
        table.add_row("저장소", f"자동 선택 · SQLite {sqlite_path}")
    table.add_row(
        "API 키", "설정됨" if settings.openai_api_key else "미설정(코드 검수는 실행 가능)"
    )
    table.add_row("게시 판정", "자동 검수 · 권리 미확인 이미지는 차단")
    console.print(table)


@app.command("work-coverage")
def work_coverage(
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 30,
    include_covered: Annotated[
        bool, typer.Option("--include-covered/--gaps-only", help="출처가 이미 등록된 작품도 표시")
    ] = False,
) -> None:
    """작품별 출처 연결과 생성 현황을 점검해 출처 보강 대상을 찾습니다."""
    catalog = load_work_catalog()
    if not catalog:
        raise typer.BadParameter("catalog/menu_works.csv를 찾을 수 없습니다.")
    sources = load_source_registry().sources
    counts = AgentRepository().get_work_content_counts()

    rows = []
    for work in catalog:
        title_ko = (work.get("title_ko") or "").strip()
        title_romaji = (work.get("title_romaji") or "").strip()
        title_english = (work.get("title_english") or "").strip()
        aliases = {value.casefold() for value in (title_ko, title_romaji, title_english) if value}
        related = [
            source
            for source in sources
            if any(
                value and value.casefold() in aliases
                for value in (source.work_title_ko, source.work_title_original)
            )
        ]
        linked = counts.get(work["menu_work_id"], {"draft_count": 0, "ready_count": 0})
        active_count = sum(source.status == "active" for source in related)
        row = {
            **work,
            **linked,
            "registered_sources": len(related),
            "active_sources": active_count,
            "pending_sources": len(related) - active_count,
        }
        if include_covered or not related or not active_count:
            rows.append(row)

    # This is a diagnostic, not a balancing policy: high-information works remain
    # fully eligible for generation and are never capped or down-ranked here.
    rows.sort(
        key=lambda row: (
            row["active_sources"] > 0,
            row["draft_count"] > 0,
            row["draft_count"],
            -row.get("popularity", 0),
            row["menu_work_id"],
        )
    )
    table = Table(title="작품별 출처·콘텐츠 커버리지 (생성량 제한 없음)")
    table.add_column("menu_work_id", justify="right")
    table.add_column("작품")
    table.add_column("등록 출처", justify="right")
    table.add_column("활성 출처", justify="right")
    table.add_column("대기 출처", justify="right")
    table.add_column("생성 콘텐츠", justify="right")
    table.add_column("ready", justify="right")
    for row in rows[:limit]:
        table.add_row(
            str(row["menu_work_id"]),
            row["title_ko"] or row["title_romaji"] or row["title_english"],
            str(row["registered_sources"]),
            str(row["active_sources"]),
            str(row["pending_sources"]),
            str(row["draft_count"]),
            str(row["ready_count"]),
        )
    console.print(table)


@app.command("work-type-coverage")
def work_type_coverage(
    work_title: Annotated[
        str | None,
        typer.Option("--work-title", help="작품의 menu_work_id 또는 CSV에 등록된 정확한 작품명"),
    ] = None,
) -> None:
    """작품별 10개 유형의 출처·후보·검증 사실·ready 커버리지를 확인합니다."""
    catalog = load_work_catalog()
    if not catalog:
        raise typer.BadParameter("catalog/menu_works.csv를 찾을 수 없습니다.")
    if work_title:
        selected_ids = set(find_work_ids(catalog, work_title))
        if not selected_ids:
            raise typer.BadParameter(f"작품을 찾을 수 없습니다: {work_title}")
    else:
        selected_ids = {int(row["menu_work_id"]) for row in catalog}

    repository = AgentRepository()
    with repository.connect() as connection:
        records = [
            dict(row)
            for row in connection.execute(
                "SELECT request_id, record_kind, status, expires_at, payload_json "
                "FROM agent_records WHERE record_kind IN "
                "('source', 'trend_source', 'discovery_candidate', 'fact', 'humor_idea')"
            ).fetchall()
        ]
        ready_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT links.menu_work_id, queue.content_type "
                "FROM feed_queue AS queue JOIN content_work_links AS links "
                "ON links.request_id=queue.request_id WHERE queue.status='ready'"
            ).fetchall()
        ]

    coverage = build_work_type_coverage(
        catalog,
        load_source_registry().sources,
        records,
        ready_rows,
    )
    table = Table(
        title=(
            f"{work_title} · 유형별 수집 커버리지"
            if work_title
            else "작품 전체 · 유형별 커버리지 합계 (작품 연결 출처 기준)"
        )
    )
    table.add_column("유형")
    table.add_column("활성 출처", justify="right")
    table.add_column("후보탐색", justify="right")
    table.add_column("검토대기", justify="right")
    table.add_column("발견 후보", justify="right")
    table.add_column("검증 사실", justify="right")
    table.add_column("커뮤니티 게시글", justify="right")
    table.add_column("트렌드 집계", justify="right")
    table.add_column("ready", justify="right")

    if work_title:
        for content_code, label in CONTENT_TYPE_LABELS.items():
            values = [coverage[work_id][content_code.value] for work_id in selected_ids]
            totals = {
                key: sum(item[key] for item in values)
                for key in (
                    "active_sources",
                    "discovery_sources",
                    "pending_sources",
                    "candidates",
                    "verified_facts",
                    "humor_posts",
                    "trend_signals",
                    "ready",
                )
            }
            table.add_row(
                label,
                str(totals["active_sources"]),
                str(totals["discovery_sources"]),
                str(totals["pending_sources"]),
                str(totals["candidates"]),
                str(totals["verified_facts"]),
                str(totals["humor_posts"]),
                str(totals["trend_signals"]),
                str(totals["ready"]),
            )
    else:
        for content_code, label in CONTENT_TYPE_LABELS.items():
            totals = {
                key: sum(coverage[work_id][content_code.value][key] for work_id in selected_ids)
                for key in (
                    "active_sources",
                    "discovery_sources",
                    "pending_sources",
                    "candidates",
                    "verified_facts",
                    "humor_posts",
                    "trend_signals",
                    "ready",
                )
            }
            table.add_row(
                label,
                str(totals["active_sources"]),
                str(totals["discovery_sources"]),
                str(totals["pending_sources"]),
                str(totals["candidates"]),
                str(totals["verified_facts"]),
                str(totals["humor_posts"]),
                str(totals["trend_signals"]),
                str(totals["ready"]),
            )
    console.print(table)
    console.print(
        "후보는 수집·검증 사실이 아니며, fact_type이 분류되지 않은 검증 사실은 "
        "유형별 사실 수에서 제외됩니다. 활성 출처 수 역시 실시간 수집 성공을 보장하지 않습니다."
    )


@app.command("generation-readiness")
def generation_readiness() -> None:
    """Show which content types have approved automatic sources and stored evidence."""
    registry = load_source_registry()
    type_map = {
        source.id: [item.value for item in source.content_types] for source in registry.sources
    }
    information_sources = {
        source.id
        for source in registry.sources
        if source.status == "active"
        and source.role in {"primary", "editorial", "reference"}
        and source.collection != "manual"
        and supports_discovery_adapter(source, work_title="adapter-check")
    }
    humor_sources = {
        source.id
        for source in registry.sources
        if source.status == "trend_only"
        and source.role == "trend"
        and source.collection != "manual"
        and supports_discovery_adapter(source)
    }
    repository = AgentRepository()
    with repository.connect() as connection:
        records = connection.execute(
            "SELECT agent_type, record_kind, status, expires_at, payload_json FROM agent_records"
        ).fetchall()

    candidates: dict[str, int] = {}
    verified_facts: dict[str, int] = {}
    humor_signals: dict[str, int] = {}
    unclassified_verified_facts = 0
    humor_trend_snapshots = 0
    ineligible_humor_snapshots = 0
    for row in records:
        expires_at = row["expires_at"]
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= datetime.now(UTC):
                continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if row["record_kind"] == "discovery_candidate":
            for code in payload.get("content_types", []):
                candidates[code] = candidates.get(code, 0) + 1
        elif (
            row["record_kind"] == "trend_source"
            and row["status"].startswith("trend_only")
            and payload.get("status") == "trend_signal"
        ):
            if payload.get("source_id") in humor_sources:
                humor_trend_snapshots += 1
            else:
                ineligible_humor_snapshots += 1
        elif row["record_kind"] == "fact" and row["status"] == "collected":
            source_ids = set(payload.get("source_ids", []))
            if payload.get("verified") is True and source_ids & information_sources:
                code = supported_verified_fact_type(
                    str(payload.get("fact_type") or ""),
                    source_ids,
                    information_sources,
                    type_map,
                )
                if code:
                    verified_facts[code] = verified_facts.get(code, 0) + 1
                else:
                    unclassified_verified_facts += 1
        elif is_automated_humor_post(row["record_kind"], row["status"], payload, humor_sources):
            source_ids = set(payload.get("source_ids", []))
            for source_id in source_ids:
                for code in type_map.get(source_id, []):
                    humor_signals[code] = humor_signals.get(code, 0) + 1

    table = Table(title="콘텐츠 유형별 자동 생성 근거 현황")
    table.add_column("유형")
    table.add_column("승인된 자동 출처", justify="right")
    table.add_column("생성에 쓸 자료", justify="right")
    table.add_column("단순 트렌드 집계", justify="right")
    table.add_column("발견 후보", justify="right")
    table.add_column("상태")
    for code, label in CONTENT_TYPE_LABELS.items():
        type_code = code.value
        allowed_sources = humor_sources if type_code == "humor" else information_sources
        source_count = sum(
            type_code in type_map.get(source_id, []) for source_id in allowed_sources
        )
        evidence_count = (
            humor_signals.get(type_code, 0)
            if type_code == "humor"
            else verified_facts.get(type_code, 0)
        )
        if source_count == 0:
            status = "출처 보강 필요"
        elif evidence_count < 1 or (type_code == "humor" and evidence_count < 3):
            status = (
                (
                    "실제 토론글 수집 필요 "
                    f"(태그 집계 {humor_trend_snapshots}, 제외 자료 {ineligible_humor_snapshots})"
                )
                if type_code == "humor" and (humor_trend_snapshots or ineligible_humor_snapshots)
                else "실제 토론글 수집 필요"
                if type_code == "humor"
                else "검증 자료 수집 필요"
            )
        else:
            status = "패키지별 이미지·작품ID 검사 필요"
        table.add_row(
            label,
            str(source_count),
            str(evidence_count),
            str(humor_trend_snapshots) if type_code == "humor" else "—",
            str(candidates.get(type_code, 0)),
            status,
        )
    console.print(table)
    console.print(
        "발견 후보는 미검증 자료라 생성 근거 수에 포함하지 않습니다. "
        "최종 생성에는 요청별 팩트·menu_work_id·관련 이미지·기간 검증도 필요합니다."
    )
    console.print(
        "출처가 없거나 활성 출처가 없는 작품은 공식 도메인/유형별 출처 후보를 "
        "추가 검증할 대상입니다. 이 표는 작품별 생성 할당량이나 상한을 만들지 않습니다."
    )

    console.print(
        "유머는 실제 게시글 기반 아이디어만 생성 자료로 셉니다. 단순 태그 빈도와 "
        "현재 자동수집 승인되지 않은 출처 자료는 생성 근거로 사용하지 않습니다."
    )
    if unclassified_verified_facts:
        console.print(
            f"유형별 집계에서 제외한 미분류/출처 불일치 검증 팩트: "
            f"{unclassified_verified_facts}건. 해당 자료는 임의로 모든 유형에 "
            "중복 집계하지 않습니다."
        )


@app.command("source-routing")
def source_routing() -> None:
    """10개 유형별 출처 매핑, 요청 승인, 생성 근거 승인 상태를 보여줍니다."""
    registry = load_source_registry()
    table = Table(title="콘텐츠 유형별 출처 라우팅")
    table.add_column("유형")
    table.add_column("연결 출처", justify="right")
    table.add_column("자동 어댑터 연결", justify="right")
    table.add_column("생성용 출처 게이트", justify="right")
    table.add_column("연결된 출처 / 상태")
    for code, label in CONTENT_TYPE_LABELS.items():
        connected = [source for source in registry.sources if code in source.content_types]
        collectable = [
            source
            for source in connected
            if supports_discovery_adapter(source, work_title="adapter-check")
        ]
        generation_eligible = (
            [
                source
                for source in connected
                if (
                    source.role == "trend"
                    and source.status == "trend_only"
                    and source.collection != "manual"
                )
                and supports_discovery_adapter(source)
                if code.value == "humor"
            ]
            if code.value == "humor"
            else [
                source
                for source in connected
                if source.role in {"primary", "editorial", "reference"}
                and source.status == "active"
                and source.collection != "manual"
                and (
                    source.role == "primary"
                    or (
                        source.role == "editorial"
                        and code.value in {"review", "curation", "humor"}
                    )
                    or (
                        source.role == "reference"
                        and code.value in {"guide", "people", "character", "curation"}
                    )
                )
                and supports_discovery_adapter(source, work_title="adapter-check")
            ]
        )
        details = (
            ", ".join(
                f"{source.id} ({source.status}"
                f"{', discovery off' if not source.discovery_enabled else ''})"
                for source in connected
            )
            or "아직 연결된 출처 없음"
        )
        table.add_row(
            label,
            str(len(connected)),
            str(len(collectable)),
            str(len(generation_eligible)),
            details,
        )
    console.print(table)
    console.print(
        "같은 출처의 여러 유형 연결은 허용됩니다. 유형 연결은 라우팅 범위이고, "
        "자동 어댑터 수는 코드상 수집 경로가 있다는 뜻이지, "
        "실시간 사이트 접근·본문 품질·이미지 사용 가능성을 검증했다는 뜻은 아닙니다. "
        "반복 후보 수집은 승인 상태(active/trend_only)이면서 "
        "discovery_enabled인 출처만 수행합니다. "
        "수집 가능한 discovery 출처는 후보 발견용이며 생성 근거 승인 수에 포함하지 않습니다."
    )


@app.command("graph-smoke")
def graph_smoke() -> None:
    """외부 API 호출 없이 생성·독립 검수 분리 구조를 시험합니다."""
    package = _load_package(SAMPLE_PATH)
    review_fixture = {
        "summary": "구조 검증 전용 fixture 평가. 실제 게시 승인으로 사용되지 않습니다.",
        "content_type_fit": 5,
        "cards": [
            {
                "page": card.page,
                "korean_naturalness": 5,
                "translation_naturalness": 5,
                "reader_clarity": 5,
                "fan_engagement": 5,
                "card_flow": 5,
                "safety_respect": 5,
                "safety_blocker": False,
                "safety_category": None,
                "feedback": "구조 시험용 높은 점수 fixture입니다.",
            }
            for card in package.cards
        ],
    }
    result = build_graph().invoke(
        {
            "request_id": package.request_id,
            "topic": package.topic,
            "content_kind": package.content_kind.value,
            "package": package.model_dump(mode="json"),
            "trace": [],
            "revision_count": 0,
            "max_revision": 3,
            "download_images": False,
            "review_fixture": review_fixture,
        }
    )
    console.print("[bold green]분리된 생성·검수 LangGraph 구조 시험 완료[/bold green]")
    console.print(" -> ".join(result["trace"]))
    console.print(f"최종 상태: {result['status']}")
    console.print(f"품질 문제: {len(result.get('qa_report', []))}건")
    console.print("안내: 오프라인 fixture 결과는 실제 게시 대기열에 승인되지 않습니다.")


@app.command("crawl-ingest")
def crawl_ingest(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    download_images: Annotated[bool, typer.Option("--download-images/--no-download-images")] = True,
) -> None:
    """크롤러가 정규화한 자료를 DB에만 적재합니다. 콘텐츠 카피는 만들지 않습니다."""
    package = _load_package(path)
    if package.cards:
        raise typer.BadParameter(
            "크롤러 입력에는 cards를 넣을 수 없습니다. 생성 단계에서 별도로 만듭니다."
        )
    if not package.sources or not package.facts:
        raise typer.BadParameter("출처와 사실/아이디어가 모두 있어야 DB에 적재할 수 있습니다.")
    source_ids = {source.id for source in package.sources}
    for fact in package.facts:
        missing = set(fact.source_ids) - source_ids
        if missing:
            raise typer.BadParameter(
                f"{fact.id}의 출처 ID가 누락됐습니다: {', '.join(sorted(missing))}"
            )
        if package.content_kind is ContentKind.INFORMATION and not fact.verified:
            raise typer.BadParameter(f"정보성 팩트 {fact.id}는 verified=true가 필요합니다.")
    if package.content_kind is ContentKind.INFORMATION:
        has_official_primary = any(
            source.is_official and source.role == "primary" for source in package.sources
        )
        has_editorial_primary = package.content_type in {"review", "curation", "humor"} and any(
            source.role == "editorial" for source in package.sources
        )
        if not (has_official_primary or has_editorial_primary):
            raise typer.BadParameter(
                "정보 자료는 공식 1차 출처가, 리뷰·큐레이션 자료는 편집 출처가 필요합니다."
            )

    repository = AgentRepository()
    repository.prepare_lifecycle(package)
    if package.content_kind is ContentKind.HUMOR:
        research_humor({"package": package.model_dump(mode="json"), "trace": []})
    else:
        research_information({"package": package.model_dump(mode="json"), "trace": []})
    result = image_curator(
        {
            "package": package.model_dump(mode="json"),
            "trace": [],
            "download_images": download_images,
        }
    )
    counts = repository.counts()
    console.print(f"크롤러 적재 완료: request_id={package.request_id}")
    console.print(f"DB 전체 정보/유머 레코드: {counts.get(package.content_kind.value, 0)}")
    console.print(f"이번 적재 이미지 수: {result.get('persisted_image_count', 0)}")
    console.print("콘텐츠 생성은 별도 실행: me-nu generate-db " + package.request_id)


@app.command("generate-db")
def generate_db(
    request_id: Annotated[str, typer.Argument()],
    kind: Annotated[ContentKind, typer.Option("--kind", help="information 또는 humor")],
    content_type: Annotated[
        str | None,
        typer.Option("--content-type", help="Optional required carousel type code"),
    ] = None,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        "output/drafts/generated_db.json"
    ),
    fixture: Annotated[
        Path | None,
        typer.Option("--fixture", help="비용 없이 생성 루프를 확인할 CopyDeck JSON"),
    ] = None,
) -> None:
    """DB에 적재된 크롤러 자료만 읽어 콘텐츠를 생성합니다."""
    try:
        _package, result = _run_generation_from_db(
            request_id, kind, fixture, requested_content_type=content_type
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    exported = _export_rendered_cards(
        result.get("package", {}), output.parent / "cards" / request_id
    )
    console.print(f"DB 자료 기반 생성 결과: {output.resolve()}")
    if exported:
        console.print(f"카드 PNG 파일: {exported[0].parent.resolve()} ({len(exported)}장)")
    console.print(f"최종 상태: {result['status']}")
    _print_run_metrics(result)
    report = result.get("quality_report", {})
    if report:
        console.print(f"품질 점수: {report.get('total')}/100 · 판정: {report.get('decision')}")


@app.command("crawl-generate")
def crawl_generate(
    source_id: Annotated[str | None, typer.Option("--source", help="활성 근거 출처 ID")] = None,
    url: Annotated[str | None, typer.Option("--url", help="출처 상세 페이지 URL")] = None,
    candidate_id: Annotated[
        str | None,
            typer.Option(
            "--candidate-id", help="DB 후보 ID로 발견 출처와 등록된 원문을 연결해 처리"
        ),
    ] = None,
    content_type: Annotated[
        str | None,
        typer.Option("--content-type", help="Optional required carousel type code"),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o"),
    ] = Path("output/drafts/crawl_generated.json"),
    max_images: Annotated[int, typer.Option("--max-images", min=1, max=20)] = 20,
    retry_failed: Annotated[
        bool,
        typer.Option(
            "--retry-failed",
            help="같은 request_id가 미통과 상태일 때만 기존 실패 건을 다시 생성합니다.",
        ),
    ] = False,
    prepare_only: bool = False,
) -> None:
    """승인된 원문 수집부터 카드 생성 및 독립 검수까지 실행합니다."""
    registry = load_source_registry()
    run_started = perf_counter()
    stage_timings: dict[str, float] = {}
    requested_content_type = content_type
    identity_preflight_started = perf_counter()
    discovery_provenance = None
    candidate_work_ids: list[int] = []
    work_match_evidence = []
    work_match_usage_records: list[dict] = []
    work_match_audit: dict = {}
    identity_preflight_capture = None
    if candidate_id:
        if source_id or url:
            raise typer.BadParameter("--candidate-id는 --source/--url과 함께 쓸 수 없습니다.")
        try:
            candidate = AgentRepository().load_discovery_candidate(candidate_id)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        candidate_types = {
            str(value).strip() for value in candidate.get("content_types", []) if str(value).strip()
        }
        candidate_host = (
            (urlparse(str(candidate.get("url", ""))).hostname or "").lower().rstrip(".")
        )
        # Discovery can persist only the type used by its current routing pass.
        # Expand that hint from active registry entries on the exact host so a
        # registered multi-type source can be routed by the article's subject.
        candidate_types.update(
            content_type.value
            for item in registry.sources
            if item.status == "active"
            and item.role in {"primary", "editorial", "reference"}
            and item.collection in {"html", "api"}
            and (urlparse(str(item.url)).hostname or "").lower().rstrip(".") == candidate_host
            for content_type in item.content_types
        )
        if not candidate_types:
            raise typer.BadParameter(
                "후보에 콘텐츠 유형이 없어 실행할 수 없습니다. 유형을 분류한 뒤 다시 시도하세요."
            )
        if content_type and content_type not in candidate_types:
            raise typer.BadParameter(
                f"후보 유형({', '.join(sorted(candidate_types))})과 요청 유형({content_type})이 "
                "일치하지 않습니다."
            )
        if content_type is None:
            if len(candidate_types) == 1:
                content_type = next(iter(candidate_types))
            else:
                raise typer.BadParameter(
                    f"후보 유형이 여러 개입니다({', '.join(sorted(candidate_types))}). "
                    "--content-type으로 하나를 지정하세요."
                )
        candidate_url = str(candidate["url"])
        candidate_host = (urlparse(candidate_url).hostname or "").lower().rstrip(".")
        eligible_sources = [
            item
            for item in registry.sources
            if item.status == "active"
            and item.role in {"primary", "editorial", "reference"}
            and item.collection in {"html", "api"}
            and (urlparse(str(item.url)).hostname or "").lower().rstrip(".") == candidate_host
            and bool(candidate_types & {value.value for value in item.content_types})
            and (
                content_type is None
                or content_type in {value.value for value in item.content_types}
            )
        ]
        candidate_source_id = str(candidate.get("source_id") or "")
        matching_sources = [
            item for item in eligible_sources if item.id == candidate_source_id
        ] or eligible_sources
        if not matching_sources:
            discovery_source = next(
                (item for item in registry.sources if item.id == candidate_source_id), None
            )
            direct_interview_sources = [
                item
                for item in registry.sources
                if item.status == "active"
                and item.role == "editorial"
                and item.collection == "html"
                and content_type == "people"
                and "people" in {value.value for value in item.content_types}
                and _candidate_host_matches_source(candidate_url, str(item.url))
            ]
            candidate_title = str(candidate.get("title") or "")
            is_direct_interview = bool(
                discovery_source
                and re.search(
                    r"\binterview\b|인터뷰|대담|インタビュー|対談|取材",
                    candidate_title,
                    re.IGNORECASE,
                )
            )
            if is_direct_interview and len(direct_interview_sources) == 1:
                # Directly conducted interviews are the original reporting for
                # the interview itself. Keep discovery provenance, but do not
                # require a second official publisher link before using it.
                matching_sources = direct_interview_sources
        if not matching_sources:
            discovery_source = next(
                (item for item in registry.sources if item.id == candidate_source_id), None
            )
            discovery_host = (
                (urlparse(str(discovery_source.url)).hostname or "").lower().rstrip(".")
                if discovery_source
                else ""
            )
            if not (
                discovery_source
                and discovery_source.status == "active"
                and discovery_source.discovery_enabled
                and discovery_source.role == "discovery"
                and discovery_source.collection in {"html", "rss"}
                and discovery_host
                and (
                    candidate_host == discovery_host
                    or candidate_host.endswith(f".{discovery_host}")
                )
            ):
                raise typer.BadParameter(
                    "후보 URL에 대응하는 활성 원문 출처가 없고, 등록된 HTML 발견 출처의 "
                    "기사 페이지도 아니어서 공식 원문을 안전하게 찾을 수 없습니다."
                )
            capture_source = discovery_source
            if discovery_source.collection != "html":
                capture_source = discovery_source.model_copy(
                    update={"collection": "html", "role": "reference"}
                )
            try:
                discovery_capture = collect_official_page(
                    capture_source,
                    candidate_url,
                    expand_related_pages=False,
                    allow_discovery_page=capture_source.role == "discovery",
                )
            except (ValueError, httpx.HTTPError) as exc:
                raise typer.BadParameter(f"발견 매체 기사 수집 실패: {exc}") from exc

            original = _registered_original_source_link(
                discovery_capture.outbound_links,
                registry.sources,
                content_type or "",
            )
            discovery_record = discovery_capture.model_dump(mode="json")
            discovery_record.update(
                {
                    "content_type": content_type,
                    "capture_role": "discovery_context_only",
                    "original_source_url": original[1] if original else None,
                    "original_source_id": original[0].id if original else None,
                }
            )
            AgentRepository().persist_page_capture(discovery_record, discovery_source.region)
            if original is None:
                # Keep the captured discovery page for investigation, but do not
                # feed discovery-only material into content generation as evidence.
                result = {
                    "status": "original_source_unresolved",
                    "candidate_id": candidate_id,
                    "content_type": content_type,
                    "title": discovery_capture.title or str(candidate.get("title") or ""),
                    "source_url": candidate_url,
                    "discovered_via_source_id": discovery_source.id,
                    "original_source_url": None,
                    "reason": "Discovery page was captured, but it has no registered primary/editorial source link.",
                    "outbound_links": [
                        item.model_dump(mode="json")
                        for item in discovery_capture.outbound_links[:20]
                    ],
                }
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                console.print(
                    "발견 페이지는 보존했지만 등록된 원문 출처 링크가 없어 생성 대기 상태로 분류했습니다."
                )
                return
            else:
                source, url, original_link_label = original
                discovery_provenance = {
                    "discovered_via_source_id": discovery_source.id,
                    "discovery_url": candidate_url,
                    "discovery_title": discovery_capture.title,
                    "original_source_url": url,
                    "original_link_label": original_link_label,
                }
        else:
            source = matching_sources[0]
            url = candidate_url
            discovery_source = next(
                (item for item in registry.sources if item.id == candidate_source_id), None
            )
            if (
                discovery_source
                and discovery_source.id != source.id
                and discovery_source.role == "discovery"
            ):
                discovery_provenance = {
                    "discovered_via_source_id": discovery_source.id,
                    "discovery_url": candidate_url,
                    "discovery_title": str(candidate.get("title") or ""),
                    "original_source_url": candidate_url,
                    "original_link_label": "RSS/discovery candidate URL",
                }
        console.print(
            f"DB 후보 연결: {candidate.get('title', candidate_id)} · "
            f"유형 힌트 {', '.join(candidate.get('content_types', [])) or '미분류'}"
        )
    else:
        if not source_id or not url:
            raise typer.BadParameter("--candidate-id 또는 --source와 --url을 지정하세요.")
        source = next((item for item in registry.sources if item.id == source_id), None)
        if source is None:
            raise typer.BadParameter(f"등록되지 않은 출처입니다: {source_id}")
    if (
        source.status != "active"
        or source.role not in {"primary", "editorial", "reference"}
        or source.collection not in {"html", "api"}
    ):
        raise typer.BadParameter("활성화된 1차/편집 HTML 출처만 이 실행 경로에 사용할 수 있습니다.")

    if content_type and content_type not in {item.value for item in source.content_types}:
        raise typer.BadParameter(
            f"Source {source.id} does not support requested type {content_type}."
        )

    if candidate_id:
        candidate_work_ids = _candidate_menu_work_ids(candidate, source, content_type)
        if not candidate_work_ids:
            # Do not reject an item solely because its headline omits the work.
            # Capture only this already-approved source page, then inspect its
            # structured about/mentions fields and registered official links.
            try:
                identity_preflight_capture = (
                    collect_reference_page(source, url)
                    if source.collection == "api"
                    else collect_official_page(
                        source,
                        url,
                        expand_related_pages=False,
                        max_cta_detail_pages=1,
                        review_ambiguous_links=not prepare_only,
                    )
                )
                work_match_usage_records.extend(
                    getattr(identity_preflight_capture, "link_review_usage", [])
                )
                identity_capture_payload = identity_preflight_capture.model_dump(mode="json")
                identity_capture_payload.update(
                    {
                        "candidate_id": candidate_id,
                        "content_type": content_type,
                        "capture_role": "candidate_identity_preflight",
                        "menu_work_ids": [],
                        "work_match_evidence": [],
                    }
                )
                identity_capture_payload["published_at"] = (
                    identity_capture_payload.get("published_at")
                    or candidate.get("published_at")
                )
                if discovery_provenance:
                    identity_capture_payload.update(discovery_provenance)
                if content_type in {"event", "goods"}:
                    preflight_temporal = expiry_evidence(
                        {
                            "title": identity_preflight_capture.title,
                            "text": identity_preflight_capture.text,
                            "published_at": (
                                identity_preflight_capture.published_at
                                or candidate.get("published_at")
                            ),
                            "event_start": identity_preflight_capture.event_start,
                            "event_end": identity_preflight_capture.event_end,
                            "availability_status": identity_preflight_capture.availability_status,
                            "availability_evidence": identity_preflight_capture.availability_evidence,
                        },
                        content_type,
                    )
                    if preflight_temporal["status"] != "active":
                        status = (
                            "stale_candidate_skipped"
                            if preflight_temporal["status"] == "expired"
                            else "expiry_unresolved_held"
                        )
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_text(
                            json.dumps(
                                {
                                    "status": status,
                                    "candidate_id": candidate_id,
                                    "content_type": content_type,
                                    "title": identity_preflight_capture.title,
                                    "source_url": str(identity_preflight_capture.url),
                                    "event_end": preflight_temporal.get("end_date")
                                    if content_type == "event" else None,
                                    "sale_end": preflight_temporal.get("end_date")
                                    if content_type == "goods" else None,
                                    "expiry_evidence": preflight_temporal.get("evidence"),
                                    "reason": "Period validity was expired or could not be established; no page data was stored.",
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                        return
                if content_type == "news":
                    preflight_news = immediate_news_action_expiry(candidate)
                    if preflight_news["status"] in {"expired", "unresolved"}:
                        status = (
                            "stale_candidate_skipped"
                            if preflight_news["status"] == "expired"
                            else "expiry_unresolved_held"
                        )
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_text(
                            json.dumps(
                                {
                                    "status": status,
                                    "candidate_id": candidate_id,
                                    "content_type": content_type,
                                    "title": candidate.get("title"),
                                    "source_url": str(candidate.get("url") or url),
                                    "expiry_evidence": preflight_news.get("evidence"),
                                    "reason": "Time-sensitive news is past its publication day or has no publication date; no page data was stored.",
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                        return
                AgentRepository().persist_page_capture(
                    identity_capture_payload, source.region
                )
            except (ValueError, httpx.HTTPError) as exc:
                raise typer.BadParameter(f"작품 연결용 원문 메타데이터 수집 실패: {exc}") from exc
            work_match_evidence = resolve_work_matches(
                titles=[
                    *_candidate_identity_titles(candidate),
                    identity_preflight_capture.title,
                    *[item.title for item in identity_preflight_capture.linked_page_evidence],
                ],
                source_ids=[source.id],
                structured_references=identity_preflight_capture.structured_work_references,
                outbound_links=identity_preflight_capture.outbound_links,
                content_type=content_type,
                source_url=str(identity_preflight_capture.url or candidate.get("url") or url),
            )
            candidate_work_ids = list(
                dict.fromkeys(item.menu_work_id for item in work_match_evidence)
            )
            if not candidate_work_ids:
                work_match_evidence = resolve_unmatched_work_with_llm(
                    titles=[
                        *_candidate_identity_titles(candidate),
                        identity_preflight_capture.title,
                        *[
                            item.title
                            for item in identity_preflight_capture.linked_page_evidence
                        ],
                    ],
                    source_url=str(candidate.get("url") or url),
                    structured_references=(
                        identity_preflight_capture.structured_work_references
                    ),
                    body_excerpt=identity_preflight_capture.text,
                    outbound_links=[
                        item.model_dump(mode="json")
                        for item in identity_preflight_capture.outbound_links
                    ],
                    content_type=content_type,
                    usage_records=work_match_usage_records,
                    audit=work_match_audit,
                )
                candidate_work_ids = list(
                    dict.fromkeys(item.menu_work_id for item in work_match_evidence)
                )
    if candidate_id and not candidate_work_ids:
        repository = AgentRepository()
        record_resolution = getattr(
            repository, "mark_discovery_candidate_work_resolution", None
        )
        record_unresolved = getattr(repository, "mark_discovery_work_unresolved", None)
        out_of_scope = (
            work_match_audit.get("subject_scope") == "not_anime_related"
            or work_match_audit.get("status") == "out_of_scope"
        )
        resolution_status = "out_of_scope" if out_of_scope else "unresolved_work_id"
        resolution_reason = str(
            work_match_audit.get("rationale")
            or (
                "The source does not identify an anime work as its subject."
                if out_of_scope
                else "An exact, unique anime work could not be confirmed from the source."
            )
        )
        if callable(record_resolution):
            record_resolution(
                candidate_id,
                resolution_reason,
                resolution_status=resolution_status,
                work_match_audit=work_match_audit,
            )
        elif callable(record_unresolved):
            record_unresolved(
                candidate_id,
                resolution_reason,
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "status": "out_of_scope_skipped" if out_of_scope else "work_id_unresolved_skipped",
                    "candidate_id": candidate_id,
                    "content_type": content_type,
                    "candidate_title": candidate.get("title"),
                    "captured_title": (
                        identity_preflight_capture.title if identity_preflight_capture else None
                    ),
                    "structured_work_references": (
                        [
                            item.model_dump(mode="json")
                            for item in identity_preflight_capture.structured_work_references
                        ]
                        if identity_preflight_capture
                        else []
                    ),
                    "outbound_links": (
                        [
                            item.model_dump(mode="json")
                            for item in identity_preflight_capture.outbound_links
                        ]
                        if identity_preflight_capture
                        else []
                    ),
                    "work_match_evidence": [],
                    "work_match_audit": work_match_audit,
                    "usage_records": work_match_usage_records,
                    "source_url": str(candidate.get("url") or url),
                    "reason": resolution_reason,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if out_of_scope:
            console.print("원문 주제가 특정 애니 작품이 아니어서 작품 ID를 만들지 않고 보류 기록을 남깁니다.")
        else:
            console.print("작품 ID가 확정되지 않아 생성 큐에는 보내지 않고 매칭 근거와 함께 보류 기록을 남깁니다.")
        return

    stage_timings["identity_preflight"] = round(
        perf_counter() - identity_preflight_started, 3
    )
    console.print("1/5 공식 페이지 수집 중…")
    stage_started = perf_counter()
    try:
        # Facts stay scoped to the selected article. The generation loader may
        # supplement visuals only from verified official pages for the same work.
        if identity_preflight_capture is not None:
            capture = identity_preflight_capture
        elif source.collection == "api":
            capture = collect_reference_page(source, url)
        else:
            # Keep both facts and article images scoped to this source page. Related
            # news pages on the same host often describe different events/products;
            # the repository adds only provenance-checked same-work artwork from its
            # reusable image library after this capture is persisted.
            capture = collect_official_page(
                source,
                url,
                expand_related_pages=False,
                max_cta_detail_pages=1,
                review_ambiguous_links=not prepare_only,
            )
        if capture is not identity_preflight_capture:
            work_match_usage_records.extend(capture.link_review_usage)
        if not prepare_only:
            capture.image_text_evidence, image_ocr_usage = extract_page_image_text(capture, source)
            work_match_usage_records.extend(image_ocr_usage)
            ocr_text_blocks = [
                f"[페이지 내 상세 이미지 OCR · 출처 이미지 {item['image_url']}]\n"
                f"{item['extracted_text']}"
                for item in capture.image_text_evidence
                if item.get("status") == "extracted" and item.get("extracted_text")
            ]
            if ocr_text_blocks:
                capture.text = (
                    capture.text.rstrip() + "\n\n" + "\n\n".join(ocr_text_blocks)
                ).strip()
        stage_timings["page_collection"] = round(perf_counter() - stage_started, 3)
        if not capture.text.strip():
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "status": "needs_crawler_update",
                        "candidate_id": candidate_id,
                        "content_type": content_type,
                        "source_url": str(capture.url),
                        "qa_report": [
                            {
                                "code": "SOURCE_BODY_EMPTY",
                                "message": (
                                    "Source page has no extractable article text; "
                                    "no assets were persisted."
                                ),
                                "severity": "error",
                                "owner": "crawler",
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            console.print(
                "원문 본문을 추출할 수 없어 이미지 적재와 생성 단계를 건너뜁니다."
            )
            return
        if source.id == "src_www_toei_anim_co_jp":
            headline_type = infer_toei_press_type(capture.title)
            if headline_type != "news":
                content_type = headline_type
        payload_prepare_started = perf_counter()
        payload = capture.model_dump(mode="json")
        if candidate_id:
            payload["published_at"] = payload.get("published_at") or candidate.get("published_at")
        if discovery_provenance:
            payload.update(discovery_provenance)
        if not work_match_evidence:
            work_match_evidence = resolve_work_matches(
                titles=[
                    *(_candidate_identity_titles(candidate) if candidate_id else []),
                    capture.title,
                    *[item.title for item in capture.linked_page_evidence],
                ],
                source_ids=[source.id],
                structured_references=capture.structured_work_references,
                outbound_links=capture.outbound_links,
                content_type=content_type,
                source_url=str(capture.url or url),
            )
            candidate_work_ids = list(
                dict.fromkeys(item.menu_work_id for item in work_match_evidence)
            )
        if not candidate_work_ids:
            work_match_evidence = resolve_unmatched_work_with_llm(
                titles=[
                    *(_candidate_identity_titles(candidate) if candidate_id else []),
                    capture.title,
                    *[item.title for item in capture.linked_page_evidence],
                ],
                source_url=str(capture.url or url),
                structured_references=capture.structured_work_references,
                body_excerpt=capture.text,
                outbound_links=[
                    item.model_dump(mode="json") for item in capture.outbound_links
                ],
                content_type=content_type,
                usage_records=work_match_usage_records,
                audit=work_match_audit,
            )
            candidate_work_ids = list(
                dict.fromkeys(item.menu_work_id for item in work_match_evidence)
            )
        payload["menu_work_ids"] = candidate_work_ids
        payload["work_match_evidence"] = [
            item.model_dump(mode="json") for item in work_match_evidence
        ]
        image_rows: list[dict] = []
        for image_url in capture.image_urls[:max_images]:
            image_id = (
                f"{source.id}-{hashlib.sha1(str(image_url).encode('utf-8')).hexdigest()[:14]}"
            )
            image_rows.append(
                {
                    "id": image_id,
                    "url": str(image_url),
                    "alt_text": capture.image_alt_text.get(str(image_url), ""),
                    "source_url": capture.image_source_pages.get(str(image_url), str(capture.url)),
                    "source_page_title": capture.image_source_titles.get(str(image_url), ""),
                    "asset_kind": capture.image_asset_kinds.get(str(image_url), ""),
                    "depicted_entities": capture.image_depicted_entities.get(str(image_url), []),
                    "local_path": "",
                    "download_status": "pending",
                    "rights_status": RightsStatus.NEEDS_REVIEW.value,
                    "error": "",
                }
            )
        payload["images"] = image_rows
        type_signal_checks: dict[str, bool] = {}
        if content_type:
            profile = get_type_crawler(content_type).profile
            type_signal_checks = profile_signal_checks(
                profile,
                f"{payload.get('title', '')}\n{capture.title}\n{capture.text}",
            )
            payload["content_type"] = content_type
            payload["type_capture_checks"] = type_signal_checks
            payload["type_signal_review"] = {
                "status": (
                    "matched" if any(type_signal_checks.values()) else "needs_semantic_review"
                ),
                "requested_content_type": requested_content_type,
                "signals": type_signal_checks,
                "note": (
                    "lexical signals are advisory; final type fit remains independently reviewed"
                ),
            }
        stage_timings["identity_and_payload_preparation"] = round(
            perf_counter() - payload_prepare_started, 3
        )
        capture_persist_started = perf_counter()
        repository = AgentRepository()
        if content_type in {"event", "goods"}:
            temporal = expiry_evidence(payload, content_type)
            if temporal["status"] != "active":
                status = (
                    "stale_candidate_skipped"
                    if temporal["status"] == "expired"
                    else "expiry_unresolved_held"
                )
                result = {
                    "status": status,
                    "candidate_id": candidate_id,
                    "content_type": content_type,
                    "title": str(payload.get("title") or capture.title),
                    "source_url": str(capture.url),
                    "event_end": temporal.get("end_date") if content_type == "event" else None,
                    "sale_end": temporal.get("end_date") if content_type == "goods" else None,
                    "expiry_evidence": temporal.get("evidence"),
                    "reason": (
                        "The explicit event or sales end date has passed; no page data was stored."
                        if temporal["status"] == "expired"
                        else "No source-backed event end date or explicit current goods availability status was found; held without storing page data."
                    ),
                }
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                console.print(
                    f"기간 판정 {status}: {payload.get('title') or capture.title}; 생성 큐 적재를 건너뜁니다."
                )
                return
            payload["temporal_expiry"] = temporal
            payload["event_end" if content_type == "event" else "sale_end"] = temporal["end_date"]
            if temporal.get("availability_status"):
                payload["availability_status"] = temporal["availability_status"]
            if temporal.get("release_date"):
                payload["release_date"] = temporal["release_date"]
        elif content_type == "news":
            temporal_news = immediate_news_action_expiry(payload)
            if temporal_news["status"] in {"expired", "unresolved"}:
                status = (
                    "stale_candidate_skipped"
                    if temporal_news["status"] == "expired"
                    else "expiry_unresolved_held"
                )
                result = {
                    "status": status,
                    "candidate_id": candidate_id,
                    "content_type": content_type,
                    "title": str(payload.get("title") or capture.title),
                    "source_url": str(capture.url),
                    "published_at": temporal_news.get("published_at"),
                    "expiry_evidence": temporal_news.get("evidence"),
                    "reason": "Time-sensitive start news is past its publication/action date or has no date; no page data was stored.",
                }
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                console.print(
                    f"시의성 뉴스 보류 {status}: {payload.get('title') or capture.title}; 페이지 저장과 생성 큐 적재를 건너뜁니다."
                )
                return
        if prepare_only:
            repository.persist_page_capture(payload, source.region)
        stage_timings["initial_capture_persist"] = round(
            perf_counter() - capture_persist_started, 3
        )
        if type_signal_checks and not any(type_signal_checks.values()):
            console.print(
                "유형 키워드 신호가 약해도 원문과 작품이 확인되어 초안 생성·독립 검수로 진행합니다."
            )
        console.print("2/5 원문 자료 정리 중…")
        stage_started = perf_counter()
        if prepare_only:
            # The continuous crawler preserves the complete captured page as
            # explicitly unverified source material. Fact extraction and fact
            # verification belong to later content work, so a missing LLM key
            # must not prevent source data from being stored.
            source_evidence = SourceEvidence(
                id=source.id,
                title=str(payload.get("title") or source.name),
                url=str(payload.get("url") or url),
                publisher=source.name,
                is_official=source.role == "primary",
                published_at=payload.get("published_at"),
                checked_at=payload.get("fetched_at"),
                role=source.role,
                region=source.region,
                allowed_asset_hosts=source.allowed_asset_hosts,
                discovered_via_source_id=payload.get("discovered_via_source_id"),
                discovery_url=payload.get("discovery_url"),
                discovery_title=payload.get("discovery_title"),
            )
            raw_statement = str(payload.get("text") or "").strip()
            raw_request_id = f"crawl-{hashlib.sha1(str(payload.get('url') or url).encode('utf-8')).hexdigest()[:14]}"
            raw_images = [
                ImageAsset(
                    id=str(image["id"]),
                    url=str(image.get("url") or "") or None,
                    source_url=str(image.get("source_url") or payload.get("url") or url),
                    official=source.role == "primary",
                    menu_work_ids=candidate_work_ids,
                    rights_status=RightsStatus.NEEDS_REVIEW,
                    asset_kind=(
                        image.get("asset_kind")
                        if image.get("asset_kind") in {
                            "official_key_visual", "catalog_poster", "catalog_cover",
                            "character_art", "scene_still", "manga_panel",
                            "goods_product", "event_visual", "fan_meme",
                            "editorial_photo", "unknown",
                        }
                        else "unknown"
                    ),
                    depicted_entities=image.get("depicted_entities") or [],
                    source_description=str(image.get("alt_text") or image.get("source_page_title") or ""),
                )
                for image in payload.get("images", [])
                if image.get("url")
            ]
            package = ContentPackage(
                request_id=raw_request_id,
                topic=(source.work_title_ko or str(payload.get("title") or source.name))[:300],
                content_kind=ContentKind.INFORMATION,
                content_type=content_type,
                event_start=payload.get("event_start"),
                event_end=payload.get("event_end"),
                event_location=payload.get("event_location"),
                menu_work_ids=candidate_work_ids,
                work_match_evidence=work_match_evidence,
                sources=[source_evidence],
                facts=[
                    Fact(
                        id=f"{raw_request_id}-source-page",
                        statement=raw_statement,
                        source_ids=[source.id],
                        verified=False,
                        time_sensitive=content_type in {"news", "event", "goods"},
                        fact_type="source_page_capture",
                        details={
                            "verification_status": "not_performed",
                            "captured_title": str(payload.get("title") or ""),
                            "captured_url": str(payload.get("url") or url),
                            "published_at": payload.get("published_at"),
                            "fetched_at": payload.get("fetched_at"),
                        },
                    )
                ] if raw_statement else [],
                images=raw_images,
            )
            event_date_evidence = str(payload.get("event_date_evidence") or "").strip()
            if (
                content_type == "event"
                and package.event_start
                and package.event_end
                and event_date_evidence
            ):
                package.facts.append(
                    Fact(
                        id=f"{raw_request_id}-event-period-source",
                        statement=(
                            f"행사 기간: {package.event_start}부터 {package.event_end}까지"
                        ),
                        source_ids=[source.id],
                        verified=False,
                        time_sensitive=True,
                        fact_type="event_schedule",
                        details={
                            "source_quote": event_date_evidence,
                            "evidence_kind": "source_structured_event",
                            "verification_status": "source_capture_only",
                        },
                    )
                )
            event_location_evidence = str(
                payload.get("event_location_evidence") or ""
            ).strip()
            if content_type == "event" and package.event_location and event_location_evidence:
                package.facts.append(
                    Fact(
                        id=f"{raw_request_id}-event-location-source",
                        statement=f"행사 장소: {package.event_location}",
                        source_ids=[source.id],
                        verified=False,
                        time_sensitive=True,
                        fact_type="event_location",
                        details={
                            "source_quote": event_location_evidence,
                            "evidence_kind": "source_structured_event",
                            "verification_status": "source_capture_only",
                        },
                    )
                )
            temporal = payload.get("temporal_expiry") or {}
            if temporal.get("status") == "active" and temporal.get("end_date"):
                if content_type == "event":
                    package.event_end = str(temporal["end_date"])
                elif content_type == "goods":
                    package.facts.append(
                        Fact(
                            id=f"{package.request_id}-sales-period",
                            statement=f"판매 종료일: {temporal['end_date']}",
                            source_ids=[source.id],
                            verified=False,
                            time_sensitive=True,
                            fact_type="sales_period",
                            details={
                                "sale_end": str(temporal["end_date"]),
                                "source_quote": str(temporal.get("evidence") or ""),
                            },
                        )
                    )
            extraction_usage = {
                "stage": "fact_extraction",
                "status": "skipped",
                "reason": "crawler_raw_capture_no_fact_verification",
            }
            detected_type = None
            console.print("사실 검증 없이 원문 전체를 미검증 자료로 보존합니다.")
        else:
            package, extraction_usage = extract_page_payload(payload)
            detected_type = infer_strong_content_type(package)
        if detected_type and detected_type != content_type:
            supported_types = {item.value for item in source.content_types}
            if detected_type not in supported_types:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(
                        {
                            "status": "content_type_mismatch_skipped",
                            "candidate_id": candidate_id,
                            "request_id": package.request_id,
                            "requested_content_type": requested_content_type,
                            "evidence_content_type": detected_type,
                            "supported_source_types": sorted(supported_types),
                            "topic": package.topic,
                            "source_urls": [str(item.url) for item in package.sources],
                            "reason": (
                                "원문 제목·검증 팩트가 다른 유형을 가리키며, "
                                "연결된 출처가 그 유형을 "
                                "지원하지 않아 카피 생성 전에 중단했습니다."
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                console.print(
                    f"원문 유형이 요청과 다릅니다 ({content_type} → {detected_type}). "
                    "이 유형을 지원하는 출처가 없어 카피 생성 전에 건너뜁니다."
                )
                return
            console.print(
                f"원문 제목·팩트로 유형을 다시 분류합니다: {content_type} → {detected_type}"
            )
            content_type = detected_type
            payload["content_type"] = detected_type
            payload["type_signal_review"] = {
                "status": "rerouted_before_copy_generation",
                "requested_content_type": requested_content_type,
                "evidence_content_type": detected_type,
            }
        if content_type in {"event", "goods"}:
            temporal_candidate = package.model_dump(mode="json")
            temporal_candidate.update(
                {
                    "text": payload.get("text", ""),
                    "main_text": payload.get("main_text", ""),
                    "published_at": payload.get("published_at"),
                    "event_start": payload.get("event_start"),
                    "event_end": payload.get("event_end"),
                    "availability_status": payload.get("availability_status"),
                    "availability_evidence": payload.get("availability_evidence"),
                }
            )
            temporal = expiry_evidence(temporal_candidate, content_type)
            if temporal["status"] != "active":
                status = (
                    "stale_candidate_skipped"
                    if temporal["status"] == "expired"
                    else "expiry_unresolved_held"
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(
                        {
                            "status": status,
                            "candidate_id": candidate_id,
                            "request_id": package.request_id,
                            "content_type": content_type,
                            "topic": package.topic,
                            "source_url": str(capture.url),
                            "event_end": temporal.get("end_date") if content_type == "event" else None,
                            "sale_end": temporal.get("end_date") if content_type == "goods" else None,
                            "expiry_evidence": temporal.get("evidence"),
                            "reason": (
                                "The explicit event or sales end date has passed; no new page data was stored."
                                if temporal["status"] == "expired"
                                else "No source-backed event end date or explicit current goods availability status was found; held without storing page data."
                            ),
                            "menu_work_ids": package.menu_work_ids,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                console.print(
                    f"기간 판정 {status}: {package.topic}; 생성 큐 적재를 건너뜁니다."
                )
                return
            payload["temporal_expiry"] = temporal
            if content_type == "event":
                package.event_start = package.event_start or payload.get("event_start")
                package.event_end = package.event_end or str(temporal["end_date"])
            elif content_type == "goods":
                if temporal.get("end_date") and not any(
                    fact.fact_type == "sales_period" and fact.details.get("sale_end")
                    for fact in package.facts
                ):
                    package.facts.append(
                        Fact(
                            id=f"{package.request_id}-sales-period",
                            statement=f"판매 종료일: {temporal['end_date']}",
                            source_ids=[source.id],
                            verified=False,
                            time_sensitive=True,
                            fact_type="sales_period",
                            details={
                                "sale_end": str(temporal["end_date"]),
                                "source_quote": str(temporal.get("evidence") or ""),
                            },
                        )
                    )
                elif (
                    temporal.get("availability_status") == "upcoming_release"
                    and temporal.get("release_date")
                ):
                    if not any(
                        fact.details.get("release_date") == temporal["release_date"]
                        for fact in package.facts
                    ):
                        package.facts.append(
                            Fact(
                                id=f"{package.request_id}-upcoming-release",
                                statement=(
                                    f"Scheduled product release date: {temporal['release_date']}"
                                ),
                                source_ids=[source.id],
                                verified=False,
                                time_sensitive=True,
                                fact_type="product_availability",
                                details={
                                    "release_date": str(temporal["release_date"]),
                                    "availability_status": "upcoming_release",
                                    "source_quote": str(temporal.get("evidence") or ""),
                                },
                            )
                        )
                elif temporal.get("availability_status"):
                    if not any(
                        fact.fact_type == "availability_status"
                        for fact in package.facts
                    ):
                        package.facts.append(
                            Fact(
                                id=f"{package.request_id}-availability-status",
                                statement=f"판매 상태 확인: {temporal['availability_status']}",
                                source_ids=[source.id],
                                verified=False,
                                time_sensitive=True,
                                fact_type="availability_status",
                                details={
                                    "availability_status": str(temporal["availability_status"]),
                                    "source_quote": str(temporal.get("evidence") or ""),
                                },
                            )
                        )
                    package.feed_valid_until = package.feed_valid_until or (
                        datetime.now(KST).date() + timedelta(days=2)
                    ).isoformat()
        # This command already knows the requested editorial type. Set it before
        # graph/storage image supplementation so event/goods restrictions apply
        # while the asset pool is being built, not only during final QA.
        package.content_type = content_type
        package.work_match_evidence = work_match_evidence
        package.menu_work_ids = candidate_work_ids
        catalog_image_count = _add_same_work_catalog_images(
            package,
            registry.sources,
            max_images,
        )
        if catalog_image_count:
            console.print(
                f"Same-work catalog image fallback: {catalog_image_count} image(s) "
                f"for work IDs {package.menu_work_ids}."
            )
        stage_timings["fact_extraction"] = round(perf_counter() - stage_started, 3)
    except (ValueError, KeyError, httpx.HTTPError, RuntimeError) as exc:
        raise typer.BadParameter(f"크롤링·팩트 추출 단계에서 중단: {exc}") from exc

    console.print("3/5 자료 저장 및 수집 메타데이터 정리 중…")
    stage_started = perf_counter()
    payload["event_end"] = package.event_end
    repository.persist_page_capture(
        payload, source.region, package.event_end, package=package
    )
    repository.prepare_lifecycle(package)
    research_information(
        {"package": package.model_dump(mode="json"), "trace": [], "repository": repository}
    )
    stage_timings["database_and_research"] = round(perf_counter() - stage_started, 3)
    if candidate_is_stale(package.model_dump(mode="json"), content_type or ""):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "status": "stale_evidence_persisted",
                    "request_id": package.request_id,
                    "topic": package.topic,
                    "content_type": content_type,
                    "event_end": package.event_end,
                    "reason": "Evidence was retained; expired event/goods are not sent to generation.",
                    "source_urls": [str(item.url) for item in package.sources],
                    "fact_count": len(package.facts),
                    "menu_work_ids": package.menu_work_ids,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        console.print("기간이 지난 후보의 원문과 사실을 저장했습니다. 생성 큐에는 보내지 않습니다.")
        return

    duplicate_started = perf_counter()
    duplicate = repository.find_duplicate_content(package, retry_same_request=retry_failed)
    stage_timings["duplicate_check"] = round(perf_counter() - duplicate_started, 3)
    if duplicate:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "status": "duplicate_evidence_persisted",
                    "duplicate_of": duplicate,
                    "request_id": package.request_id,
                    "topic": package.topic,
                    "source_urls": [str(source.url) for source in package.sources],
                    "fact_count": len(package.facts),
                    "menu_work_ids": package.menu_work_ids,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        console.print("중복 콘텐츠 후보의 출처와 사실은 저장했고, 중복 생성을 막았습니다.")
        return

    console.print(f"4/5 이미지 권리 확인 및 다운로드 중… (후보 {len(package.images)}개)")
    stage_started = perf_counter()
    image_result = image_curator(
        {
            "package": package.model_dump(mode="json"),
            "trace": [],
            "download_images": True,
            "repository": repository,
        }
    )
    stage_timings["image_curation_and_download"] = round(perf_counter() - stage_started, 3)
    curated_package = ContentPackage.model_validate(image_result["package"])
    usable_images = [
        image
        for image in curated_package.images
        if image.eligible_for_editorial_use(
            curated_package.sources,
            allow_risk_accepted=True,
        )
        and image.download_status in {"downloaded", "available"}
    ]
    # Sparse imagery is a hard preflight condition: every card must receive a real,
    # relevant image, and placeholder graphics are not a substitute for source art.

    if prepare_only:
        from ani_menu_agent.pipelines.common.jobs import PipelineLane, PipelineStage

        repository.enqueue_pipeline_job(
            lane=PipelineLane.TIME_SENSITIVE,
            stage=PipelineStage.GENERATE,
            idempotency_key=f"{package.request_id}:generate:0",
            request_id=package.request_id,
            payload={
                "content_type": content_type,
                "content_kind": ContentKind.INFORMATION.value,
                "revision_count": 0,
                "review_failures": 0,
                "max_revision": POLICY.max_review_failures,
                "usage_records": [extraction_usage, *work_match_usage_records],
                "trace": ["crawl_prepare", "evidence_persisted", "images_curated"],
            },
        )
        prepared = {
            "status": "generation_pending",
            "request_id": package.request_id,
            "content_type": content_type,
            "topic": package.topic,
            "source_urls": [str(item.url) for item in curated_package.sources],
            "source_count": len(curated_package.sources),
            "fact_count": len(curated_package.facts),
            "image_count": len(usable_images),
            "stage_timings_seconds": {
                **stage_timings,
                "total": round(perf_counter() - run_started, 3),
            },
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"수집 자료와 생성 작업을 DB에 저장했습니다: {package.request_id}")
        return

    console.print("5/5 카드 생성 및 독립 검수 중…")
    try:
        _stored_package, result = _run_generation_from_db(
            package.request_id,
            ContentKind.INFORMATION,
            requested_content_type=content_type,
            initial_usage_records=[extraction_usage, *work_match_usage_records],
            repository=repository,
        )
    except (ValueError, RuntimeError) as exc:
        raise typer.BadParameter(f"DB 기반 콘텐츠 생성 단계에서 중단: {exc}") from exc

    stage_timings.update(result.get("stage_timings_seconds", {}))
    stage_timings["total"] = round(perf_counter() - run_started, 3)
    result["stage_timings_seconds"] = stage_timings
    if work_match_audit:
        result["work_match_audit"] = work_match_audit
    _add_usage_summary(result)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    exported = _export_rendered_cards(
        result.get("package", {}), output.parent / "cards" / package.request_id
    )
    report = result.get("quality_report", {})
    console.print(f"원문 캡처: {capture.url}")
    console.print(
        f"검증 팩트 {len(package.facts)}건 · 사용 가능 이미지 {len(usable_images)}건 DB 적재"
    )
    _print_run_metrics(result)
    if exported:
        console.print(f"카드 PNG 파일: {exported[0].parent.resolve()} ({len(exported)}장)")
    console.print(f"카드 생성 결과: {output.resolve()}")
    console.print(f"최종 상태: {result['status']}")
    if report:
        console.print(f"품질 점수: {report.get('total')}/100 · 판정: {report.get('decision')}")
        console.print(f"치명 차단: {report.get('hard_blockers', [])}")
        console.print(f"점수 항목: {report.get('dimensions', {})}")
    console.print(" -> ".join(result.get("trace", [])))


def _crawl_generate_humor_candidate(candidate: dict, output: Path) -> dict:
    """Turn one automated trend signal into a humor package, then use the shared QA graph."""
    if candidate_is_stale(candidate, ContentKind.HUMOR.value):
        return {
            "status": "stale_candidate_skipped",
            "candidate_id": candidate.get("id"),
            "published_at": candidate.get("published_at"),
        }
    registry = load_source_registry()
    source = next(
        (item for item in registry.sources if item.id == str(candidate.get("source_id", ""))),
        None,
    )
    if (
        source is None
        or source.role != "trend"
        or source.status != "trend_only"
        or source.collection == "manual"
        or ContentKind.HUMOR.value not in {item.value for item in source.content_types}
    ):
        raise ValueError("유머 생성에는 자동수집 승인된 trend_only 출처만 사용할 수 있습니다.")

    topic = str(candidate.get("title") or "").strip()
    summary = " ".join(str(candidate.get("summary") or "").split())
    source_url = str(candidate.get("url") or "")
    community_posts = [
        post
        for post in candidate.get("community_posts", [])
        if isinstance(post, dict)
        and len(" ".join(str(post.get("text") or "").split())) >= 25
        and str(post.get("url") or "").startswith("https://")
    ]
    if len(community_posts) < 2:
        return {
            "status": "insufficient_discussion_evidence",
            "candidate_id": candidate.get("id"),
            "reason": "공개된 서로 다른 작성자의 토론 글과 답글을 확인하지 못했습니다.",
        }
    work_ids = _candidate_menu_work_ids(candidate, source, ContentKind.HUMOR.value)
    work_match_usage_records: list[dict] = []
    work_match_audit: dict = {}
    if not work_ids and topic:
        fallback_evidence = resolve_unmatched_work_with_llm(
            titles=[topic],
            source_url=source_url,
            body_excerpt="\n".join(
                [summary, *(str(post.get("text") or "") for post in community_posts[:4])]
            )[:2_000],
            content_type=ContentKind.HUMOR.value,
            usage_records=work_match_usage_records,
            audit=work_match_audit,
        )
        work_ids = list(dict.fromkeys(item.menu_work_id for item in fallback_evidence))
    humor_signals = profile_signal_checks(
        get_type_crawler(ContentKind.HUMOR.value).profile,
        f"{topic}\n{summary}",
    )
    if humor_signals and not any(humor_signals.values()):
        return {
            "status": "trend_signal_incomplete",
            "candidate_id": candidate.get("id"),
            "reason": "Trend text has no configured community/anime discussion signal.",
            "signals": humor_signals,
        }
    if not topic or len(summary) < 20 or not work_ids:
        return {
            "status": "trend_signal_incomplete",
            "candidate_id": candidate.get("id"),
            "reason": "작품 제목 매칭 또는 생성 가능한 토론 신호가 부족합니다.",
            "work_match_audit": work_match_audit,
        }

    candidate_id = str(candidate.get("id") or source_url)
    request_id = "humor-" + hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:20]
    now = datetime.now(UTC).isoformat()
    evidence = SourceEvidence(
        id=source.id,
        title=topic,
        url=source_url,
        publisher=source.name,
        is_official=False,
        published_at=candidate.get("published_at"),
        checked_at=now,
        role="trend",
        region=source.region,
    )
    facts = []
    for index, post in enumerate(community_posts[:6], start=1):
        post_text = " ".join(str(post.get("text") or "").split())[:700]
        post_url_hash = hashlib.sha256(
            str(index).encode() + str(post.get("url") or "").encode()
        ).hexdigest()[:12]
        facts.append(
            Fact(
                id=f"community-{post_url_hash}",
                statement=f"공개 커뮤니티 게시물에 나타난 개인 의견: {post_text}",
                source_ids=[source.id],
                verified=False,
                fact_type="community_viewpoint",
                details={
                    "candidate_id": candidate.get("id"),
                    "observed_at": post.get("published_at") or now,
                    "source_url": str(post.get("url") or source_url),
                    "editorial_use": "paraphrase_as_individual_opinion",
                    "source_quote": post_text,
                },
            )
        )
    package = ContentPackage(
        request_id=request_id,
        topic=topic,
        content_kind=ContentKind.HUMOR,
        content_type=ContentKind.HUMOR.value,
        menu_work_ids=work_ids,
        audience_region="KR",
        sources=[evidence],
        facts=facts,
    )
    repository = AgentRepository()
    duplicate = repository.find_duplicate_content(package)
    if duplicate:
        return {
            "status": "duplicate_skipped",
            "candidate_id": candidate_id,
            "duplicate_of": duplicate,
        }

    _add_same_work_catalog_images(
        package,
        registry.sources,
        20,
    )
    repository.prepare_lifecycle(package)
    research_humor(
        {"package": package.model_dump(mode="json"), "trace": [], "repository": repository}
    )
    image_curator(
        {
            "package": package.model_dump(mode="json"),
            "trace": [],
            "download_images": True,
            "repository": repository,
        }
    )
    _stored_package, result = _run_generation_from_db(
        request_id,
        ContentKind.HUMOR,
        requested_content_type=ContentKind.HUMOR.value,
        initial_usage_records=work_match_usage_records,
        repository=repository,
    )
    if work_match_audit:
        result["work_match_audit"] = work_match_audit
    _add_usage_summary(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _export_rendered_cards(result.get("package", {}), output.parent / "cards" / request_id)
    return {
        "status": result.get("status", "unknown"),
        "candidate_id": candidate_id,
        "request_id": request_id,
        "output": str(output.resolve()),
        "quality_report": result.get("quality_report", {}),
    }


@app.command("crawl-type")
def crawl_type(
    content_type: Annotated[
        str,
        typer.Option("--content-type", help="10개 유형 중 하나"),
    ],
    work_title: Annotated[str | None, typer.Option("--work-title")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=20)] = 10,
    target_passed: Annotated[
        int | None,
        typer.Option("--target-passed", min=1, max=20, help="이번 실행에서 확보할 검수 통과 수"),
    ] = None,
    retry_candidate_id: Annotated[
        str | None,
        typer.Option("--retry-candidate-id", help="명시한 후보 1건만 제한적으로 재시도"),
    ] = None,
    retry_note: Annotated[
        str | None,
        typer.Option("--retry-note", help="재시도 전에 해결한 원인 또는 변경사항"),
    ] = None,
    retry_prior_failures: Annotated[
        bool,
        typer.Option(
            "--retry-prior-failures",
            help="저장된 고유 후보 중 통과하지 못한 후보를 새 버전으로 재평가",
        ),
    ] = False,
    retry_work_identity_failures: Annotated[
        bool,
        typer.Option(
            "--retry-work-identity-failures",
            help="작품 ID 미확정 후보를 한 번 재평가",
        ),
    ] = False,
    stored_candidates_only: Annotated[
        bool,
        typer.Option(
            "--stored-candidates-only",
            help="출처 목록을 다시 조회하지 않고 DB에 저장된 후보만 처리",
        ),
    ] = False,
    retry_before: Annotated[
        str | None,
        typer.Option(
            "--retry-before",
            help="이 시각 이전에 처리된 실패만 재평가하는 고정 실행 경계(ISO-8601 UTC)",
        ),
    ] = None,
    region_policy: Annotated[
        str,
        typer.Option(
            "--region-policy",
            help="이전 명령과의 호환용이며 후보 정렬에는 영향을 주지 않습니다.",
        ),
    ] = "mixed",
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="이번 실행의 생성 결과 폴더"),
    ] = Path("output/crawl_runs"),
    enqueue_only: bool = False,
) -> dict:
    """유형별 수집부터 DB 적재·콘텐츠 생성·독립 검수까지 연결해 실행합니다."""
    if content_type not in {item.value for item in CONTENT_TYPE_LABELS}:
        raise typer.BadParameter(
            f"지원하지 않는 유형입니다: {content_type}. "
            f"가능한 값: {', '.join(item.value for item in CONTENT_TYPE_LABELS)}"
        )
    if region_policy not in {"kr_first", "mixed", "jp_first"}:
        raise typer.BadParameter("region-policy는 kr_first, mixed, jp_first 중 하나여야 합니다.")

    if bool(retry_candidate_id) != bool(retry_note and retry_note.strip()):
        raise typer.BadParameter(
            "명시적 재시도에는 --retry-candidate-id와 비어 있지 않은 "
            "--retry-note가 함께 필요합니다."
        )
    if stored_candidates_only and not retry_prior_failures:
        raise typer.BadParameter(
            "--stored-candidates-only에는 --retry-prior-failures가 필요합니다."
        )
    retry_cutoff = None
    if retry_before:
        try:
            retry_cutoff = datetime.fromisoformat(retry_before.replace("Z", "+00:00"))
            if retry_cutoff.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError as exc:
            raise typer.BadParameter("--retry-before는 시간대가 포함된 ISO-8601이어야 합니다.") from exc

    # Re-evaluate old failed leads once after agent changes. Successful, stale,
    # and duplicate records stay protected; source-changed leads can be retried
    # again because their fingerprint differs from the last attempt.
    retryable_statuses = {
        "stale_candidate_skipped",
        "expiry_unresolved_held",
        "needs_crawler_update",
        "work_id_unresolved_skipped",
        "original_source_unresolved",
        "candidate_pipeline_error",
        "insufficient_discussion_evidence",
        "content_type_mismatch_skipped",
        "rejected_archived",
    }
    repository = AgentRepository()
    previous_candidate_attempts = repository.list_candidate_attempts(content_type)
    if retry_candidate_id:
        previous = previous_candidate_attempts.get(retry_candidate_id)
        if previous is None:
            raise typer.BadParameter("이 유형에서 처리한 기록이 없는 후보는 재시도할 수 없습니다.")
        if previous.get("status") not in retryable_statuses:
            raise typer.BadParameter(
                f"이 상태는 수정 재시도 대상이 아닙니다: {previous.get('status')}"
            )
        previous_match_audit = previous.get("work_match_audit") or {}
        corrected_catalog_evidence = (
            previous_match_audit.get("status") == "invalid_source_title_evidence"
            and previous_match_audit.get("action") == "match_existing"
            and len(previous_match_audit.get("catalog_matches") or []) == 1
        )
        if (
            int(previous.get("retry_count", 0)) >= POLICY.max_candidate_retries
            and not corrected_catalog_evidence
        ):
            raise typer.BadParameter(
                f"후보별 재시도 한도({POLICY.max_candidate_retries})를 이미 사용했습니다."
            )
        try:
            retry_payload = repository.load_discovery_candidate(retry_candidate_id)
            retry_item = DiscoveryItem.model_validate(retry_payload)
        except (ValueError, TypeError) as exc:
            raise typer.BadParameter(f"재시도 후보를 읽지 못했습니다: {exc}") from exc
        if content_type not in set(retry_item.content_types):
            raise typer.BadParameter("재시도 후보가 지정한 콘텐츠 유형에 연결되어 있지 않습니다.")

    discovery_limit = min(max(limit * 10, limit), 200)
    crawler = get_type_crawler(content_type)
    provided_work_title = (work_title or "").strip() or None
    sampled_work_titles = (
        _rotating_catalog_work_titles(3)
        if content_type in {"character", "guide"} and not provided_work_title
        else []
    )
    run_titles = sampled_work_titles or [provided_work_title]
    runs = []
    if retry_candidate_id:
        collected_items = [retry_item]
    else:
        collected_items = []
        if not stored_candidates_only:
            for index, title in enumerate(run_titles):
                runs.append(
                    crawler.run(
                        limit=(
                            min(max(limit * 4, 20), 80)
                            if sampled_work_titles
                            else discovery_limit
                        ),
                        region_policy=region_policy,
                        work_title=title,
                        unfiltered_sources=(
                            {"onnada"}
                            if content_type == "character"
                            and sampled_work_titles
                            and index == 0
                            else None
                        ),
                        capture_images=False,
                    )
                )
            seen_item_ids: set[str] = set()
            for crawler_run in runs:
                for item in crawler_run.items:
                    if item.id not in seen_item_ids:
                        collected_items.append(item)
                        seen_item_ids.add(item.id)

        if retry_prior_failures or retry_work_identity_failures:
            stored_items = [
                DiscoveryItem.model_validate(payload)
                for payload in repository.list_discovery_candidates(content_type)
            ]
            merged_items = {item.id: item for item in stored_items}
            # Prefer the current source scan over its older stored snapshot.
            for item in collected_items:
                merged_items[item.id] = item
            collected_items = list(merged_items.values())

    run_folder = output_dir / content_type / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_folder.mkdir(parents=True, exist_ok=True)
    eligible_items = []
    for item in collected_items:
        previous = previous_candidate_attempts.get(item.id)
        if retry_candidate_id or previous is None:
            eligible_items.append(item)
            continue
        retry_unresolved_identity = (
            retry_work_identity_failures
            and previous.get("status") == "work_id_unresolved_skipped"
        )
        retry_recal_expiry_revision = (
            retry_prior_failures
            and content_type == "event"
            and item.source_id in {"recal_kr", "recal_music"}
            and previous.get("status") == "expiry_unresolved_held"
            and previous.get("expiry_revision") != RECAL_EXPIRY_REVISION
        )
        previous_match_audit = previous.get("work_match_audit") or {}
        retry_scope_conflict = (
            retry_unresolved_identity
            and previous_match_audit.get("status") == "scope_conflicts_with_work_match"
            and previous_match_audit.get("subject_scope") == "not_anime_related"
        )
        prior_catalog_matches = previous_match_audit.get("catalog_matches") or []
        retry_grounded_short_title = (
            retry_unresolved_identity
            and previous_match_audit.get("status") == "invalid_source_title_evidence"
            and previous_match_audit.get("action") == "match_existing"
            and len(prior_catalog_matches) == 1
            and bool(previous_match_audit.get("evidence_title"))
        )
        retry_other_failure = (
            retry_prior_failures
            and previous.get("status") in retryable_statuses
        )
        if not (
            retry_unresolved_identity
            or retry_other_failure
            or retry_recal_expiry_revision
        ):
            continue
        old_fingerprint = str(previous.get("candidate_fingerprint") or "")
        current_fingerprint = discovery_candidate_fingerprint(item.model_dump(mode="json"))
        retry_count = int(previous.get("retry_count", 0))
        attempted_at = str(previous.get("attempted_at") or "")
        try:
            previous_attempted_at = datetime.fromisoformat(
                attempted_at.replace("Z", "+00:00")
            )
        except ValueError:
            previous_attempted_at = None
        is_prior_to_cutoff = (
            retry_cutoff is None
            or (previous_attempted_at is not None and previous_attempted_at < retry_cutoff)
        )
        if retry_recal_expiry_revision:
            # Recheck Re:CAL events once under the corrected structured-date
            # extraction policy, even if they exhausted the generic retry cap.
            eligible_items.append(item)
        elif retry_unresolved_identity:
            # Retry once when a prior audit has a specific, corrected resolution
            # path. The updated outcome leaves this retryable state, preventing
            # the candidate from looping on every crawler scan.
            if retry_count < 1 or (
                retry_count < 2
                and (retry_scope_conflict or retry_grounded_short_title)
            ):
                eligible_items.append(item)
        elif (retry_count < 1 and is_prior_to_cutoff) or (
            old_fingerprint and current_fingerprint != old_fingerprint
        ):
            eligible_items.append(item)
    eligible_before_stale_filter = len(eligible_items)
    stale_candidates_seen = sum(
        candidate_is_stale(item.model_dump(mode="json"), content_type)
        for item in eligible_items
    )
    fresh_items = (
        eligible_items
        if retry_candidate_id
        else _latest_first_candidate_items(eligible_items, limit)
    )
    diversity_deferred = len(eligible_items) - len(fresh_items)
    skipped_attempted = len(collected_items) - eligible_before_stale_filter
    outcomes: list[dict] = []
    passed_count = 0
    quota_exhausted = False
    for index, item in enumerate(fresh_items, start=1):
        if quota_exhausted:
            break
        if target_passed is not None and passed_count >= target_passed:
            break
        candidate = item.model_dump(mode="json")
        candidate_id = str(candidate.get("id") or f"candidate-{index}")
        file_token = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:12]
        result_path = run_folder / f"{index:02d}-{file_token}.json"
        temporal_skip = None
        if content_type in {"event", "goods"}:
            temporal = expiry_evidence(candidate, content_type)
            if temporal["status"] == "expired":
                temporal_skip = {
                    "status": "stale_candidate_skipped",
                    "content_type": content_type,
                    "event_end": temporal.get("end_date") if content_type == "event" else None,
                    "sale_end": temporal.get("end_date") if content_type == "goods" else None,
                    "expiry_evidence": temporal.get("evidence"),
                    "reason": "The listing already exposes an expired period; the article page was not fetched.",
                }
        elif content_type == "news":
            temporal_news = immediate_news_action_expiry(candidate)
            if temporal_news["status"] in {"expired", "unresolved"}:
                temporal_skip = {
                    "status": (
                        "stale_candidate_skipped"
                        if temporal_news["status"] == "expired"
                        else "expiry_unresolved_held"
                    ),
                    "content_type": content_type,
                    "published_at": temporal_news.get("published_at"),
                    "expiry_evidence": temporal_news.get("evidence"),
                    "reason": "Time-sensitive start news is past its publication day or has no publication date; the article page was not fetched.",
                }
        try:
            if temporal_skip:
                outcome = {**temporal_skip, "candidate_id": candidate_id}
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(
                    json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            elif content_type == ContentKind.HUMOR.value and candidate.get("source_role") == "trend":
                outcome = _crawl_generate_humor_candidate(candidate, result_path)
                if not result_path.exists():
                    result_path.write_text(
                        json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            else:
                crawl_generate(
                    candidate_id=candidate_id,
                    content_type=content_type,
                    output=result_path,
                    max_images=20,
                    retry_failed=bool(
                        retry_candidate_id
                        or (retry_prior_failures and candidate_id in previous_candidate_attempts)
                    ),
                    prepare_only=enqueue_only,
                )
                generated = json.loads(result_path.read_text(encoding="utf-8"))
                outcome = {
                    "status": generated.get("status", "unknown"),
                    "candidate_id": candidate_id,
                    "request_id": generated.get("request_id"),
                    "output": str(result_path.resolve()),
                    "quality_report": generated.get("quality_report", {}),
                }
        except Exception as exc:
            # crawl_generate writes deliberate terminal outcomes (notably
            # duplicate_skipped) before raising Typer's control-flow exception.
            # Preserve that outcome instead of counting it as a pipeline crash.
            saved_outcome = {}
            if result_path.exists():
                try:
                    saved_outcome = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    saved_outcome = {}
            if saved_outcome.get("status") in {
                "duplicate_skipped",
                "stale_candidate_skipped",
                "work_id_unresolved_skipped",
                "needs_crawler_update",
            }:
                outcome = {
                    **saved_outcome,
                    "candidate_id": candidate_id,
                    "output": str(result_path.resolve()),
                }
            else:
                outcome = {
                    "status": "candidate_pipeline_error",
                    "candidate_id": candidate_id,
                    "error": str(exc)[:500] or exc.__class__.__name__,
                    "output": str(result_path.resolve()),
                }
            if not result_path.exists():
                result_path.write_text(
                    json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        if retry_candidate_id:
            previous_attempt_count = int(
                previous_candidate_attempts[retry_candidate_id].get("attempt_count", 1)
            )
            outcome["attempt_count"] = previous_attempt_count + 1
            outcome["retry_count"] = int(
                previous_candidate_attempts[retry_candidate_id].get("retry_count", 0)
            ) + 1
            outcome["retry_note"] = retry_note.strip() if retry_note else ""
        elif (
            retry_prior_failures or retry_work_identity_failures
        ) and candidate_id in previous_candidate_attempts:
            previous = previous_candidate_attempts[candidate_id]
            previous_fingerprint = str(previous.get("candidate_fingerprint") or "")
            current_fingerprint = discovery_candidate_fingerprint(candidate)
            outcome["attempt_count"] = int(previous.get("attempt_count", 1)) + 1
            outcome["retry_count"] = int(previous.get("retry_count", 0)) + 1
            outcome["retry_note"] = (
                "candidate_updated_since_previous_attempt"
                if previous_fingerprint and previous_fingerprint != current_fingerprint
                else "re-evaluated_after_agent_improvements"
            )
        else:
            outcome["attempt_count"] = int(
                previous_candidate_attempts.get(candidate_id, {}).get("attempt_count", 0)
            ) + 1
            outcome["retry_count"] = int(
                previous_candidate_attempts.get(candidate_id, {}).get("retry_count", 0)
            )
        if content_type == "event" and item.source_id in {"recal_kr", "recal_music"}:
            outcome["expiry_revision"] = RECAL_EXPIRY_REVISION
        outcome = _enrich_candidate_outcome(outcome)
        outcomes.append(outcome)
        try:
            repository.persist_candidate_attempt(content_type, candidate, outcome)
        except Exception as exc:
            console.print(
                f"[yellow]후보 처리 이력을 DB에 저장하지 못했습니다: {str(exc)[:240]}[/yellow]"
            )
        if outcome.get("status") == "ready_for_feed":
            passed_count += 1
        outcome_text = str(outcome).lower()
        if any(
            marker in outcome_text
            for marker in (
                "credit_balance_exhausted",
                "insufficient_quota",
                "no credits remaining",
            )
        ):
            quota_exhausted = True
        console.print(
            f"[{index}/{len(fresh_items)}] {candidate.get('title', candidate_id)} "
            f"→ {outcome.get('status')}"
        )

    status_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    reason_code_counts: dict[str, int] = {}
    for outcome in outcomes:
        status = str(outcome.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        stage = str(outcome.get("failure_stage") or "completed")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        for code in set(outcome.get("reason_codes") or []):
            reason_code_counts[str(code)] = reason_code_counts.get(str(code), 0) + 1

    summary = {
        "content_type": content_type,
        "candidate_count": len(fresh_items),
        "candidates_discovered": len(collected_items),
        "previously_attempted_skipped": skipped_attempted,
        "work_concentration_deferred": diversity_deferred,
        "candidate_records_persisted": sum(item.persisted_count for item in runs),
        "birthday_reference_upserts": sum(
            item.birthday_reference_upserts for item in runs
        ),
        "work_titles_sampled": sampled_work_titles,
        "target_passed": target_passed,
        "passed_this_run": passed_count,
        "collection_errors": [error for item in runs for error in item.errors],
        "source_results": [asdict(result) for item in runs for result in item.source_results],
        "rejected_candidate_count": sum(item.rejected_count for item in runs),
        "stale_candidates_deferred_to_evidence": stale_candidates_seen,
        "funnel": {
            "discovered": len(collected_items),
            "deferred_for_work_diversity": diversity_deferred,
            "previously_attempted_skipped": skipped_attempted,
            "stale_deferred_to_evidence": stale_candidates_seen,
            "attempted": len(outcomes),
            "status_counts": status_counts,
            "failure_stage_counts": stage_counts,
            "reason_code_counts": reason_code_counts,
        },
        "source_discovery": run_source_discovery(AgentRepository(), validation_limit=3),
        "outcomes": outcomes,
    }
    summary_path = run_folder / "run_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    ready = sum(item.get("status") == "ready_for_feed" for item in outcomes)
    console.print(
        f"유형 {content_type}: 후보 {len(collected_items)}건 → 독립 검수 통과 {ready}건 · "
        f"실행 요약 {summary_path.resolve()}"
    )


    return summary


@app.command("validate")
def validate(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """콘텐츠 manifest를 LLM 비용 없이 검수합니다."""
    package = _load_package(path)
    issues = run_quality_checks(package)
    report = score_quality(package, issues)
    console.print(
        f"품질 점수: {report['total']}/100 · 카드 최저점: "
        f"{report['lowest_card_score']} · 판정: {report['decision']}"
    )
    console.print(f"점수 항목: {report['dimensions']}")
    if not issues:
        console.print("[bold green]통과[/bold green] 코드 가드레일에서 문제가 발견되지 않았습니다.")
        return
    table = Table(title=f"검수 문제 {len(issues)}건")
    table.add_column("코드")
    table.add_column("페이지")
    table.add_column("담당")
    table.add_column("내용")
    for issue in issues:
        table.add_row(issue.code, str(issue.page or "-"), issue.owner, issue.message)
    console.print(table)
    raise typer.Exit(code=1)


@app.command("generate-manifest")
def generate(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[
        Path,
        typer.Option("--output", "-o"),
    ] = Path("output/drafts/generated_manifest.json"),
    fixture: Annotated[
        Path | None,
        typer.Option(
            "--fixture",
            help="API 호출 대신 사용할 CopyDeck JSON. 오프라인 재현 테스트용입니다.",
        ),
    ] = None,
) -> None:
    """검증 사실·이미지 manifest에서 카드 카피와 레이아웃을 생성합니다.

    이 명령만 LLM을 호출합니다. 통과물은 내부 피드 대기열에 저장하며 SNS에 자동 게시하지 않습니다.
    """
    package = _load_package(path)
    offline_cards: list[dict] = []
    if fixture is not None:
        fixture_payload = json.loads(fixture.read_text(encoding="utf-8"))
        offline_cards = [
            card.model_dump(mode="json") for card in CopyDeck.model_validate(fixture_payload).cards
        ]
    result = build_graph().invoke(
        {
            "request_id": package.request_id,
            "topic": package.topic,
            "content_kind": package.content_kind.value,
            "package": package.model_dump(mode="json"),
            "trace": [],
            "revision_count": 0,
            "max_revision": get_settings().max_revision,
            "allow_llm": fixture is None,
            "offline_cards": offline_cards,
            "download_images": fixture is None,
            "render_required": fixture is None,
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    exported = _export_rendered_cards(
        result.get("package", {}), output.parent / "cards" / package.request_id
    )
    console.print(f"결과 저장: {output.resolve()}")
    if exported:
        console.print(f"카드 PNG 파일: {exported[0].parent.resolve()} ({len(exported)}장)")
    console.print(f"최종 상태: {result['status']}")
    console.print(f"품질 문제: {len(result.get('qa_report', []))}건")
    report = result.get("quality_report", {})
    if report:
        console.print(f"품질 점수: {report.get('total')}/100 · 판정: {report.get('decision')}")


@app.command("render-manifest")
def render_existing_manifest(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    cards_dir: Annotated[Path | None, typer.Option("--cards-dir")] = None,
) -> None:
    """기존 생성 manifest를 추가 LLM 호출 없이 카드 PNG로 렌더링합니다."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    package = ContentPackage.model_validate(payload["package"])
    repository = AgentRepository()
    started = perf_counter()
    png_by_page = render_package_cards(package, get_image_bytes=repository.get_image_bytes)
    package = repository.persist_rendered_cards(package, png_by_page)
    elapsed = round(perf_counter() - started, 3)
    repository.update_rendered_card_refs(package)

    payload["package"] = package.model_dump(mode="json")
    payload["rendered_card_count"] = len(png_by_page)
    payload["render_elapsed_seconds"] = elapsed
    payload["rendered_image_keys"] = {
        str(card.page): card.rendered_image_key for card in package.cards
    }
    output_path = output or path.with_name(f"{path.stem}_rendered{path.suffix}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    export_dir = cards_dir or path.parent / "cards" / package.request_id
    exported = _export_rendered_cards(package.model_dump(mode="json"), export_dir)
    console.print(f"추가 LLM 호출 없이 {len(exported)}장 PNG 생성 · {elapsed:.2f}초")
    console.print(f"PNG 폴더: {export_dir.resolve()}")
    console.print(f"렌더 manifest: {output_path.resolve()}")


@app.command("sources")
def sources() -> None:
    """등록된 출처와 자동화 상태를 확인합니다."""
    registry = load_source_registry()
    table = Table(title=f"수집 출처 v{registry.version}")
    table.add_column("출처")
    table.add_column("역할")
    table.add_column("수집")
    table.add_column("연결 상태")
    table.add_column("상태")
    table.add_column("지역")
    table.add_column("콘텐츠 유형")
    for source in registry.sources:
        if source.status == "trend_only" and source.role == "trend":
            connection = "주제 신호 전용"
        elif source.status != "active":
            connection = "자동 호출 보류"
        elif source.collection == "manual":
            connection = "수동 확인"
        elif source.collection == "youtube_rss" and source.feed_url is None:
            connection = "채널 피드 설정 필요"
        else:
            connection = "자동 어댑터 연결"
        table.add_row(
            source.name,
            source.role,
            source.collection,
            connection,
            source.status,
            source.region,
            ", ".join(CONTENT_TYPE_LABELS[item] for item in source.content_types) or "미분류",
        )
    console.print(table)


@app.command("source-discover")
def source_discover(
    validation_limit: Annotated[
        int,
        typer.Option(
            "--validation-limit",
            min=0,
            max=20,
            help="이번 실행에서 자동 검증할 신규 사이트 수",
        ),
    ] = 3,
) -> None:
    """기사 원문 캡처의 외부 링크에서 새 출처 사이트를 발굴·검증합니다."""
    result = run_source_discovery(AgentRepository(), validation_limit=validation_limit)
    console.print_json(json.dumps(result, ensure_ascii=False))


@app.command("source-candidates")
def source_candidates(
    status: Annotated[str | None, typer.Option("--status")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 100,
) -> None:
    """새 출처 후보의 검증 상태·통과 유형을 확인합니다."""
    rows = AgentRepository().list_source_candidates(status=status, limit=limit)
    table = Table(title=f"새 출처 후보 {len(rows)}건")
    table.add_column("ID")
    table.add_column("호스트")
    table.add_column("상태")
    table.add_column("유형")
    table.add_column("검증 시각")
    for item in rows:
        table.add_row(
            str(item.get("id", "")),
            str(item.get("host", "")),
            str(item.get("status", "")),
            ", ".join(item.get("content_types", [])),
            str(item.get("last_checked_at") or "-"),
        )
    console.print(table)


@app.command("discover")
def discover(
    work_title: Annotated[str | None, typer.Option("--work-title")] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="선택: DB 기록의 JSON 스냅샷도 내보냅니다."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=50)] = 20,
    region_policy: Annotated[
        str,
        typer.Option(
            "--region-policy",
            help="이전 명령과의 호환용이며 후보 정렬에는 영향을 주지 않습니다.",
        ),
    ] = "mixed",
    content_type: Annotated[
        str | None,
        typer.Option("--content-type", help="선택: 10개 유형 중 지정한 유형의 출처만 수집"),
    ] = None,
    capture_images: Annotated[
        bool,
        typer.Option(
            "--capture-images/--no-capture-images",
            help="유형 수집 시 원문 페이지 이미지도 보관",
        ),
    ] = True,
) -> None:
    """유형에 연결되고 자동 수집 승인된 RSS/API/HTML 출처에서 후보를 수집합니다."""
    if region_policy not in {"kr_first", "mixed", "jp_first"}:
        raise typer.BadParameter("region-policy는 kr_first, mixed, jp_first 중 하나여야 합니다.")
    supported_types = {item.value for item in CONTENT_TYPE_LABELS}
    if content_type is not None and content_type not in supported_types:
        raise typer.BadParameter(
            f"지원하지 않는 --content-type입니다: {content_type}. "
            f"가능한 값: {', '.join(item.value for item in CONTENT_TYPE_LABELS)}"
        )
    crawler_run = None
    if content_type is None:
        items, errors = collect_candidates(
            limit=limit,
            region_policy=region_policy,
            work_title=work_title,
            exhaustive_listing=False,
        )
        persisted = AgentRepository().persist_discovery(
            [item.model_dump(mode="json") for item in items],
            region_policy,
        )
    else:
        crawler_run = get_type_crawler(content_type).run(
            limit=limit,
            region_policy=region_policy,
            work_title=work_title,
            capture_images=capture_images,
        )
        items = crawler_run.items
        errors = crawler_run.errors
        persisted = crawler_run.persisted_count
    source_discovery = (
        run_source_discovery(AgentRepository(), validation_limit=3)
        if crawler_run is not None
        else None
    )
    runtime_sources = (
        {item.source_id: asdict(item) for item in crawler_run.source_results}
        if crawler_run is not None
        else {}
    )
    payload = {
        "items": [item.model_dump(mode="json") for item in items],
        "errors": errors,
        "notice": (
            "발견 후보이며 공식 발표 원문 검증 전에는 정보 콘텐츠로 사용할 수 없습니다. "
            "커뮤니티 자료는 주제 신호로만 적재합니다."
        ),
        "region_policy": region_policy,
        "content_type_filter": content_type,
        "source_run_results": list(runtime_sources.values()),
        "source_discovery": source_discovery,
        "rejected_candidate_count": crawler_run.rejected_count if crawler_run else 0,
        "image_capture": (
            {
                "pages_captured": crawler_run.captured_page_count,
                "image_candidates": crawler_run.image_candidate_count,
                "images_persisted": crawler_run.persisted_image_count,
            }
            if crawler_run
            else None
        ),
        "source_coverage": [
            {
                "source_id": source.id,
                "status": source.status,
                "collection": source.collection,
                "attempted": runtime_sources[source.id]["attempted"]
                if source.id in runtime_sources
                else (
                    (
                        source.status == "active"
                        or (source.role == "trend" and source.status == "trend_only")
                    )
                    and (source.role != "primary" or source.collection == "rss")
                    and source.collection != "manual"
                    and not (source.role == "reference" and not work_title)
                    and supports_discovery_adapter(source, work_title=work_title)
                ),
                "reason": runtime_sources[source.id]["status"]
                if source.id in runtime_sources
                else (
                    "active source adapter invoked"
                    if (
                        source.status == "active"
                        or (source.role == "trend" and source.status == "trend_only")
                    )
                    and (source.role != "primary" or source.collection == "rss")
                    and source.collection != "manual"
                    and not (source.role == "reference" and not work_title)
                    and supports_discovery_adapter(source, work_title=work_title)
                    else "not automatically polled: source gate, primary-page flow, or manual mode"
                ),
                "candidates": runtime_sources.get(source.id, {}).get("candidate_count", 0),
                "error": runtime_sources.get(source.id, {}).get("error", ""),
            }
            for source in load_source_registry().sources
            if content_type is None
            or any(item.value == content_type for item in source.content_types)
        ],
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"JSON 스냅샷 {len(items)}건 저장: {output.resolve()}")
    else:
        console.print("JSON 파일은 생략했습니다. 후보는 에이전트 DB에 기록했습니다.")
    console.print(f"정보·유머 후보 DB 적재: {persisted}건")
    if crawler_run is not None:
        attempted = sum(item.attempted for item in crawler_run.source_results)
        succeeded = sum(
            item.status.startswith("succeeded") for item in crawler_run.source_results
        )
        console.print(
            f"출처별 실행: {succeeded}/{attempted} 성공 · "
            f"유형 품질 게이트 반려: {crawler_run.rejected_count}건 · "
            f"원문 페이지 캡처: {crawler_run.captured_page_count}개 · "
            f"이미지 후보/적재: {crawler_run.image_candidate_count}/"
            f"{crawler_run.persisted_image_count}개"
        )
        for source_result in crawler_run.source_results:
            pages_scanned = source_result.get("pages_scanned", 0)
            if pages_scanned > 0:
                page_counts = source_result.get("page_candidate_counts", [])
                duplicates = source_result.get("duplicate_candidate_count", 0)
                console.print(
                    f"목록 페이지 {source_result['source_id']}: {pages_scanned}페이지 · "
                    f"페이지별 후보 {page_counts} · 페이지 중복 {duplicates}건 · "
                    f"종료 사유 {source_result.get('pagination_stop_reason', 'unknown')}"
                )
    if errors:
        console.print(f"수집 오류 {len(errors)}건: {' | '.join(errors)}")


@app.command("collect-page")
def collect_page(
    source_id: Annotated[str, typer.Option("--source", help="출처 레지스트리의 source id")],
    url: Annotated[str, typer.Option("--url", help="등록된 공식 출처 도메인의 페이지 URL")],
    output: Annotated[
        Path,
        typer.Option("--output", "-o"),
    ] = Path("data/collected/pages/latest.json"),
    max_images: Annotated[int, typer.Option("--max-images", min=0, max=100)] = 100,
    event_end: Annotated[
        str | None,
        typer.Option("--event-end", help="행사 종료일 YYYY-MM-DD (미지정 시 수집 후 30일 보관)"),
    ] = None,
) -> None:
    """공식 페이지와 이미지 후보를 수집해 공개 이미지 바이트를 로컬 DB에 보관합니다."""
    registry = load_source_registry()
    source = next((item for item in registry.sources if item.id == source_id), None)
    if source is None:
        raise typer.BadParameter(f"등록되지 않은 출처입니다: {source_id}")
    if source.status != "active":
        raise typer.BadParameter(
            f"{source_id} 출처는 자동 수집 승인 상태가 아닙니다 (status={source.status})."
        )
    if event_end:
        try:
            date.fromisoformat(event_end)
        except ValueError as exc:
            raise typer.BadParameter("--event-end는 YYYY-MM-DD 형식이어야 합니다.") from exc

    try:
        capture = collect_official_page(source, url)
    except (ValueError, httpx.HTTPError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    evidence = SourceEvidence(
        id=source.id,
        title=capture.title,
        url=capture.url,
        publisher=source.name,
        is_official=source.role == "primary",
        checked_at=capture.fetched_at,
        role=source.role,
        region=source.region,
        allowed_asset_hosts=source.allowed_asset_hosts,
    )
    package = ContentPackage(
        request_id=f"capture-{source.id}",
        topic=capture.title,
        content_kind=ContentKind.INFORMATION,
        audience_region=source.region,
        event_end=event_end,
        retention_expires_at=(
            (datetime.now(UTC) + timedelta(days=30)).isoformat() if event_end is None else None
        ),
        sources=[evidence],
    )
    package.menu_work_ids = resolve_menu_work_ids(package)
    asset_rows: list[dict[str, str]] = []
    for image_url in capture.image_urls[:max_images]:
        image = ImageAsset(
            id=f"{source.id}-{hashlib.sha1(str(image_url).encode('utf-8')).hexdigest()[:14]}",
            url=image_url,
            source_url=capture.image_source_pages.get(str(image_url), capture.url),
            official=source.role == "primary",
            rights_status=RightsStatus.NEEDS_REVIEW,
            provenance_source=evidence if source.role == "primary" else None,
            menu_work_ids=package.menu_work_ids,
            source_description=" ".join(
                part for part in (
                    capture.image_alt_text.get(str(image_url), "").strip(),
                    capture.image_source_titles.get(str(image_url), "").strip(),
                ) if part
            )[:240],
        )
        package.images.append(image)
        asset_rows.append(
            {
                "id": image.id,
                "url": str(image.url),
                "source_url": str(image.source_url),
                "alt_text": capture.image_alt_text.get(str(image.url), ""),
                "source_page_title": capture.image_source_titles.get(str(image.url), ""),
                "local_path": image.local_path or "",
                "download_status": image.download_status,
                "rights_status": image.rights_status.value,
                "error": image.download_error or "",
            }
        )

    payload = capture.model_dump(mode="json")
    payload["images"] = asset_rows
    payload["event_end"] = event_end
    repository = AgentRepository()
    capture_request_id = f"page:{hashlib.sha1(str(capture.url).encode('utf-8')).hexdigest()[:16]}"
    package.request_id = capture_request_id
    repository.persist_page_capture(payload, source.region, event_end)
    # The page capture table alone is not enough for cross-request reuse:
    # same-work lookup joins image assets to their persisted source row.
    repository.upsert_record(
        agent_type="information",
        record_id=f"source:{capture_request_id}:{source.id}",
        record_kind="source",
        package=package,
        payload=evidence.model_dump(mode="json"),
        status="collected",
    )
    image_result = image_curator(
        {
            "package": package.model_dump(mode="json"),
            "trace": [],
            "download_images": source.role in {"primary", "editorial", "reference"},
        }
    )
    package = ContentPackage.model_validate(image_result["package"])
    asset_rows = [
        {
            "id": image.id,
            "url": str(image.url or ""),
            "source_url": str(image.source_url),
            "alt_text": image.source_description,
            "local_path": image.local_path or "",
            "download_status": image.download_status,
            "rights_status": image.rights_status.value,
            "error": image.download_error or "",
        }
        for image in package.images
    ]
    payload["images"] = asset_rows
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"원문 캡처 저장: {output.resolve()}")
    downloaded = sum(image.download_status == "downloaded" for image in package.images)
    console.print(
        f"이미지 후보 {len(asset_rows)}개 중 다운로드·SQLite 보관 {downloaded}개 "
        "(공식 페이지 출처·출처 페이지·사용 위험 상태 기록)"
    )


@app.command("extract-page")
def extract_page(
    capture: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[
        Path,
        typer.Option("--output", "-o"),
    ] = Path("data/input/extracted_package.json"),
) -> None:
    """공식 페이지 캡처에서 원문 인용으로 확인된 사실만 추출합니다 (LLM 1회)."""
    try:
        package, usage = extract_page_capture(capture)
    except (ValueError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cost = estimate_gpt_6_luna_cost(
        int(usage["input_tokens"]),
        cached_input_tokens=0,
        cache_write_tokens=0,
        output_tokens=int(usage["output_tokens"]),
    )
    console.print(f"원문 근거 팩트 {len(package.facts)}건: {output.resolve()}")
    console.print(
        f"추출 사용량: 입력 {usage['input_tokens']} · 출력 {usage['output_tokens']} tokens · "
        f"예상 비용 ${cost:.6f} (GPT-6 Luna 요율 기준)"
    )
    console.print("다음 단계: me-nu crawl-ingest " + str(output))


@app.command("db-status")
def db_status() -> None:
    """수집·생성·피드·탈락 보관 데이터를 포함한 DB 저장량을 표시합니다."""
    repository = AgentRepository()
    table = Table(title=f"me;nu 에이전트 DB · {repository.path}")
    table.add_column("구분")
    table.add_column("레코드 수", justify="right")
    for name, count in repository.counts().items():
        table.add_row(name, str(count))
    console.print(table)


@app.command("db-cleanup")
def db_cleanup() -> None:
    """노출 기간이 끝나 보관 유예기간도 지난 콘텐츠와 수집 자료를 정리합니다."""
    removed = AgentRepository().cleanup_expired()
    console.print(f"만료 콘텐츠/카드/수집 레코드 {removed}건 정리 완료")


@app.command("pipeline-worker")
def pipeline_worker(
    stage: Annotated[str, typer.Option("--stage", help="crawl, generate, review")],
    lane: Annotated[str, typer.Option("--lane")] = "time_sensitive",
    content_types: Annotated[str, typer.Option("--content-types")] = "news,event,goods",
    scan_interval_seconds: Annotated[int, typer.Option("--scan-interval-seconds", min=10)] = 300,
    batch_limit: Annotated[int, typer.Option("--batch-limit", min=1, max=200)] = 200,
    idle_sleep_seconds: Annotated[float, typer.Option("--idle-sleep-seconds", min=0.2)] = 3,
    stop_file: Annotated[Path | None, typer.Option("--stop-file")] = None,
) -> None:
    """Run exactly one independently supervised pipeline worker."""
    from ani_menu_agent.pipelines.common.jobs import PipelineStage
    from ani_menu_agent.pipelines.workers.runtime import run_crawler_worker, run_queue_worker

    try:
        worker_stage = PipelineStage(stage)
    except ValueError as exc:
        raise typer.BadParameter("--stage must be crawl, generate, or review") from exc
    stopping = threading.Event()
    def should_stop() -> bool:
        return stopping.is_set() or bool(stop_file and stop_file.exists())

    def request_stop(_signum, _frame):
        stopping.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, request_stop)
    console.print(f"Starting independent {worker_stage.value} worker in lane {lane}.")
    if worker_stage == PipelineStage.CRAWL:
        selected_types = tuple(
            value.strip() for value in content_types.split(",") if value.strip()
        )
        if not selected_types:
            raise typer.BadParameter("--content-types must include at least one type")
        run_crawler_worker(
            content_types=selected_types,
            scan_interval_seconds=scan_interval_seconds,
            batch_limit=batch_limit,
            stop_requested=should_stop,
        )
    else:
        run_queue_worker(
            stage=worker_stage,
            lane=lane,
            idle_sleep_seconds=idle_sleep_seconds,
            stop_requested=should_stop,
        )
    console.print(f"Stopped {worker_stage.value} worker.")


@app.command("export-markdown")
def export_markdown(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o")],
) -> None:
    """구조화 manifest를 사람이 검토하기 쉬운 Markdown으로 내보냅니다."""
    package = _load_package(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(package), encoding="utf-8")
    console.print(f"Markdown saved: {output.resolve()}")


if __name__ == "__main__":
    app()
