"""Start exactly one independent RAG worker against an existing local database."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

REQUIRED_WORKER_TABLES = frozenset({
    "rag_card_image", "rag_card_image_component", "rag_chunk", "rag_claim", "rag_claim_evidence",
    "rag_content_card", "rag_content_draft", "rag_content_entity_link",
    "rag_content_source", "rag_content_tag", "rag_document",
    "rag_document_content_type", "rag_document_entity_link", "rag_draft_evidence",
    "rag_entity", "rag_entity_fact", "rag_entity_relation", "rag_entity_work_link",
    "rag_excerpt", "rag_generation_work", "rag_image_asset", "rag_image_entity_link",
    "rag_image_work_link", "rag_job_event", "rag_judgment_log", "rag_queue_job",
    "rag_rendered_card", "rag_retrieval", "rag_retrieval_hit", "rag_review_history",
    "rag_source", "rag_source_read_ledger", "rag_web_candidate_page_cache",
    "rag_web_candidate_url_ledger", "rag_web_discovery_plan", "rag_work_catalog",
    "rag_work_link",
})


def _assert_existing_schema(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"RAG database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=20)
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = REQUIRED_WORKER_TABLES - tables
        if missing:
            raise RuntimeError(
                "Existing RAG schema is incomplete for workers; apply the reviewed schema separately. "
                f"Missing tables: {', '.join(sorted(missing))}"
            )
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=("crawler", "generation", "review"))
    parser.add_argument("--db", required=True, type=Path, help="Existing ani_rag SQLite database")
    parser.add_argument("--vector-db", type=Path, help="Existing read-only vector sidecar (crawler/generation)")
    parser.add_argument(
        "--image-dir", type=Path,
        default=PROJECT_ROOT / "data" / "image_assets",
        help="Local image asset directory",
    )
    parser.add_argument(
        "--plan-config", type=Path,
        default=PROJECT_ROOT / "config" / "automatic_plans.json",
        help="RAG-only recurring collection and web-discovery plans",
    )
    parser.add_argument(
        "--schedule-state", type=Path,
        default=PROJECT_ROOT / "data" / "plans" / "automatic_schedule_state_rotation7d.json",
        help="Persistent start checkpoint for staggered recurring plans",
    )
    parser.add_argument("--stop-file", type=Path, help="Exit cleanly when this file is created")
    return parser


async def _run(args: argparse.Namespace) -> None:
    from ani_rag.agents.content_generation.agent import ContentGenerationAgent
    from ani_rag.agents.mcp_crawler.agent import MCPCrawlerAgent
    from ani_rag.agents.reviewer.agent import IndependentReviewAgent
    from ani_rag.automatic_plans import load_automatic_plans
    from ani_rag.environment import load_project_environment
    from ani_rag.firecrawl_adapter import FirecrawlMCPAdapter
    from ani_rag.model_services import OpenAIResponsesStructuredModel
    from ani_rag.retrieval import OpenAIQueryEmbeddingModel
    from ani_rag.wikidata_adapter import WikidataStructuredMCPAdapter
    from ani_rag.runtime import (
        compose_generation_review_runtime,
        compose_reviewer_agent,
        compose_runtime,
        open_vector_index_readonly,
    )
    from ani_rag.settings import load_source_registry

    database = args.db.resolve()
    _assert_existing_schema(database)
    needs_vectors = args.role in {"crawler", "generation"}
    vector_path = args.vector_db.resolve() if args.vector_db else None
    if needs_vectors and (vector_path is None or not vector_path.is_file()):
        raise FileNotFoundError("--vector-db must name the existing completed RAG vector sidecar")

    load_project_environment()
    model = OpenAIResponsesStructuredModel.from_project_environment()
    vector = open_vector_index_readonly(vector_path) if needs_vectors else None
    embeddings = None
    if needs_vectors:
        from openai import OpenAI
        import os

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing from the process environment and project .env")
        embeddings = OpenAIQueryEmbeddingModel(OpenAI(api_key=api_key))

    connection = sqlite3.connect(database, timeout=60, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    adapter = FirecrawlMCPAdapter()
    structured_adapter = WikidataStructuredMCPAdapter()
    registry = load_source_registry()
    if args.role == "crawler":
        recurring_plans = load_automatic_plans(
            registry, config_path=args.plan_config.resolve(),
            state_path=args.schedule_state.resolve(),
        )
        runtime = compose_runtime(
            connection,
            registry=registry,
            url_mcp=adapter,
            web_discovery_adapter=adapter,
            image_search_adapter=adapter,
            structured_mcp=structured_adapter,
            generation_model=model,
            review_model=model,
            work_match_model=model,
            vector_index=vector,
            query_embeddings=embeddings,
            image_asset_dir=args.image_dir.resolve(),
            recurring_plans=recurring_plans,
        )
        agent = runtime.crawler_agent
        print(f"Loaded {len(recurring_plans)} recurring RAG plans.", flush=True)
    elif args.role == "generation":
        runtime = compose_generation_review_runtime(
            connection,
            generation_model=model,
            review_model=model,
            vector_index=vector,
            query_embeddings=embeddings,
            image_search_adapter=adapter,
            image_asset_dir=args.image_dir.resolve(),
        )
        agent = ContentGenerationAgent(runtime.generation_worker, runtime.queue_reconciler)
    else:
        agent = compose_reviewer_agent(connection, review_model=model)

    stop = asyncio.Event()
    async def watch_stop_file() -> None:
        if args.stop_file is None:
            return
        marker = args.stop_file.resolve()
        while not stop.is_set():
            if marker.exists():
                stop.set()
                return
            await asyncio.sleep(1)

    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    print(f"Starting {args.role} worker; stop with Ctrl+C.", flush=True)
    stop_watcher = asyncio.create_task(watch_stop_file())
    try:
        await agent.run_forever(stop_event=stop)
    finally:
        stop_watcher.cancel()
        await asyncio.gather(stop_watcher, return_exceptions=True)
        connection.close()
        if vector is not None:
            vector.close()


def main() -> int:
    args = _parser().parse_args()
    if args.role == "crawler":
        logging.basicConfig(format="%(asctime)s %(message)s")
        logging.getLogger("ani_rag.web_discovery.pressure").setLevel(logging.INFO)
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
