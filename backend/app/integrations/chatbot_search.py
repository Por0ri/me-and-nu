"""소비자 챗봇 근거 찾기 도구(agent/chatbot에 넘긴다): 지금 토픽의 공개 콘텐츠 안에서만 찾는다.

토픽 값은 코드가 넘긴다. 에이전트가 공격을 받아도 다른 토픽 글은 나오지 않는다.
임베딩이 채워지면 이 자리를 벡터 검색으로 바꾼다. 지금은 낱말 겹침으로 고른다.
"""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import content_repository as content_repo

POOL_SIZE = 200
EXCERPT_CHARS = 600  # 발췌 하나(약 300토큰)
SEARCH_CANDIDATES = 8


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[0-9A-Za-z가-힣]{2,}", text.lower()))


def score(question_tokens: set[str], content) -> int:
    haystack = f"{content.title} {content.summary or ''} {(content.body or '')[:2000]}".lower()
    return sum(1 for token in question_tokens if token in haystack)


def excerpt(content, question_tokens: set[str], size: int = EXCERPT_CHARS) -> str:
    """질문 낱말이 처음 나오는 곳 둘레를 잘라 온다. 없으면 요약 · 본문 앞부분."""
    text = (content.body or content.summary or "").strip()
    if len(text) <= size:
        return text
    lowered = text.lower()
    hits = [lowered.find(t) for t in question_tokens if lowered.find(t) >= 0]
    start = max(0, min(hits) - size // 4) if hits else 0
    return ("…" if start else "") + text[start : start + size] + "…"


async def source_url(db: AsyncSession, content_id: int) -> str:
    source = await content_repo.get_primary_source(db, content_id)
    return source[0].source_url if source else ""


async def anchor_item(db: AsyncSession, content_id: int, topic_id: int, question: str) -> dict | None:
    content = await content_repo.get_public_content(db, content_id, topic_id)
    if content is None:
        return None
    return {
        "contentId": content.content_id,
        "title": content.title,
        "url": await source_url(db, content.content_id),
        "excerpt": excerpt(content, tokens(question)),
    }


async def search(db: AsyncSession, topic_id: int, query: str, exclude: set[int]) -> list[dict]:
    pool = await content_repo.list_public_contents(
        db, topic_id, subtopic_id=None, after=None, limit=POOL_SIZE
    )
    query_tokens = tokens(query)
    ranked = sorted(
        ((score(query_tokens, c), c) for c in pool if c.content_id not in exclude),
        key=lambda pair: pair[0],
        reverse=True,
    )
    picked = [c for s, c in ranked[:SEARCH_CANDIDATES] if s > 0]
    return [
        {
            "contentId": c.content_id,
            "title": c.title,
            "url": await source_url(db, c.content_id),
            "excerpt": excerpt(c, query_tokens),
        }
        for c in picked
    ]
