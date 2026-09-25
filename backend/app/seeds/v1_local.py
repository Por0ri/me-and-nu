"""Idempotent local policies and public content for the V1 API walkthrough.

Run after Alembic upgrade with ``python -m app.seeds.v1_local``. This content is
an explicitly labelled development sample, not an article from a real publisher.
"""

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import (
    Content,
    ContentSource,
    ContentTag,
    Policy,
    SourceSite,
    Subtopic,
    Topic,
)
from app.seeds.movie_catalog import MovieCatalogSeedResult, seed_movie_catalog


LOCAL_POLICIES = (
    {
        "policy_type": "terms",
        "policy_version": "v1",
        "title": "서비스 이용약관 (로컬 테스트용)",
        "content": "로컬 개발 환경에서 API 흐름을 검증하기 위한 임시 이용약관입니다. 실제 서비스 약관이 아닙니다.",
        "is_required": True,
    },
    {
        "policy_type": "privacy",
        "policy_version": "v1",
        "title": "개인정보 처리방침 (로컬 테스트용)",
        "content": "로컬 개발 환경에서 동의 저장을 검증하기 위한 임시 개인정보 안내입니다. 실제 서비스 방침이 아닙니다.",
        "is_required": True,
    },
    {
        "policy_type": "advertising",
        "policy_version": "v1",
        "title": "광고성 정보 수신 동의 (로컬 테스트용)",
        "content": "로컬 개발 환경에서 선택 동의를 검증하기 위한 임시 안내입니다. 실제 광고 수신 동의 문안이 아닙니다.",
        "is_required": False,
    },
    {
        "policy_type": "marketing",
        "policy_version": "v1",
        "title": "마케팅 정보 수신 동의 (로컬 테스트용)",
        "content": "로컬 개발 환경에서 선택 동의를 검증하기 위한 임시 안내입니다. 실제 마케팅 수신 동의 문안이 아닙니다.",
        "is_required": False,
    },
)

LOCAL_SOURCE_NAME = "MeNu 로컬 테스트"
LOCAL_SOURCE_URL = "https://example.com/menu-v1-movie-sample"
LOCAL_CONTENT_TITLE = "영화 피드 기능 확인용 로컬 샘플"
LOCAL_CONTENT_PUBLISHED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class V1LocalSeedResult:
    movie_catalog: MovieCatalogSeedResult
    created_policies: int
    created_source_sites: int
    created_contents: int
    created_content_sources: int
    created_content_tags: int
    content_id: int
    topic_id: int
    subtopic_id: int


async def seed_local_policies(session: AsyncSession) -> int:
    """Create only missing versions; never overwrite policy text entered by users."""
    created = 0
    for values in LOCAL_POLICIES:
        existing = await session.scalar(
            select(Policy.policy_id).where(
                Policy.policy_type == values["policy_type"],
                Policy.policy_version == values["policy_version"],
            )
        )
        if existing is None:
            session.add(Policy(**values))
            await session.flush()
            created += 1
    return created


async def seed_v1_local(session: AsyncSession) -> V1LocalSeedResult:
    """Create catalog, policies, and one public movie content in one transaction."""
    movie_catalog = await seed_movie_catalog(session)
    created_policies = await seed_local_policies(session)

    topic = await session.scalar(select(Topic).where(Topic.topic_code == "movie"))
    if topic is None:
        raise RuntimeError("영화 Topic 시드에 실패했습니다.")
    subtopic = await session.scalar(
        select(Subtopic).where(
            Subtopic.topic_id == topic.topic_id,
            Subtopic.parent_subtopic_id.is_(None),
            Subtopic.subtopic_name == "영화 정보",
        )
    )
    if subtopic is None or subtopic.topic_id != topic.topic_id:
        raise RuntimeError("영화 Subtopic 시드 또는 Topic 연결이 잘못되었습니다.")

    created_source_sites = 0
    source_site = await session.scalar(
        select(SourceSite).where(
            SourceSite.topic_id == topic.topic_id,
            SourceSite.media_name == LOCAL_SOURCE_NAME,
        )
    )
    if source_site is None:
        source_site = SourceSite(
            topic_id=topic.topic_id,
            media_name=LOCAL_SOURCE_NAME,
            collect_method="manual",
            site_url="https://example.com",
            source_grade="없음",
            copyright_status="unknown",
            is_collecting=False,
        )
        session.add(source_site)
        await session.flush()
        created_source_sites = 1

    # The source URL serves as a stable local seed marker. A pre-existing marker
    # must still point to the intended movie Topic before we reuse the content.
    content = await session.scalar(
        select(Content)
        .join(ContentSource, Content.content_id == ContentSource.content_id)
        .where(ContentSource.source_url == LOCAL_SOURCE_URL)
    )
    created_contents = 0
    if content is None:
        content = Content(
            topic_id=topic.topic_id,
            title=LOCAL_CONTENT_TITLE,
            summary="로컬 V1 API에서 피드와 사용자 반응을 확인하는 샘플 콘텐츠입니다.",
            body="이 콘텐츠는 로컬 개발 검증용 샘플이며 실제 기사나 영화 정보가 아닙니다.",
            production_type="human",
            judgment_status="confirmed",
            status="active",
            content_type="article",
            published_at=LOCAL_CONTENT_PUBLISHED_AT,
            is_sponsored=False,
        )
        session.add(content)
        await session.flush()
        created_contents = 1
    if content.topic_id != topic.topic_id:
        raise RuntimeError("로컬 콘텐츠의 Topic 연결이 잘못되었습니다.")

    created_content_sources = 0
    content_source = await session.scalar(
        select(ContentSource.content_source_id).where(
            ContentSource.content_id == content.content_id,
            ContentSource.source_url == LOCAL_SOURCE_URL,
        )
    )
    if content_source is None:
        session.add(
            ContentSource(
                content_id=content.content_id,
                source_site_id=source_site.source_site_id,
                source_url=LOCAL_SOURCE_URL,
                source_title="MeNu 로컬 테스트 샘플",
                source_role="primary",
                citation_order=1,
            )
        )
        await session.flush()
        created_content_sources = 1

    created_content_tags = 0
    content_tag = await session.scalar(
        select(ContentTag.content_tag_id).where(
            ContentTag.content_id == content.content_id,
            ContentTag.subtopic_id == subtopic.subtopic_id,
        )
    )
    if content_tag is None:
        session.add(
            ContentTag(
                content_id=content.content_id,
                topic_id=topic.topic_id,
                subtopic_id=subtopic.subtopic_id,
            )
        )
        await session.flush()
        created_content_tags = 1

    return V1LocalSeedResult(
        movie_catalog=movie_catalog,
        created_policies=created_policies,
        created_source_sites=created_source_sites,
        created_contents=created_contents,
        created_content_sources=created_content_sources,
        created_content_tags=created_content_tags,
        content_id=content.content_id,
        topic_id=topic.topic_id,
        subtopic_id=subtopic.subtopic_id,
    )


async def run_v1_local_seed() -> V1LocalSeedResult:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            return await seed_v1_local(session)


def main() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            result = runner.run(run_v1_local_seed())
    else:
        result = asyncio.run(run_v1_local_seed())

    print(
        "V1 로컬 시드 완료: "
        f"policies={result.created_policies}, "
        f"topic={result.topic_id}, "
        f"subtopic={result.subtopic_id}, "
        f"content={result.content_id}, "
        f"new_contents={result.created_contents}"
    )


if __name__ == "__main__":
    main()
