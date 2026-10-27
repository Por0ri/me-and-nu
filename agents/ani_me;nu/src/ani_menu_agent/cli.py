"""개발 환경 점검과 구조화 초안 검수 CLI."""

import hashlib
import importlib.metadata
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from ani_menu_agent.agents.content_generation.renderer import render_package_cards
from ani_menu_agent.agents.humor import research_humor
from ani_menu_agent.agents.image import image_curator
from ani_menu_agent.agents.information import research_information
from ani_menu_agent.config import get_settings
from ani_menu_agent.discovery import collect_candidates
from ani_menu_agent.extraction import extract_page_capture, extract_page_payload
from ani_menu_agent.generation import estimate_gpt_6_luna_cost
from ani_menu_agent.graph import build_graph
from ani_menu_agent.markdown_export import render_markdown
from ani_menu_agent.models import (
    ContentKind,
    ContentPackage,
    CopyDeck,
    ImageAsset,
    RightsStatus,
    SourceEvidence,
)
from ani_menu_agent.quality import run_quality_checks, score_quality
from ani_menu_agent.sources import load_source_registry
from ani_menu_agent.sources.page_collector import collect_official_page
from ani_menu_agent.storage import AgentRepository

app = typer.Typer(no_args_is_help=True)
console = Console()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PATH = PROJECT_ROOT / "examples" / "sample_content.json"


def _load_package(path: Path) -> ContentPackage:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ContentPackage.model_validate(payload)


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
) -> tuple[ContentPackage, dict]:
    """Load crawler records from PostgreSQL and run the existing generation graph."""
    package = AgentRepository().load_content_package(request_id, kind.value)
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
            "package": package.model_dump(mode="json"),
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
    if package.content_kind is ContentKind.INFORMATION and not any(
        source.is_official and source.role == "primary" for source in package.sources
    ):
        raise typer.BadParameter("정보성 자료는 공식 1차 출처가 최소 하나 필요합니다.")

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
        _package, result = _run_generation_from_db(request_id, kind, fixture)
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
    source_id: Annotated[str, typer.Option("--source", help="활성 공식 1차 출처 ID")],
    url: Annotated[str, typer.Option("--url", help="해당 출처 도메인의 공식 상세 페이지")],
    output: Annotated[
        Path,
        typer.Option("--output", "-o"),
    ] = Path("output/drafts/crawl_generated.json"),
    max_images: Annotated[int, typer.Option("--max-images", min=1, max=20)] = 8,
    retry_failed: Annotated[
        bool,
        typer.Option(
            "--retry-failed",
            help="같은 request_id가 미통과 상태일 때만 기존 실패 건을 다시 생성합니다.",
        ),
    ] = False,
) -> None:
    """공식 페이지 수집부터 DB 적재, 카드 생성·검수까지 한 번에 실행합니다."""
    registry = load_source_registry()
    source = next((item for item in registry.sources if item.id == source_id), None)
    if source is None:
        raise typer.BadParameter(f"등록되지 않은 출처입니다: {source_id}")
    if source.status != "active" or source.role != "primary" or source.collection != "html":
        raise typer.BadParameter("활성화된 공식 1차 HTML 출처만 이 실행 경로에 사용할 수 있습니다.")

    run_started = perf_counter()
    stage_timings: dict[str, float] = {}
    console.print("1/5 공식 페이지 수집 중…")
    stage_started = perf_counter()
    try:
        # A generated story must not borrow images from unrelated articles on
        # the same site. Related-page expansion remains available for asset
        # inventory, but content generation stays scoped to its source article.
        capture = collect_official_page(source, url, expand_related_pages=False)
        stage_timings["page_collection"] = round(perf_counter() - stage_started, 3)
        payload = capture.model_dump(mode="json")
        image_rows: list[dict[str, str]] = []
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
                    "local_path": "",
                    "download_status": "pending",
                    "rights_status": RightsStatus.NEEDS_REVIEW.value,
                    "error": "",
                }
            )
        payload["images"] = image_rows
        repository = AgentRepository()
        repository.persist_page_capture(payload, source.region)
        console.print("2/5 근거 문장 추출 중…")
        stage_started = perf_counter()
        package, extraction_usage = extract_page_payload(payload)
        stage_timings["fact_extraction"] = round(perf_counter() - stage_started, 3)
    except (ValueError, KeyError, httpx.HTTPError, RuntimeError) as exc:
        raise typer.BadParameter(f"크롤링·팩트 추출 단계에서 중단: {exc}") from exc

    duplicate = repository.find_duplicate_content(package, retry_same_request=retry_failed)
    if duplicate:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "status": "duplicate_skipped",
                    "duplicate_of": duplicate,
                    "request_id": package.request_id,
                    "topic": package.topic,
                    "source_urls": [str(source.url) for source in package.sources],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise typer.BadParameter(
            "기존 콘텐츠와 작품·출처 또는 핵심 사실이 중복되어 카드 생성을 건너뜁니다. "
            f"기존 request_id={duplicate['request_id']} ({duplicate['reason']})"
        )

    console.print("3/5 자료 저장 및 수집 메타데이터 정리 중…")
    stage_started = perf_counter()
    payload["event_end"] = package.event_end
    repository.persist_page_capture(payload, source.region, package.event_end)
    repository.prepare_lifecycle(package)
    research_information({"package": package.model_dump(mode="json"), "trace": []})
    stage_timings["database_and_research"] = round(perf_counter() - stage_started, 3)
    console.print(f"4/5 이미지 권리 확인 및 다운로드 중… (후보 {len(package.images)}개)")
    stage_started = perf_counter()
    image_result = image_curator(
        {
            "package": package.model_dump(mode="json"),
            "trace": [],
            "download_images": True,
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
    # Sparse imagery is not by itself a reason to reject useful, fact-backed content.
    # The graph uses each source image at most once and fills remaining cards with
    # original typographic/editorial graphics rather than repeating copyrighted art.

    console.print("5/5 카드 생성 및 독립 검수 중…")
    try:
        _stored_package, result = _run_generation_from_db(
            package.request_id, ContentKind.INFORMATION
        )
    except (ValueError, RuntimeError) as exc:
        raise typer.BadParameter(f"DB 기반 콘텐츠 생성 단계에서 중단: {exc}") from exc

    result["usage_records"] = [extraction_usage, *result.get("usage_records", [])]
    stage_timings.update(result.get("stage_timings_seconds", {}))
    stage_timings["total"] = round(perf_counter() - run_started, 3)
    result["stage_timings_seconds"] = stage_timings
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
        )
    console.print(table)


@app.command("discover")
def discover(
    output: Annotated[
        Path,
        typer.Option("--output", "-o"),
    ] = Path("data/discovered/candidates.json"),
    limit: Annotated[int, typer.Option("--limit", min=1, max=50)] = 20,
    region_policy: Annotated[
        str,
        typer.Option("--region-policy", help="kr_first, mixed, jp_first"),
    ] = "kr_first",
) -> None:
    """활성 RSS/API/HTML 및 trend-only 출처의 후보·주제 신호를 수집합니다."""
    if region_policy not in {"kr_first", "mixed", "jp_first"}:
        raise typer.BadParameter("region-policy는 kr_first, mixed, jp_first 중 하나여야 합니다.")
    items, errors = collect_candidates(limit=limit, region_policy=region_policy)
    persisted = AgentRepository().persist_discovery(
        [item.model_dump(mode="json") for item in items],
        region_policy,
    )
    payload = {
        "items": [item.model_dump(mode="json") for item in items],
        "errors": errors,
        "notice": (
            "발견 후보이며 공식 발표 원문 검증 전에는 정보 콘텐츠로 사용할 수 없습니다. "
            "커뮤니티 자료는 주제 신호로만 적재합니다."
        ),
        "region_policy": region_policy,
        "source_coverage": [
            {
                "source_id": source.id,
                "status": source.status,
                "collection": source.collection,
                "attempted": (
                    (
                        source.status == "active"
                        or (source.role == "trend" and source.status == "trend_only")
                    )
                    and (source.role != "primary" or source.collection == "rss")
                    and source.collection != "manual"
                ),
                "reason": (
                    "active source adapter invoked"
                    if (
                        source.status == "active"
                        or (source.role == "trend" and source.status == "trend_only")
                    )
                    and (source.role != "primary" or source.collection == "rss")
                    and source.collection != "manual"
                    else "not automatically polled: source gate, primary-page flow, or manual mode"
                ),
            }
            for source in load_source_registry().sources
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"후보 {len(items)}건 저장: {output.resolve()}")
    console.print(f"정보·유머 후보 DB 적재: {persisted}건")
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
    max_images: Annotated[int, typer.Option("--max-images", min=0, max=20)] = 8,
    event_end: Annotated[
        str | None,
        typer.Option("--event-end", help="행사 종료일 YYYY-MM-DD (미지정 시 수집 후 30일 보관)"),
    ] = None,
) -> None:
    """공식 페이지와 이미지 URL 후보만 저장합니다. 권리 확인 전에는 이미지를 받지 않습니다."""
    registry = load_source_registry()
    source = next((item for item in registry.sources if item.id == source_id), None)
    if source is None:
        raise typer.BadParameter(f"등록되지 않은 출처입니다: {source_id}")
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
        url=source.url,
        publisher=source.name,
        is_official=True,
        checked_at=capture.fetched_at,
        role="primary",
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
    asset_rows: list[dict[str, str]] = []
    for image_url in capture.image_urls[:max_images]:
        image = ImageAsset(
            id=f"{source.id}-{hashlib.sha1(str(image_url).encode('utf-8')).hexdigest()[:14]}",
            url=image_url,
            source_url=capture.image_source_pages.get(str(image_url), capture.url),
            official=True,
            rights_status=RightsStatus.NEEDS_REVIEW,
        )
        package.images.append(image)
        asset_rows.append(
            {
                "id": image.id,
                "url": str(image.url),
                "alt_text": capture.image_alt_text.get(str(image.url), ""),
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
    repository.persist_images(package)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"원문 캡처 저장: {output.resolve()}")
    console.print(f"이미지 URL 후보 {len(asset_rows)}건 저장 (권리 확인 전 다운로드 안 함)")


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
    """세 에이전트의 저장량과 피드·탈락 보관량을 표시합니다."""
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
