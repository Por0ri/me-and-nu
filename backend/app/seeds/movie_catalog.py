import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import SourceSite, Subtopic, Topic


MOVIE_TOPIC = {
    "topic_code": "movie",
    "topic_name": "영화",
    "description": "영화 정보 및 리뷰 콘텐츠 도메인",
    "sort_order": 1,
    "is_active": True,
}

MOVIE_SUBTOPICS = (
    {
        "subtopic_name": "영화 정보",
        "depth_level": 1,
    },
    {
        "subtopic_name": "영화 리뷰",
        "depth_level": 1,
    },
)

MOVIE_SOURCE_SITES = (
    {
        "media_name": "TMDB",
        "collect_method": "api",
        "site_url": "https://www.themoviedb.org",
        "source_grade": "없음",
        "copyright_status": "unknown",
        "is_collecting": True,
    },
    {
        "media_name": "한국어 위키백과",
        "collect_method": "api",
        "site_url": "https://ko.wikipedia.org",
        "source_grade": "없음",
        "copyright_status": "unknown",
        "is_collecting": True,
    },
)


@dataclass(frozen=True)
class MovieCatalogSeedResult:
    created_topics: int = 0
    created_subtopics: int = 0
    created_source_sites: int = 0

    @property
    def created_total(self) -> int:
        return (
            self.created_topics
            + self.created_subtopics
            + self.created_source_sites
        )


async def seed_movie_catalog(
    session: AsyncSession,
) -> MovieCatalogSeedResult:
    """영화 에이전트가 참조할 최소 기준 데이터를 중복 없이 추가합니다."""
    created_topics = 0
    created_subtopics = 0
    created_source_sites = 0

    topic = await session.scalar(
        select(Topic).where(
            Topic.topic_code == MOVIE_TOPIC["topic_code"]
        )
    )
    if topic is None:
        topic = Topic(**MOVIE_TOPIC)
        session.add(topic)
        await session.flush()
        created_topics = 1

    for values in MOVIE_SUBTOPICS:
        subtopic = await session.scalar(
            select(Subtopic).where(
                Subtopic.topic_id == topic.topic_id,
                Subtopic.parent_subtopic_id.is_(None),
                Subtopic.subtopic_name == values["subtopic_name"],
            )
        )
        if subtopic is None:
            session.add(
                Subtopic(
                    topic_id=topic.topic_id,
                    parent_subtopic_id=None,
                    **values,
                )
            )
            await session.flush()
            created_subtopics += 1

    for values in MOVIE_SOURCE_SITES:
        source_site = await session.scalar(
            select(SourceSite).where(
                SourceSite.topic_id == topic.topic_id,
                SourceSite.media_name == values["media_name"],
            )
        )
        if source_site is None:
            session.add(
                SourceSite(
                    topic_id=topic.topic_id,
                    **values,
                )
            )
            await session.flush()
            created_source_sites += 1

    return MovieCatalogSeedResult(
        created_topics=created_topics,
        created_subtopics=created_subtopics,
        created_source_sites=created_source_sites,
    )


async def run_movie_catalog_seed() -> MovieCatalogSeedResult:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            return await seed_movie_catalog(session)


def main() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop
        ) as runner:
            result = runner.run(run_movie_catalog_seed())
    else:
        result = asyncio.run(run_movie_catalog_seed())

    print(
        "영화 기준 데이터 시드 완료: "
        f"topic={result.created_topics}, "
        f"subtopic={result.created_subtopics}, "
        f"source_site={result.created_source_sites}, "
        f"total={result.created_total}"
    )


if __name__ == "__main__":
    main()
