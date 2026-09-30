"""소비자 챗봇 사실 확인 도구(agent/chatbot에 넘긴다): 정해 둔 공식 API만 부른다. 결과는 짧은 값만 받고 저장해서 다시 쓴다.

범용 검색 API는 쓰지 않는다. 호출 주소는 코드에 고정하고 LLM은 검색어만 넘긴다.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import ChatFactCache

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_AGENT = "me-nu/0.1 ( https://github.com/Por0ri/me-and-nu )"
TIMEOUT = 8.0

# 코드 벽: 토픽마다 쓸 수 있는 조회처. 기상청(kma)은 위치 동의 기능이 생기기 전까지 끈다.
TOPIC_PROVIDERS = {
    "movie": ("tmdb", "kobis"),
    "anime": ("tmdb",),
    "music": ("musicbrainz",),
}
CACHE_TTL = {
    "tmdb": timedelta(days=1),
    "kobis": timedelta(hours=12),
    "musicbrainz": timedelta(days=7),
}
ATTRIBUTION = {
    "tmdb": "TMDB 제공",
    "tmdb_ott": "TMDB · JustWatch 제공",
    "kobis": "KOBIS 제공 · 어제 기준",
    "musicbrainz": "MusicBrainz 제공",
}


def providers_for(topic_code: str) -> list[str]:
    keys = {"tmdb": settings.tmdb_key, "kobis": settings.kobis_key, "musicbrainz": True}
    return [p for p in TOPIC_PROVIDERS.get(topic_code, ()) if keys.get(p)]


def _checked(now: datetime) -> str:
    local = now.astimezone(KST)
    return f"{local.month}/{local.day}"


async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """API 재시도 1회(루프 C)."""
    last: Exception | None = None
    for _ in range(2):
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
    raise last  # type: ignore[misc]


async def _tmdb(query: str, topic_code: str) -> tuple[dict, str]:
    kind = "tv" if topic_code == "anime" else "movie"
    base = "https://api.themoviedb.org/3"
    common = {"api_key": settings.tmdb_key, "language": "ko-KR"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        found = await _get_json(client, f"{base}/search/{kind}", {**common, "query": query})
        results = (found.get("results") or [])[:3]
        candidates = [
            {
                "title": r.get("title") or r.get("name"),
                "originalTitle": r.get("original_title") or r.get("original_name"),
                "date": r.get("release_date") or r.get("first_air_date"),
            }
            for r in results
        ]
        if not results:
            return {"candidates": []}, ATTRIBUTION["tmdb"]
        top = results[0]
        detail = await _get_json(
            client,
            f"{base}/{kind}/{top['id']}",
            {**common, "append_to_response": "credits,watch/providers"},
        )
    crew = (detail.get("credits") or {}).get("crew") or []
    directors = [c.get("name") for c in crew if c.get("job") == "Director"][:3]
    kr = ((detail.get("watch/providers") or {}).get("results") or {}).get("KR") or {}
    ott = [p.get("provider_name") for p in kr.get("flatrate") or []][:6]
    data = {
        "candidates": candidates,
        "top": {
            "title": detail.get("title") or detail.get("name"),
            "date": detail.get("release_date") or detail.get("first_air_date"),
            "runtime": detail.get("runtime"),
            "episodes": detail.get("number_of_episodes"),
            "directors": directors,
            "ottKR": ott,
        },
    }
    return data, ATTRIBUTION["tmdb_ott" if ott else "tmdb"]


async def _kobis(query: str, topic_code: str) -> tuple[dict, str]:
    yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y%m%d")
    url = "https://kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        body = await _get_json(client, url, {"key": settings.kobis_key, "targetDt": yesterday})
    rows = ((body.get("boxOfficeResult") or {}).get("dailyBoxOfficeList") or [])[:10]
    data = {
        "date": yesterday,
        "boxOffice": [
            {"rank": r.get("rank"), "title": r.get("movieNm"), "openDate": r.get("openDt"), "audienceTotal": r.get("audiAcc")}
            for r in rows
        ],
    }
    return data, ATTRIBUTION["kobis"]


async def _musicbrainz(query: str, topic_code: str) -> tuple[dict, str]:
    url = "https://musicbrainz.org/ws/2/release-group/"
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        body = await _get_json(client, url, {"query": query, "fmt": "json", "limit": 3})
    groups = body.get("release-groups") or []
    data = {
        "releases": [
            {
                "title": g.get("title"),
                "type": g.get("primary-type"),
                "firstReleaseDate": g.get("first-release-date"),
                "artist": ((g.get("artist-credit") or [{}])[0]).get("name"),
            }
            for g in groups[:3]
        ]
    }
    return data, ATTRIBUTION["musicbrainz"]


_FETCHERS = {"tmdb": _tmdb, "kobis": _kobis, "musicbrainz": _musicbrainz}


async def lookup(
    db: AsyncSession, provider: str, query: str, topic_id: int, topic_code: str
) -> dict | None:
    """저장분을 먼저 보고, 없거나 지났으면 API를 부른다. 실패하면 None."""
    if provider not in providers_for(topic_code):
        return None
    now = datetime.now(timezone.utc)
    key = f"{topic_code}:{' '.join(query.lower().split())}"[:300]
    if provider == "kobis":
        key = f"kobis:{(datetime.now(KST) - timedelta(days=1)).strftime('%Y%m%d')}"
    cached = await db.scalar(
        select(ChatFactCache).where(ChatFactCache.provider == provider, ChatFactCache.lookup_key == key)
    )
    if cached is not None and (cached.expires_at is None or cached.expires_at > now):
        return {
            "provider": provider,
            "attribution": cached.attribution or provider,
            "checkedAt": _checked(cached.fetched_at),
            "data": cached.result,
        }
    try:
        data, attribution = await _FETCHERS[provider](query, topic_code)
    except Exception:  # noqa: BLE001 — 조회 실패는 '못 찾음'으로 처리한다.
        logger.warning("fact lookup failed: %s %s", provider, query, exc_info=True)
        return None
    if cached is None:
        cached = ChatFactCache(provider=provider, lookup_key=key, topic_id=topic_id)
        db.add(cached)
    cached.result = data
    cached.attribution = attribution
    cached.fetched_at = now
    cached.expires_at = now + CACHE_TTL[provider]
    await db.flush()
    return {"provider": provider, "attribution": attribution, "checkedAt": _checked(now), "data": data}
