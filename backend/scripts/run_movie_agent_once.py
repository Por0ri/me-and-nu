import argparse
import asyncio
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent

# 개발 단계의 형제 폴더 구조에서 backend의 app과 agent 패키지를 함께 찾습니다.
for path in (str(BACKEND_ROOT), str(PROJECT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.integrations.movie_review_agent import MovieReviewAgentAdapter
from app.models import SourceSite, Topic
from app.services.movie_agent_runner import run_movie_agent_and_persist


async def _movie_catalog_context(db):
    topic_id = await db.scalar(
        select(Topic.topic_id).where(Topic.topic_code == "movie")
    )
    if topic_id is None:
        raise RuntimeError(
            "영화 Topic이 없습니다. 먼저 python -m app.seeds.movie_catalog를 실행하세요."
        )

    result = await db.execute(
        select(SourceSite.media_name, SourceSite.source_site_id).where(
            SourceSite.topic_id == topic_id,
            SourceSite.media_name.in_(("TMDB", "한국어 위키백과")),
        )
    )
    source_site_ids = {name: site_id for name, site_id in result.all()}
    return topic_id, source_site_ids


async def run_once(title: str, year: int) -> None:
    adapter = MovieReviewAgentAdapter.load_default()
    material = await adapter.fetch_material(title, year)
    if material is None:
        raise RuntimeError(f"TMDB에서 영화를 찾지 못했습니다: {title} ({year})")

    async with AsyncSessionLocal() as db:
        topic_id, source_site_ids = await _movie_catalog_context(db)
        persisted = await run_movie_agent_and_persist(
            db,
            topic_id=topic_id,
            material=material,
            producer=adapter.produce,
            source_site_ids=source_site_ids,
            agent_version=adapter.version,
        )

    draft_id = (
        persisted.draft.draft_id if persisted.draft is not None else None
    )
    print(
        "영화 에이전트 DB 저장 완료: "
        f"agent_run_id={persisted.agent_run.agent_run_id}, "
        f"status={persisted.agent_run.status}, "
        f"outcome={persisted.agent_run.outcome}, "
        f"draft_id={draft_id}, "
        f"steps={persisted.step_count}, "
        f"sources={persisted.source_count}, "
        f"judgments={persisted.judgment_count}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="영화 리뷰 에이전트를 한 번 실행하고 DB에 저장합니다."
    )
    parser.add_argument("title", help="영화 제목")
    parser.add_argument("year", type=int, help="개봉 연도")
    args = parser.parse_args()

    if sys.platform == "win32":
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop
        ) as runner:
            runner.run(run_once(args.title, args.year))
    else:
        asyncio.run(run_once(args.title, args.year))


if __name__ == "__main__":
    main()
