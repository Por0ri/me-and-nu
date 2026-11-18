"""챗봇의 '우리 글 찾기'. 홈 챗봇은 늘 쓰고, 상세 챗봇은 글을 찾아 달라는 말이 있을 때만 쓴다.

질문에서 찾기 조건을 규칙으로 뽑고 (LLM 을 부르지 않아 빠르고 비용이 없다), 조건에 맞는 공개 글을
모아 LLM 에 후보로 넘긴다. 어떤 글을 보여 줄지 고르고 소개하는 일은 chat_service 가 LLM 에 맡긴다.

조건 (서로 다른 조건은 모두 맞아야 하고, 같은 조건 안에서는 하나만 맞아도 된다)
- 작품: 우리 작품 목록(entity)의 이름 · 별칭이 질문에 있을 때. 세 글자 이상 낱말은 이름 일부로도 찾는다
  ('그래도' · '어떻게' 같은 흔한 낱말은 빼고).
  맞는 이름이 하나도 없으면 한 글자만 다른 네 글자 이상 한글 이름으로 고쳐 읽는다 (사카모테 데이즈 → 사카모토 데이즈)
- 장르 · 소재: 온보딩 칩(preference_tag)의 한국어 이름과 몇 가지 다른 말 (로코 → 로맨스 + 코미디).
  칩이 둘 이상이면 모두 가진 작품의 글을 먼저 찾고, 없으면 하나라도 가진 작품의 글
- 글 종류: 에이전트 글 종류 (팝업 · 전시 → 행사, OST → 음악 …) · 카드뉴스
- 내 기록: 최근 30일 동안 본 글 · 좋아요 누른 글 · 저장한 글 (기록 최신순)
- 비슷한 글: 기준 작품(질문 속 작품 → 보고 있는 글의 작품 → 최근 본 글의 작품)과 같은 작품의 글,
  그리고 장르 · 소재 칩이 2개 이상 겹치는 작품의 글 (많이 겹치는 작품 먼저)
모으는 순서: 오늘(없으면 어제) 추천에 든 글 → 제목에 조건 낱말(장르 · 소재 · 작품 이름)이 든 글 → 나머지.
같은 순서 안에서는 글 종류를 번갈아 최신 글부터 모은다 (최신 글만 모으면 마지막에 넣은 묶음의 글 종류만 몰린다).
줄 세우기는 홈 검색과 같다: 오늘 추천 순서, 그다음 위에서 모은 순서.
'그만볼래요' 한 글과 보고 있는 글은 빼고, "더 보여줘" 처럼 조건 없이 이어 물으면 이미 보여 준 글도 뺀다.
딱 맞는 글이 없으면 글 종류 조건만 빼고 한 번 더 찾는다 (LLM 이 "행사 글은 없고 대신 …" 이라고 말할 수 있게).
그래도 물은 작품의 글이 없으면 장르 · 소재 칩이 겹치는 작품의 글을 대신 넘긴다 ("나루토 글은 아직 없어 …").
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.integrations import dictionary
from app.models import Content, ContentEntityLink, Entity, EntityTag
from app.repositories import bookmark_repository, my_activity_repository
from app.repositories import onboarding_catalog_repository as work_search_repo
from app.repositories import recommendation_repository as recommendation_repo
from app.repositories.carousel_repository import CAROUSEL_METHOD
from app.services.my_activity_service import RECENT_VIEW_DAYS, counts_as_viewed

KST = timezone(timedelta(hours=9))
MAX_CANDIDATES = 10  # LLM 에 넘기는 후보 글 수 (LLM 은 이 중 최대 5개를 고른다)
MAX_MINE = 20  # 내 기록에서 찾을 때는 제목 · 요약만 보고 골라야 해서 후보를 더 넘긴다
MAX_SAME_WORK = 4  # 비슷한 글: 같은 작품 글은 이만큼만, 나머지는 비슷한 작품 글
POOL = 200  # 조건에 맞는 글을 이만큼 모은 뒤 추천 순서로 다시 줄 세운다 (모으는 순서는 _search)
MY_RECORDS = 50  # 좋아요 · 저장 기록에서 볼 최신 글 수
SIMILAR_WORKS = 20
MIN_SHARED_TAGS = 2  # 비슷한 작품: 기준 작품과 겹쳐야 하는 장르 · 소재 칩 수
MAX_NAME_PART_WORKS = 3  # 이름 일부가 이보다 많은 작품에 맞으면 흔한 낱말로 보고 버린다
MIN_NAME_PART_LENGTH = 3
MIN_TYPO_NAME_LENGTH = 4  # 한 글자 오타로 보고 맞춰 볼 이름의 최소 글자 수 (한글 이름만)
SUMMARY_LIMIT = 160
WORKS_TTL_SECONDS = 300  # 작품 이름 목록은 5분 동안 다시 읽지 않는다

# 에이전트 글 종류 (agents/ani_me;nu models.CONTENT_TYPE_LABELS 와 같은 이름, behind 는 예전 값)
TYPE_LABELS = {
    "news": "소식", "event": "행사", "guide": "작품 가이드", "people": "인물", "goods": "굿즈",
    "review": "리뷰 분석", "humor": "유머", "character": "캐릭터", "curation": "큐레이션", "music": "음악",
    "behind": "비하인드",
}
# 질문 낱말 → 글 종류. 한국어는 낱말 앞에 다른 한글이 붙지 않을 때만, 영어는 단어 전체만 맞춘다
TYPE_WORDS = {
    "news": ("소식", "뉴스", "근황"),
    "event": ("행사", "이벤트", "팝업", "전시", "콜라보", "카페", "페스티벌", "축제", "콘서트", "공연", "상영회",
              "무대인사"),
    "guide": ("가이드", "입문", "정주행", "보는 순서", "볼 순서", "시청 순서"),
    "people": ("인물", "인터뷰"),
    "goods": ("굿즈", "피규어", "상품", "한정판", "블루레이"),
    "review": ("리뷰", "후기", "평가", "반응", "평점"),
    "humor": ("유머", "웃긴", "웃기는", "짤방", "밈"),
    "character": ("캐릭터 소개", "캐릭터 분석", "캐릭터 글", "캐릭터 콘텐츠"),
    "curation": ("큐레이션", "모음", "총정리"),
    "music": ("음악", "노래", "ost", "주제가", "오프닝", "엔딩", "애니송", "op", "ed"),
    "behind": ("비하인드", "뒷이야기", "제작 비화"),
}
# 칩 이름 말고 흔히 쓰는 말 → 칩 이름
TAG_WORDS = {
    "로코": ("로맨스", "코미디"), "러브코미디": ("로맨스", "코미디"), "러브 코미디": ("로맨스", "코미디"),
    "럽코": ("로맨스", "코미디"), "개그": ("코미디",), "공포": ("호러",), "추리": ("미스터리",), "학교": ("학원",),
    "치유": ("힐링",), "공상과학": ("SF",), "메카": ("로봇",), "타임루프": ("시간 여행",), "타임리프": ("시간 여행",),
}
MINE_LABELS = {
    "viewed": "내 기록: 최근 30일 동안 본 글", "liked": "내 기록: 좋아요 누른 글", "saved": "내 기록: 저장한 글",
}

_FIND = re.compile(
    r"찾아|찾고|찾는|찾을|보여|추천|볼\s*만|볼거리|읽을\s*만|모아\s*줘|골라\s*줘|콘텐츠|컨텐츠|카드\s*뉴스|게시물|포스트"
    r"|관련\s*글|다른\s*글|글\s*(있|없|좀|추천|보여|찾)"
)
_CARD_NEWS = re.compile(r"카드\s*뉴스")
_VIEWED = re.compile(r"(?<![가-힣])(본|봤던|봤었던|읽은|읽었던|열어\s*본)\s*(거|것|글|콘텐츠|컨텐츠|카드|게시물)")
_LIKED = re.compile(r"좋아요\s*(를\s*)?(누른|눌렀던|눌러\s*둔|한|했던)")
_SAVED = re.compile(r"저장|북마크|담아\s*둔|찜")
_SIMILAR = re.compile(r"비슷|유사|같은\s*(느낌|분위기|장르|류)|이런\s*(느낌|분위기|류|거|것|글)|이거랑|이것과")
# '음악 애니' · '음악 장르' 는 장르(음악 칩), 그냥 '음악' 은 글 종류(음악 글)
_GENRE_AFTER = r"(?=\s*(애니|작품|장르|만화))"
_NOT_GENRE_AFTER = r"(?!\s*(애니|작품|장르|만화))"
_HANGUL = re.compile(r"[가-힣]+")
# 작품 이름으로 보지 않는 낱말 (글 종류 · 장르 다른 말 · 찾기 말)
_RESERVED = frozenset(
    {dictionary.name_key(word) for words in TYPE_WORDS.values() for word in words}
    | {dictionary.name_key(word) for word in TAG_WORDS}
    | {"카드뉴스", "콘텐츠", "컨텐츠", "게시물", "포스트", "관련", "장르", "추천", "비슷한"}
)
# 이름 일부로는 보지 않는 흔한 낱말 (이름 전체를 쓰면 그대로 찾는다: "그래도 세계는 아름다워").
# 흔한 질문 낱말 약 150개를 작품 이름과 맞춰 봤을 때 작품 1~3개에 잘못 맞은 것 + OTT 이름 (2026-11-17)
_NOT_NAME_PARTS = frozenset(dictionary.name_key(word) for word in (
    "그래도", "그리고", "그래서", "어떻게", "마지막", "사이트", "목소리", "레전드",
    "디즈니", "넷플릭스", "라프텔", "티빙", "왓챠", "유튜브", "웨이브", "쿠팡플레이",
))


def _pattern(word: str) -> str:
    """낱말 하나의 정규식. 한국어는 앞에 다른 한글이 붙지 않을 때만 (일본 ≠ 본), 영어는 단어 전체만."""
    if word.isascii():
        return rf"(?<![0-9a-z]){re.escape(word)}(?![0-9a-z])"
    return r"(?<![가-힣])" + r"\s*".join(re.escape(part) for part in word.split())


_TYPE_PATTERNS = {
    code: re.compile("|".join(_pattern(word) + (_NOT_GENRE_AFTER if word == "음악" else "") for word in words))
    for code, words in TYPE_WORDS.items()
}


@dataclass
class Found:
    """LLM 에 넘길 후보 글과, 그 글을 찾은 조건."""

    conditions: list[str]
    contents: list[Content] = field(default_factory=list)
    work_names: dict[int, str] = field(default_factory=dict)  # 글 번호 → 대표 작품 이름
    labels: dict[int, str] = field(default_factory=dict)  # 글 번호 → '같은 작품' · '비슷한 작품'
    relaxed: bool = False  # 딱 맞는 글이 없어서 글 종류 조건을 빼고 찾았다
    base_missing: bool = False  # 비슷한 글을 찾는데 기준 작품을 못 정했다 (LLM 이 되묻는다)
    substituted: bool = False  # 물은 작품의 글이 없어서 장르 · 소재가 비슷한 작품 글을 대신 찾았다

    def prompt_block(self) -> str:
        head = "찾은 조건: " + " / ".join(self.conditions)
        if self.substituted:
            head += " (이 작품 글이 아직 없어서 장르 · 소재가 비슷한 작품 글을 찾았다)"
        if self.relaxed:
            head += " (딱 맞는 글이 없어서 글 종류 조건은 빼고 찾았다)"
        lines = [head]
        for number, content in enumerate(self.contents, start=1):
            meta = (TYPE_LABELS.get(content.content_type or ""), self.work_names.get(content.content_id),
                    self.labels.get(content.content_id), _day(content.published_at))
            lines.append(f"[글 {number}] {content.title} ({' · '.join(part for part in meta if part)})")
            if content.summary and content.summary.strip():
                lines.append(content.summary.strip()[:SUMMARY_LIMIT])
        if not self.contents:
            lines.append("(조건에 맞는 글 없음)")
        return "\n".join(lines)


@dataclass(frozen=True)
class _Ask:
    """질문에서 읽은 찾기 조건."""

    wants: bool  # 글을 찾거나 추천해 달라는 말이 있다
    works: tuple[int, ...] = ()
    tags: tuple = ()  # PreferenceTag
    types: tuple[str, ...] = ()
    card_news: bool = False
    mine: str | None = None  # viewed · liked · saved
    similar: bool = False
    corrections: tuple[tuple[str, str], ...] = ()  # (질문에 쓴 이름, 고쳐 읽은 작품 이름)

    @property
    def has_conditions(self) -> bool:
        return bool(self.works or self.tags or self.types or self.card_news or self.mine or self.similar)


@dataclass
class _WorkIndex:
    names: dict[int, str]  # 작품 번호 → 이름
    keys: dict[str, set[int]]  # 비교용 이름 · 별칭 → 작품 번호들
    loaded_at: float
    masks: dict[str, set[str]] | None = None  # 한 글자를 가린 한글 이름 → 이름들 (오타 찾기, 처음 쓸 때 만든다)


_work_indexes: dict[int, _WorkIndex] = {}


def _day(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:  # SQLite 시험 DB 는 시간대를 잃는다
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KST).date().isoformat()


async def _work_index(db: AsyncSession, topic_id: int) -> _WorkIndex:
    """분야의 작품 이름 · 별칭 (비활성 작품도: 글은 비활성 작품에도 연결된다)."""
    cached = _work_indexes.get(topic_id)
    if cached is not None and time.monotonic() - cached.loaded_at < WORKS_TTL_SECONDS:
        return cached
    rows = await db.execute(
        select(Entity.entity_id, Entity.name, Entity.aliases)
        .where(Entity.topic_id == topic_id, Entity.entity_type == "anime")
    )
    names: dict[int, str] = {}
    keys: dict[str, set[int]] = {}
    for entity_id, name, aliases in rows.all():
        names[entity_id] = name
        for value in [name, *(aliases if isinstance(aliases, list) else [])]:
            key = dictionary.name_key(value) if isinstance(value, str) else ""
            if len(key) >= dictionary.MIN_KEY_LENGTH:
                keys.setdefault(key, set()).add(entity_id)
    index = _WorkIndex(names, keys, time.monotonic())
    _work_indexes[topic_id] = index
    return index


def _typo_masks(index: _WorkIndex) -> dict[str, set[str]]:
    """한 글자를 '*' 로 가린 한글 이름 → 원래 이름들. 처음 오타를 찾을 때 한 번 만든다."""
    if index.masks is None:
        masks: dict[str, set[str]] = {}
        for key in index.keys:
            if len(key) >= MIN_TYPO_NAME_LENGTH and _HANGUL.fullmatch(key):
                for position in range(len(key)):
                    masks.setdefault(key[:position] + "*" + key[position + 1:], set()).add(key)
        index.masks = masks
    return index.masks


def _typo_hits(index: _WorkIndex, text: str, words: list[re.Match],
               reserved: frozenset[str]) -> list[tuple[int, int, int, int, set[int]]]:
    """맞는 이름이 하나도 없을 때만: 한 글자만 다른 한글 이름 ("사카모테 데이즈" → "사카모토 데이즈").

    질문 낱말(찾기 말 · 장르 · 글 종류 · '애니' 같은 흔한 말)이 섞인 묶음은 이름으로 보지 않고,
    맞는 작품이 너무 많으면 버린다.
    """
    masks = _typo_masks(index)
    # 이 말로 시작하는 낱말은 질문 낱말로 본다 ("팝업이나" · "판타지물" · "애니로")
    blocked = [word for word in reserved | dictionary.STOP_NAMES if len(word) >= 2]

    def question_word(word: str) -> bool:
        key = dictionary.name_key(word)
        return _wants(word) or any(key.startswith(other) for other in blocked)

    hits: list[tuple[int, int, int, int, set[int]]] = []
    for key, places in dictionary.question_spans(text).items():
        if len(key) < MIN_TYPO_NAME_LENGTH or not _HANGUL.fullmatch(key) or key in index.keys:
            continue
        names = set().union(*(masks.get(key[:position] + "*" + key[position + 1:], set())
                              for position in range(len(key))))
        ids = set().union(*(index.keys[name] for name in names)) if names else set()
        if not 0 < len(ids) <= MAX_NAME_PART_WORKS:
            continue
        for start, end in places:
            if not any(question_word(word.group()) for word in words[start:end]):
                hits.append((end - start, len(key), start, end, ids))
    return hits


def _match_works(
    index: _WorkIndex, text: str, reserved: frozenset[str],
) -> tuple[tuple[int, ...], list[tuple[int, int]], tuple[tuple[str, str], ...]]:
    """질문 속 작품 → (작품 번호들, 이름이 차지한 글자 범위들, 오타를 고쳐 읽은 이름들).

    긴 이름이 먼저, 겹치는 짧은 이름은 버린다.
    """
    hits: list[tuple[int, int, int, int, set[int]]] = []  # (단어 수, 글자 수, 시작, 끝, 작품들)
    for key, places in dictionary.question_spans(text).items():
        if key in reserved:
            continue
        ids = index.keys.get(key)
        if ids is None and len(key) >= MIN_NAME_PART_LENGTH and key not in _NOT_NAME_PARTS:
            # 이름 일부 ("프리렌" → "장송의 프리렌"): 한 단어짜리만, 너무 많은 작품에 맞으면 흔한 낱말로 본다
            places = [(start, end) for start, end in places if end - start == 1]
            if places:
                part = set().union(*(found for name, found in index.keys.items() if key in name))
                ids = part if 0 < len(part) <= MAX_NAME_PART_WORKS else None
        for start, end in places if ids else ():
            hits.append((end - start, len(key), start, end, ids))
    words = list(dictionary._WORD.finditer(text))  # question_spans 와 같은 단어 자르기
    typo = not hits
    if typo:
        hits = _typo_hits(index, text, words, reserved)
    hits.sort(key=lambda hit: (-hit[0], -hit[1], hit[2]))
    taken: list[tuple[int, int, set[int]]] = []
    for _, _, start, end, ids in hits:
        if all(end <= other_start or other_end <= start for other_start, other_end, _ in taken):
            taken.append((start, end, ids))
    taken.sort(key=lambda place: place[0])
    works: list[int] = []
    for _, _, ids in taken:
        works.extend(entity_id for entity_id in sorted(ids) if entity_id not in works)
    spans = [(words[start].start(), words[end - 1].end()) for start, end, _ in taken]
    corrections = tuple(
        (text[span_start:span_end], _names(index, sorted(ids)))
        for (span_start, span_end), (_, _, ids) in zip(spans, taken)
    ) if typo else ()
    return tuple(works), spans, corrections


def _match_tags(text: str, tags: list, free) -> tuple:
    by_name = {tag.display_name: tag for tag in tags}
    phrases = [(tag.display_name, (tag.display_name,)) for tag in tags] + list(TAG_WORDS.items())
    picked: list = []
    for phrase, names in phrases:
        pattern = _pattern(phrase.lower()) + (_GENRE_AFTER if phrase == "음악" else "")
        if any(free(match) for match in re.finditer(pattern, text)):
            for name in names:
                tag = by_name.get(name)
                if tag is not None and tag not in picked:
                    picked.append(tag)
    return tuple(picked)


def _mine(text: str) -> str | None:
    return ("viewed" if _VIEWED.search(text) else "liked" if _LIKED.search(text)
            else "saved" if _SAVED.search(text) else None)


def _wants(text: str) -> bool:
    """글을 찾거나 추천해 달라는 말이 있는지 (DB 없이)."""
    return bool(_FIND.search(text) or _SIMILAR.search(text)) or _mine(text) is not None


def _read(text: str, index: _WorkIndex, tags: list) -> _Ask:
    """소문자로 바꾼 질문에서 찾기 조건을 읽는다. 작품 이름 자리의 낱말은 장르 · 글 종류로 보지 않는다."""
    reserved = _RESERVED | {dictionary.name_key(tag.display_name) for tag in tags}
    works, taken, corrections = _match_works(index, text, reserved)

    def free(match: re.Match) -> bool:
        start, end = match.span()
        return all(end <= taken_start or taken_end <= start for taken_start, taken_end in taken)

    mine = _mine(text)
    similar = bool(_SIMILAR.search(text))
    return _Ask(
        wants=_wants(text),
        works=works,
        tags=_match_tags(text, tags, free),
        types=tuple(code for code, pattern in _TYPE_PATTERNS.items() if any(free(m) for m in pattern.finditer(text))),
        card_news=bool(_CARD_NEWS.search(text)),
        mine=mine,
        similar=similar,
        corrections=corrections,
    )


async def find(
    db: AsyncSession, *, user_id: int, topic_id: int, tap_id: int, anchor_content_id: int | None,
    question: str, previous: str | None = None, shown: list[int] | tuple[int, ...] = (),
) -> Found | None:
    """후보 글을 모은다. 상세 챗봇에서 찾아 달라는 말이 없으면 None (지금 글에 대한 질문으로 본다)."""
    if anchor_content_id is not None and not _wants(question.lower()):
        return None  # 상세 챗봇 질문 대부분은 여기서 끝난다 (DB 를 읽지 않는다)
    index = await _work_index(db, topic_id)
    tags = await work_search_repo.list_active_tags(db, topic_id)
    ask = _read(question.lower(), index, tags)
    exclude: set[int] = set()
    if not ask.has_conditions:
        # "더 보여줘" 처럼 조건 없이 이어 물으면 앞 질문의 조건으로 찾고, 이미 보여 준 글은 뺀다
        exclude |= set(shown)
        earlier = _read(previous.lower(), index, tags) if previous else None
        if earlier is not None and earlier.has_conditions:
            ask = earlier
    return await _collect(db, ask, index, user_id=user_id, topic_id=topic_id, tap_id=tap_id,
                          anchor_content_id=anchor_content_id, exclude=exclude)


def _linked_to(entity_ids) -> object:
    """그 작품들에 연결된 글 (작품 번호 목록이나 작품 번호를 고르는 SQL)."""
    return Content.content_id.in_(
        select(ContentEntityLink.content_id).where(ContentEntityLink.entity_id.in_(entity_ids))
    )


def _tagged(tag_ids: list[int], *, every: bool) -> object:
    works = select(EntityTag.entity_id).where(EntityTag.tag_id.in_(tag_ids))
    if every and len(tag_ids) > 1:
        works = works.group_by(EntityTag.entity_id).having(func.count(func.distinct(EntityTag.tag_id)) == len(tag_ids))
    return _linked_to(works)


async def _search(db: AsyncSession, topic_id: int, filters: list, exclude: set[int], *,
                  ranked: list[int] | tuple[int, ...] = (), words: tuple[str, ...] = ()) -> list[int]:
    """조건에 맞는 공개 글 번호 (최대 POOL 개).

    오늘 추천에 든 글(ranked) → 제목에 조건 낱말(words)이 든 글 → 나머지 순서로 모은다. 최신 글만 모으면
    추천에 든 글과 딱 맞는 모음 글이 POOL 밖으로 밀리고, 마지막에 넣은 묶음의 글 종류만 몰린다.
    같은 순서 안에서는 글 종류를 번갈아 최신 글부터 모은다.
    """
    newest = (Content.published_at.desc().nulls_last(), Content.content_id.desc())
    tiers = []
    if ranked:
        tiers.append(Content.content_id.in_(list(ranked)))
    if words:
        tiers.append(or_(*(Content.title.contains(word, autoescape=True) for word in words)))
    tier = case(*((condition, number) for number, condition in enumerate(tiers)), else_=len(tiers)) if tiers else None
    turn = func.row_number().over(
        partition_by=[tier, Content.content_type] if tier is not None else [Content.content_type], order_by=newest)
    statement = select(Content.content_id).where(*recommendation_repo._visible(topic_id), *filters)
    if exclude:
        statement = statement.where(Content.content_id.not_in(sorted(exclude)))
    order = [tier, turn, *newest] if tier is not None else [turn, *newest]
    statement = statement.order_by(*order).limit(POOL)
    return list((await db.scalars(statement)).all())


def _title_words(ask: _Ask, index: _WorkIndex) -> tuple[str, ...]:
    """제목에 있으면 먼저 모을 낱말: 장르 · 소재 칩 이름과 그 다른 말, 물은 작품 이름 ("고어 애니 모음")."""
    names = {tag.display_name for tag in ask.tags}
    words = [*names, *(word for word, tags in TAG_WORDS.items() if set(tags) <= names)]
    words += [index.names[entity_id] for entity_id in ask.works if entity_id in index.names]
    return tuple(dict.fromkeys(word for word in words if word))


async def _rank_positions(db: AsyncSession, *, tap_id: int, topic_id: int) -> dict[int, int]:
    """오늘(없으면 어제) 추천 순서. 홈 검색과 같은 기준."""
    today = datetime.now(KST).date()
    round_ = await recommendation_repo.latest_round(db, tap_id, [today, today - timedelta(days=1)])
    if round_ is None:
        return {}
    ranked = await recommendation_repo.ranked_items(db, round_.recommendation_round_id, topic_id)
    return {content_id: position for position, (content_id, _, _) in enumerate(ranked)}


async def _my_records(db: AsyncSession, mine: str, user_id: int, topic_id: int) -> list[int]:
    """내 기록의 글 번호 (기록 최신순). 본 글은 마이페이지 '최근 본 글'과 같은 기준."""
    if mine == "viewed":
        since = datetime.now(timezone.utc) - timedelta(days=RECENT_VIEW_DAYS)
        last_seen: dict[int, datetime] = {}
        for content_id, kind, value, at in await my_activity_repository.list_view_events(
                db, user_id, topic_id=topic_id, since=since):
            if content_id is None or not counts_as_viewed(kind, value or {}):
                continue
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            if content_id not in last_seen or at > last_seen[content_id]:
                last_seen[content_id] = at
        return [content_id for content_id, _ in sorted(last_seen.items(), key=lambda item: item[1], reverse=True)]
    if mine == "liked":
        rows = await my_activity_repository.list_liked_contents(db, user_id, topic_id=topic_id, after=None,
                                                                limit=MY_RECORDS)
        return [content.content_id for _, content in rows]
    rows = await bookmark_repository.list_saved_bookmarks(db, user_id, topic_id=topic_id, q=None, after=None,
                                                          limit=MY_RECORDS)
    return [content.content_id for _, content in rows]


async def _linked_works(db: AsyncSession, content_ids: list[int]) -> list[int]:
    rows = await db.scalars(
        select(ContentEntityLink.entity_id)
        .where(ContentEntityLink.content_id.in_(content_ids))
        .order_by(ContentEntityLink.sort_order)
    )
    return list(dict.fromkeys(rows.all()))


async def _similar_base(db: AsyncSession, ask: _Ask, *, user_id: int, topic_id: int,
                        anchor_content_id: int | None) -> list[int]:
    """비슷한 글의 기준 작품: 질문 속 작품 → 보고 있는 글의 작품 → 최근 본 글의 작품."""
    if ask.works:
        return list(ask.works)
    if anchor_content_id is not None:
        linked = await _linked_works(db, [anchor_content_id])
        if linked:
            return linked
    viewed = await _my_records(db, "viewed", user_id, topic_id)
    return await _linked_works(db, viewed[:1]) if viewed else []


async def _similar_works(db: AsyncSession, base: list[int]) -> list[int]:
    """기준 작품과 장르 · 소재 칩이 MIN_SHARED_TAGS 개 이상 겹치는 작품 (많이 겹치는 순서)."""
    base_tag = aliased(EntityTag)
    shared = func.count(func.distinct(EntityTag.tag_id))
    rows = await db.scalars(
        select(EntityTag.entity_id)
        .where(EntityTag.tag_id.in_(select(base_tag.tag_id).where(base_tag.entity_id.in_(base))),
               EntityTag.entity_id.not_in(base))
        .group_by(EntityTag.entity_id)
        .having(shared >= MIN_SHARED_TAGS)
        .order_by(shared.desc(), EntityTag.entity_id)
        .limit(SIMILAR_WORKS)
    )
    return list(rows.all())


def _names(index: _WorkIndex, entity_ids) -> str:
    return " · ".join(index.names[entity_id] for entity_id in list(entity_ids)[:3] if entity_id in index.names)


async def _collect(db: AsyncSession, ask: _Ask, index: _WorkIndex, *, user_id: int, topic_id: int, tap_id: int,
                   anchor_content_id: int | None, exclude: set[int]) -> Found:
    found = Found(conditions=[])
    exclude = exclude | await recommendation_repo.negative_content_ids(db, user_id, tap_id)
    if anchor_content_id is not None:
        exclude.add(anchor_content_id)
    filters: list = []  # 한 번 더 찾을 때도 늘 지키는 조건
    mine_ids: list[int] | None = None
    if ask.mine:
        mine_ids = await _my_records(db, ask.mine, user_id, topic_id)
        filters.append(Content.content_id.in_(mine_ids))
        found.conditions.append(MINE_LABELS[ask.mine])
    if ask.card_news:
        filters.append(Content.ai_judgment_basis["method"].as_string() == CAROUSEL_METHOD)
        found.conditions.append("카드뉴스만")
    base: list[int] = []
    if ask.similar:
        base = await _similar_base(db, ask, user_id=user_id, topic_id=topic_id, anchor_content_id=anchor_content_id)
        found.conditions.append(
            f"비슷한 글 (기준 작품: {_names(index, base)}) — 같은 작품 글, 그리고 장르 · 소재 칩이 겹치는 작품 글"
            if base else "비슷한 글: 기준 작품 없음"
        )
        if not base:
            found.base_missing = True  # 무엇과 비슷한 글인지 모른다 → LLM 이 되묻는다
            return found
    work_filter = None
    if ask.works and not base:
        work_filter = _linked_to(list(ask.works))
        filters.append(work_filter)
        found.conditions.append("작품: " + _names(index, ask.works))
    for typed, name in ask.corrections:
        found.conditions.append(f"이름 고쳐 읽음: 질문의 '{typed}' → {name}")
    if ask.tags:
        found.conditions.append("장르 · 소재: " + " · ".join(tag.display_name for tag in ask.tags))
    if ask.types:
        found.conditions.append("글 종류: " + " · ".join(TYPE_LABELS[code] for code in ask.types))
    if not found.conditions:
        found.conditions.append("조건 없음: 오늘 추천 순서")

    rank = await _rank_positions(db, tap_id=tap_id, topic_id=topic_id)
    mine_order = {content_id: position for position, content_id in enumerate(mine_ids or [])}

    def ordered(ids: list[int]) -> list[int]:
        if mine_ids is not None:
            return sorted(ids, key=lambda content_id: mine_order.get(content_id, len(mine_order)))
        return sorted(ids, key=lambda content_id: rank.get(content_id, len(rank)))  # 같으면 모은 순서 그대로

    async def search(extra: list, skip: set[int], words: tuple[str, ...] = ()) -> list[int]:
        return ordered(await _search(db, topic_id, extra, skip, ranked=list(rank), words=words))

    tag_ids = [tag.tag_id for tag in ask.tags]
    tries = [(True, True)]  # (칩을 모두 가진 작품만, 글 종류 조건)
    if len(tag_ids) > 1:
        tries.append((False, True))
    if ask.types and (filters or tag_ids or base):
        tries.append((False, False))
    ids: list[int] = []
    for every_tag, with_types in tries:
        extra = list(filters)
        if tag_ids:
            extra.append(_tagged(tag_ids, every=every_tag))
        if with_types and ask.types:
            extra.append(Content.content_type.in_(ask.types))
        if base:
            ids = await _similar_ids(db, base, extra, exclude, search, found)
        else:
            ids = await search(extra, exclude, _title_words(ask, index))
        if ids:
            found.relaxed = bool(ask.types) and not with_types
            break
    if not ids and work_filter is not None and not ask.mine:
        # 물은 작품의 글이 없다: 장르 · 소재 칩이 겹치는 작품의 글을 대신 넘긴다 ("나루토 글은 아직 없어 …")
        ids = await _substitute_ids(db, list(ask.works), [f for f in filters if f is not work_filter],
                                    ask, tag_ids, exclude, search, found)

    chosen = ids[: MAX_MINE if ask.mine else MAX_CANDIDATES]
    rows = await recommendation_repo.contents_by_id(db, chosen, topic_id)
    found.contents = [rows[content_id] for content_id in chosen if content_id in rows]
    main = await recommendation_repo.main_works(db, [content.content_id for content in found.contents])
    found.work_names = {content_id: index.names[entity_id] for content_id, entity_id in main.items()
                        if entity_id in index.names}
    return found


async def _similar_ids(db: AsyncSession, base: list[int], extra: list, exclude: set[int],
                       search, found: Found) -> list[int]:
    """같은 작품 글 (추천 순서로 MAX_SAME_WORK 개) + 비슷한 작품 글 (많이 겹치는 작품 먼저)."""
    same = (await search([*extra, _linked_to(base)], exclude))[:MAX_SAME_WORK]
    works = await _similar_works(db, base)
    others = await search([*extra, _linked_to(works)], exclude | set(same)) if works else []
    others = await _by_closeness(db, others, works)
    found.labels = {content_id: "같은 작품" for content_id in same} | {content_id: "비슷한 작품" for content_id in others}
    return same + others


async def _by_closeness(db: AsyncSession, ids: list[int], works: list[int]) -> list[int]:
    """비슷한 작품 글을 많이 겹치는 작품 먼저. 안정 정렬이라 같은 작품 안에서는 추천 순서 그대로."""
    if not ids:
        return ids
    closeness = {entity_id: position for position, entity_id in enumerate(works)}
    main = await recommendation_repo.main_works(db, ids)
    return sorted(ids, key=lambda content_id: closeness.get(main.get(content_id), len(closeness)))


async def _substitute_ids(db: AsyncSession, asked: list[int], filters: list, ask: _Ask,
                          tag_ids: list[int], exclude: set[int], search, found: Found) -> list[int]:
    """물은 작품 대신 칩이 겹치는 작품의 글. 글 종류 조건은 먼저 지키고, 없으면 빼고 한 번 더."""
    works = await _similar_works(db, asked)
    if not works:
        return []
    for with_types in ((True, False) if ask.types else (True,)):
        extra = [*filters, _linked_to(works)]
        if tag_ids:
            extra.append(_tagged(tag_ids, every=False))
        if with_types and ask.types:
            extra.append(Content.content_type.in_(ask.types))
        ids = await _by_closeness(db, await search(extra, exclude), works)
        if ids:
            found.substituted = True
            found.relaxed = bool(ask.types) and not with_types
            found.labels = {content_id: "비슷한 작품" for content_id in ids}
            return ids
    return []
