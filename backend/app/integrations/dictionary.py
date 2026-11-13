"""정보사전(RAG 사전 DB) 읽기 전용 조회.

질문 속 작품 · 캐릭터 · 인물 · 곡 · 스튜디오 이름을 사전 별칭에서 찾고, 그 대상의 사실을
출처 · 근거 주소와 함께 돌려준다. 사전은 RAG 에이전트가 채우고 여기서는 읽기만 한다.
DICT_DATABASE_URL 이 없거나 사전에 닿지 않으면 빈 결과를 돌려주고, 챗봇은 우리 글로만 답한다.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------- 규칙 값 ----------

# 이름 비교에 남기는 글자. 나머지 기호 · 띄어쓰기는 지운다 (SQL 쪽도 같은 묶음을 쓴다)
_KEEP = "0-9a-z가-힣ぁ-んァ-ヶー一-龥"
_DROP = re.compile(f"[^{_KEEP}]+")
_WORD = re.compile(f"[{_KEEP}]+")
_SQL_DROP = f"[^{_KEEP}]"

# 단어 끝 조사 (긴 것부터 본다). 뗀 판과 안 뗀 판을 둘 다 찾으니 이름이 사라지지는 않는다
PARTICLES = (
    "에서는", "에게서", "에서", "이랑", "하고", "에게", "한테", "까지", "부터", "처럼", "보다", "으로",
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "랑", "와", "과", "로", "만",
)
# 질문에 흔히 나오는 서비스 단어. 같은 이름의 캐릭터가 있어도 대상으로 잡지 않는다 (애니 → 애니 레온하트)
STOP_NAMES = frozenset({
    "애니", "애니메이션", "만화", "웹툰", "소설", "영화", "극장판", "작품", "시리즈", "시즌",
    "주인공", "캐릭터", "성우", "감독", "작가", "노래", "오프닝", "엔딩", "주제가", "추천", "요즘", "인기",
})
MAX_SPAN_WORDS = 6
MIN_KEY_LENGTH = 2
# 질문 + 고른 문장 중 이름을 찾는 앞부분 길이 (고른 문장이 길면 비교할 글자가 수천 개가 돼 느려진다)
MAX_LOOKUP_CHARS = 700
# 두 글자 이름이나 이름 일부로 찾은 캐릭터 · 인물은 인기(즐겨찾기 수)가 이 이상일 때만 쓴다 (엄마 · 경찰 · 군인)
MIN_POPULARITY = 100
MAX_TARGETS = 4
MAX_ENTRIES = 8  # 작품 하나에서 넣는 시즌 · 극장판 수 (인기 순, 8개 넘는 작품은 30편)
MAX_LINES = 90
MAX_CHARS = 10_000
# 챗봇에서 쓰지 않는 출처: KOBIS 는 저장 · 재전송 제한, 온나다 · 나무위키는 노션 "RAG 에이전트 정리"에서 보류
EXCLUDED_SOURCES = ("kobis", "onnada", "namuwiki")

PERSON_KINDS = ("character", "person")
TITLE_KINDS = ("work", "entry")

# 대상 종류마다 가져오는 칸 (이름이 _ref 로 끝나는 참고 글 칸은 넣지 않는다)
FIELDS: dict[str, tuple[str, ...]] = {
    # 작품(시리즈) 칸의 화수 · 날짜는 특정 시즌 값이 붙어 있는 경우가 있어서 시즌 줄에서만 쓴다
    # entry_count 는 사전 칸이 아니라 여기서 센 시즌 · 극장판 수
    "work": ("entry_count", "kr_title", "kr_ott", "kr_rating", "broadcaster", "official_site", "award"),
    "entry": ("kr_title", "status", "start_date", "end_date", "next_airing", "episodes", "duration_min",
              "main_studio", "studio", "director", "staff", "source_material", "source_work", "genre",
              "character", "theme_song", "theme_song_ja", "kr_ott", "kr_rating", "broadcaster",
              "official_site", "award", "format", "season_year"),
    "character": ("gender", "birthday", "age", "blood_type", "voice_actor", "voice_actor_ko"),
    "person": ("occupation", "person_gender", "birth_date", "death_date", "home_town", "birthplace",
               "years_active", "person_blood_type", "affiliation", "record_label", "award", "official_site"),
    "song": ("performer", "song_title_ja", "song_credit", "release_date", "release_type"),
    "studio": ("founded", "headquarters", "founder", "parent_org", "official_site"),
}
# 시즌 이름 옆에만 쓰고 줄로는 내지 않는 칸
LABEL_ONLY_FIELDS = frozenset({"format", "season_year"})
# 핵심 칸: 줄 수 · 글자 수가 모자라면 모든 대상의 핵심 칸이 다른 칸보다 먼저 자리를 받는다
# (시즌이 많은 작품에서 뒤쪽 시즌의 화수 · 날짜가 통째로 잘리지 않게)
CORE_FIELDS = frozenset({
    "entry_count", "kr_title", "status", "start_date", "end_date", "next_airing", "episodes", "main_studio",
    "studio", "director", "birthday", "voice_actor", "voice_actor_ko", "occupation", "birth_date", "performer",
    "appears_in", "voiced", "made", "used_in",
})
# 질문에 맞는 칸만 넣기: 질문 속 낱말 → 넣을 칸. 맞는 낱말이 없으면 핵심 칸(CORE_FIELDS)만 넣는다.
# 한글 낱말은 띄어쓰기를 지운 질문 안에 들어 있으면 맞고, 영어 낱말(op · ed …)은 단어 전체가 같아야 맞는다
FOCUS_WORDS: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (("감독", "연출"), frozenset({"director"})),
    (("제작사", "스튜디오", "제작"), frozenset({"main_studio", "studio"})),
    (("성우", "목소리", "더빙", "cv"), frozenset({"character", "voice_actor", "voice_actor_ko", "voiced"})),
    (("캐릭터", "주인공", "등장인물", "인물"), frozenset({"character", "appears_in"})),
    (("오프닝", "엔딩", "주제가", "노래", "곡", "가수", "부른", "불렀", "op", "ed", "ost"),
     frozenset({"theme_song", "theme_song_ja", "performer", "song_title_ja", "song_credit", "release_date",
                "sang", "used_in"})),
    (("몇화", "화수", "에피소드", "몇기", "몇편", "시즌"), frozenset({"episodes", "entry_count", "duration_min"})),
    (("언제", "방영", "개봉", "나와", "나왔", "나오", "출시", "다음화", "완결", "끝났", "끝나"),
     frozenset({"start_date", "end_date", "next_airing", "status", "release_date"})),
    (("원작", "작가", "만화", "소설", "라노벨", "웹툰"), frozenset({"source_material", "source_work", "staff"})),
    (("음악", "작곡", "작사", "편곡"), frozenset({"staff", "theme_song_ja", "song_credit"})),
    (("장르",), frozenset({"genre"})),
    (("어디서", "볼수", "보는곳", "스트리밍", "넷플릭스", "티빙", "라프텔", "왓챠", "디즈니", "방송사", "ott"),
     frozenset({"kr_ott", "broadcaster"})),
    (("등급", "관람가", "연령"), frozenset({"kr_rating"})),
    (("생일", "나이", "몇살", "혈액형", "태어", "출신", "고향"),
     frozenset({"birthday", "age", "blood_type", "birth_date", "person_blood_type", "home_town", "birthplace"})),
    (("공식", "사이트", "홈페이지", "트위터"), frozenset({"official_site"})),
    (("수상", "상받", "어워드"), frozenset({"award"})),
    (("맡은", "출연", "연기", "대표작", "필모"), frozenset({"voiced", "directed"})),
    (("만든작품", "작품목록", "작품들"), frozenset({"made", "directed", "voiced", "appears_in"})),
)
# 어떤 질문이든 넣는 칸 (시즌 · 극장판 수, 한국 공식 제목)
IDENTITY_FIELDS = frozenset({"entry_count", "kr_title"})
# 거꾸로 찾는 사실: 대상 종류 → (사전 칸, 줄 이름, 최대 개수)
REVERSE: dict[str, tuple[tuple[str, str, int], ...]] = {
    "character": (("character", "appears_in", 3),),
    "person": (("voice_actor", "voiced", 8), ("director", "directed", 5), ("performer", "sang", 5)),
    "studio": (("main_studio", "made", 8),),
    "song": (("theme_song", "used_in", 3),),
}
LABELS = {
    "entry_count": "시즌 · 극장판 수", "kr_title": "한국 공식 제목", "status": "방영 상태", "start_date": "방영 시작", "end_date": "방영 끝",
    "next_airing": "다음 방영", "episodes": "화수", "duration_min": "한 화 길이(분)",
    "main_studio": "애니메이션 제작사", "studio": "제작 참여사", "director": "감독", "staff": "제작진",
    "source_material": "원작 종류", "source_work": "원작", "genre": "장르", "character": "주요 캐릭터",
    "theme_song": "주제가", "theme_song_ja": "주제가 일본어 정보", "kr_ott": "국내 OTT",
    "kr_rating": "국내 관람등급", "broadcaster": "방송 · 배급", "official_site": "공식 사이트", "award": "수상",
    "gender": "성별", "birthday": "생일", "age": "나이", "blood_type": "혈액형", "voice_actor": "일본 성우",
    "voice_actor_ko": "한국 성우", "occupation": "직업", "person_gender": "성별", "birth_date": "생년월일",
    "death_date": "사망일", "home_town": "출신지", "birthplace": "출생지", "years_active": "활동 시작",
    "person_blood_type": "혈액형", "affiliation": "소속", "record_label": "레이블", "performer": "부른 사람",
    "song_title_ja": "일본어 원제", "song_credit": "크레딧", "release_date": "발매일", "release_type": "발매 형태",
    "founded": "설립", "headquarters": "본사", "founder": "설립자", "parent_org": "모회사",
    "appears_in": "나오는 작품", "voiced": "맡은 캐릭터", "directed": "감독한 작품", "sang": "부른 곡",
    "made": "만든 작품", "used_in": "쓰인 작품",
}
# 값이 하나여야 하는 칸: 출처끼리 값이 다르면 표시한다
SINGLE_FIELDS = frozenset({
    "status", "start_date", "end_date", "episodes", "duration_min", "source_material", "gender", "birthday",
    "age", "blood_type", "person_gender", "birth_date", "death_date", "person_blood_type", "release_date",
    "founded",
})
DATE_FIELDS = frozenset({"start_date", "end_date", "birthday", "birth_date", "death_date", "release_date", "founded"})
LINE_CAPS = {"genre": 6, "character": 6, "theme_song": 6, "theme_song_ja": 6, "staff": 6, "award": 3,
             "kr_ott": 3, "kr_rating": 3, "official_site": 2, "occupation": 4}
DEFAULT_CAP = 5
ENUM_TEXT = {
    "status": {"FINISHED": "방영 끝", "RELEASING": "방영 중", "NOT_YET_RELEASED": "방영 전",
               "CANCELLED": "취소", "HIATUS": "휴재"},
    "source_material": {"MANGA": "만화", "LIGHT_NOVEL": "라이트노벨", "NOVEL": "소설", "WEB_NOVEL": "웹소설",
                        "ORIGINAL": "오리지널", "VISUAL_NOVEL": "비주얼 노벨", "VIDEO_GAME": "게임",
                        "GAME": "게임", "WEB_MANGA": "웹 만화", "OTHER": "기타"},
    "gender": {"Male": "남성", "Female": "여성"},
    "person_gender": {"Male": "남성", "Female": "여성"},
}
FORMAT_TEXT = {"TV": "TV", "TV_SHORT": "TV 단편", "MOVIE": "극장판", "OVA": "OVA", "ONA": "ONA",
               "SPECIAL": "스페셜", "MUSIC": "뮤직비디오"}
ROLE_TEXT = {"MAIN": "주연", "SUPPORTING": "조연", "BACKGROUND": "단역"}
# 남기는 제작진 역할 (감독은 director 칸으로 따로 온다)
STAFF_ROLES = {"Original Creator": "원작자", "Original Story": "원작자", "Music": "음악",
               "Series Composition": "시리즈 구성", "Character Design": "캐릭터 디자인"}
VALUE_KEY_TEXT = {"title": "제목", "singer": "가수", "lyricist": "작사", "composer": "작곡",
                  "arranger": "편곡", "episodes": "회차"}
AUTHOR_ROLES = {"Story & Art": "글 · 그림", "Story": "글", "Art": "그림", "Illustration": "일러스트",
                "Original Creator": "원안"}


# ---------- 결과 모양 ----------

@dataclass(frozen=True)
class Hit:
    """질문 속 글자로 찾은 사전 대상 하나."""

    key: str  # 질문에서 맞은 글자 (보고 있는 글의 작품이면 빈 글자)
    whole: bool  # 별칭 전체와 맞았나 (False = 이름 일부와 맞음)
    entity_id: int
    kind: str
    parent_id: int | None
    name: str
    popularity: float
    adult: bool = False


@dataclass(frozen=True)
class Subject:
    """사실을 모으는 대상 (찾은 대상 + 작품이면 그 시즌 · 극장판)."""

    entity_id: int
    kind: str
    label: str


@dataclass(frozen=True)
class FactRow:
    subject_id: int
    field: str
    text: str
    source_name: str
    url: str | None


@dataclass(frozen=True)
class FactLine:
    """LLM 에 넘기는 사실 한 줄과 그 근거 (출처 이름, 근거 주소). subject 는 묶음 머리에 한 번만 쓴다."""

    subject: str
    text: str
    sources: tuple[tuple[str, str], ...]


@dataclass
class DictionaryResult:
    targets: list[Hit] = field(default_factory=list)
    lines: list[FactLine] = field(default_factory=list)
    failed: bool = False  # 사전이 켜져 있는데 조회가 실패함 (꺼져 있는 것과 구분)


# ---------- 글자 다루기 ----------

def name_key(value: str) -> str:
    """이름 비교용 글자: 대소문자 · 기호 · 띄어쓰기 무시 (RAG 사전의 norm 과 같은 생각)."""
    return _DROP.sub("", value.lower())


def strip_particle(word: str) -> str:
    for particle in PARTICLES:
        if word.endswith(particle) and len(word) > len(particle) + 1:
            return word[: -len(particle)]
    return word


def question_spans(question: str) -> dict[str, list[tuple[int, int]]]:
    """질문을 1~6 단어 묶음으로 잘라 비교용 글자 → 단어 위치 목록으로 돌려준다."""
    words = _WORD.findall(question.lower())
    spans: dict[str, list[tuple[int, int]]] = {}
    for start in range(len(words)):
        for end in range(start + 1, min(start + MAX_SPAN_WORDS, len(words)) + 1):
            part = words[start:end]
            for key in {"".join(part), "".join(part[:-1]) + strip_particle(part[-1])}:
                if len(key) >= MIN_KEY_LENGTH and key not in STOP_NAMES:
                    spans.setdefault(key, []).append((start, end))
    return spans


def wanted_fields(question: str) -> frozenset[str]:
    """질문에 맞는 칸만 고른다 (감독 → 감독, 성우 → 캐릭터 · 성우 …). 맞는 낱말이 없으면 핵심 칸."""
    joined = name_key(question)
    words = set(_WORD.findall(question.lower()))
    picked: set[str] = set()
    for keys, names in FOCUS_WORDS:
        if any((key in words) if key.isascii() else (key in joined) for key in keys):
            picked |= names
    return frozenset(picked | IDENTITY_FIELDS) if picked else CORE_FIELDS


def display_name(name_ko: str | None, alias_ko: str | None, native: str | None, romaji: str | None) -> str:
    korean = name_ko or alias_ko
    original = native or romaji
    if korean and original and korean != original:
        return f"{korean} ({original})"
    return korean or original or "이름 없음"


def _date_text(value: dict) -> str:
    year, month, day = value.get("year"), value.get("month"), value.get("day")
    parts = [f"{year}년" if year else "", f"{month}월" if month else "", f"{day}일" if day and month else ""]
    return " ".join(part for part in parts if part)


def value_text(value: Any) -> str:
    """사실 값(JSON)을 짧은 글자로 바꾼다."""
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (int, float, str)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(value_text(item) for item in value if item is not None)
    if not isinstance(value, dict):
        return ""
    if value and set(value) <= {"year", "month", "day"}:
        return _date_text(value)
    if "service" in value:  # 국내 OTT
        since = f", {value['since']}부터" if value.get("since") else ""
        return f"{value.get('service')} ({value.get('title')}{since})"
    if "grade" in value:  # 국내 관람등급
        return f"{value.get('grade')} ({value.get('title')})"
    if "airing_at" in value:
        return f"{value.get('episode')}화 {value.get('airing_at')}"
    if "authors" in value:  # 원작 정보: 글 · 그림 · 원안만 (번역 · 어시스턴트 · 편집은 뺀다)
        authors = ", ".join(
            f"{a.get('name')} ({AUTHOR_ROLES[a.get('role')]})" for a in value.get("authors") or []
            if isinstance(a, dict) and a.get("name") and a.get("role") in AUTHOR_ROLES
        )
        bits = [ENUM_TEXT["source_material"].get(str(value.get("format")), value.get("format")),
                ENUM_TEXT["status"].get(str(value.get("status")), value.get("status")),
                f"작가 {authors}" if authors else None]
        return " · ".join(str(bit) for bit in bits if bit)
    if "ko" in value or "en" in value:  # 위키데이터 항목 (방송사 · 소속 · 수상 · 본사)
        name = value.get("ko") or value.get("en") or ""
        return f"{name} ({value['year']})" if name and value.get("year") else name
    if "url" in value:
        return str(value["url"])
    head = _song_tag(value)  # 주제가: OP1 처럼 종류와 순서를 앞에 붙인다
    bits = [head] if head else []
    for key, item in value.items():
        if key in ("type", "sequence") and head:
            continue
        if item in (None, "", [], {}) or isinstance(item, (dict, list)):
            continue
        label = VALUE_KEY_TEXT.get(key)
        bits.append(f"{label} {item}" if label else str(item))
    return " · ".join(bits)


def _song_tag(value: Any) -> str:
    if not isinstance(value, dict) or not value.get("type"):
        return ""
    return f"{value['type']}{value.get('sequence') or ''}"


def _staff_role(value: Any) -> str | None:
    role = str(value.get("role") or "") if isinstance(value, dict) else ""
    return STAFF_ROLES.get(role.split(" (")[0].strip())  # "Music (eps 1-12)" → Music


# ---------- 대상 고르기 ----------

def _popular_enough(hit: Hit) -> bool:
    if hit.kind not in PERSON_KINDS or (hit.whole and len(hit.key) > 2):
        return True
    return hit.popularity >= MIN_POPULARITY


def _covered(key: str, matched: set[str], spans: dict[str, list[tuple[int, int]]]) -> bool:
    """질문 속 이 글자가 모두 더 긴 다른 이름 안에 들어 있으면 True (하나에 나츠키 안의 하나에)."""
    own = spans.get(key) or []
    others = [span for other in matched if other != key for span in spans.get(other, [])]
    return bool(own) and all(
        any(o[0] <= s[0] and s[1] <= o[1] and o[1] - o[0] > s[1] - s[0] for o in others) for s in own
    )


def choose_targets(hits: Sequence[Hit], spans: dict[str, list[tuple[int, int]]]) -> list[Hit]:
    usable = [hit for hit in hits if not hit.adult and _popular_enough(hit)]
    matched = {hit.key for hit in usable}
    usable = [hit for hit in usable if not _covered(hit.key, matched, spans)]
    work_ids = {hit.entity_id for hit in usable if hit.kind == "work"}
    usable = [hit for hit in usable if not (hit.kind == "entry" and hit.parent_id in work_ids)]

    chosen: list[Hit] = []
    for key in sorted({hit.key for hit in usable}, key=lambda k: (-len(k), k)):
        same = sorted((hit for hit in usable if hit.key == key), key=lambda hit: -hit.popularity)
        chosen += [hit for hit in same if hit.kind in TITLE_KINDS][:2]
        # 같은 글자의 캐릭터 · 인물 · 곡 · 스튜디오는 인기 제일 높은 하나만 (고죠 → 고죠 사토루)
        chosen += [hit for hit in same if hit.kind not in TITLE_KINDS][:1]
    return _unique(chosen)[:MAX_TARGETS]


def _unique(hits: Sequence[Hit]) -> list[Hit]:
    seen: set[int] = set()
    result = []
    for hit in hits:
        if hit.entity_id not in seen:
            seen.add(hit.entity_id)
            result.append(hit)
    return result


# ---------- 줄 만들기 ----------

def _merge_dates(values: dict[str, list[tuple[str, str | None]]]) -> dict[str, list[tuple[str, str | None]]]:
    """'2019년 4월' · '11월 15일' 처럼 덜 자세한 날짜는 더 자세한 날짜('2019년 4월 6일')에 합친다."""
    merged: dict[str, list[tuple[str, str | None]]] = {}
    for value, sources in sorted(values.items(), key=lambda item: -len(item[0])):
        wider = next((kept for kept in merged if kept.startswith(value) or kept.endswith(" " + value)), None)
        if wider is None:
            merged[value] = list(sources)
        else:
            merged[wider] += sources
    return {value: merged[value] for value in values if value in merged}


def _names(sources: list[tuple[str, str | None]]) -> list[str]:
    return list(dict.fromkeys(name for name, _ in sources))


def _tag(names: list[str]) -> str:
    agreed = f" {len(names)}곳 일치" if len(names) > 1 else ""
    return f"[{' · '.join(names)}{agreed}]"


def build_lines(subjects: Sequence[Subject], rows: Sequence[FactRow]) -> list[FactLine]:
    """대상 · 칸마다 한 줄로 묶는다. 출처가 같은 값을 몇 곳에서 확인했는지와 출처끼리 다른 값을 적는다."""
    candidates: list[tuple[bool, int, int, FactLine]] = []  # (핵심 아님, 대상 순서, 칸 순서, 줄)
    for subject_index, subject in enumerate(subjects):
        grouped: dict[str, dict[str, list[tuple[str, str | None]]]] = {}
        for row in rows:
            if row.subject_id == subject.entity_id and row.text:
                grouped.setdefault(row.field, {}).setdefault(row.text, []).append((row.source_name, row.url))
        order = [f for f in FIELDS.get(subject.kind, ()) if f not in LABEL_ONLY_FIELDS]
        order += [line for _, line, _ in REVERSE.get(subject.kind, ())]
        for field_index, name in enumerate(order):
            values = grouped.get(name)
            if not values:
                continue
            if name in DATE_FIELDS:
                values = _merge_dates(values)
            items = list(values.items())[: LINE_CAPS.get(name, DEFAULT_CAP)]
            name_sets = [_names(sources) for _, sources in items]
            if all(names == name_sets[0] for names in name_sets):
                body = " / ".join(value for value, _ in items) + " " + _tag(name_sets[0])
            else:
                body = " / ".join(f"{value} {_tag(names)}" for (value, _), names in zip(items, name_sets))
            if name in SINGLE_FIELDS and len(items) > 1:
                body += " (출처마다 값이 다름)"
            sources = tuple(dict.fromkeys(
                (source, url) for _, pairs in items for source, url in pairs if url
            ))
            line = FactLine(subject=subject.label, text=f"{LABELS.get(name, name)}: {body}", sources=sources)
            candidates.append((name not in CORE_FIELDS, subject_index, field_index, line))

    # 핵심 칸부터 자리를 주고, 남은 자리에 나머지 칸을 넣은 뒤 원래 순서로 되돌린다
    kept: list[tuple[int, int, FactLine]] = []
    used_chars = 0
    headers: set[int] = set()
    for _, subject_index, field_index, line in sorted(candidates, key=lambda c: c[:3]):
        cost = len(line.text) + (0 if subject_index in headers else len(line.subject))
        if len(kept) >= MAX_LINES:
            break
        if used_chars + cost > MAX_CHARS:
            continue
        kept.append((subject_index, field_index, line))
        used_chars += cost
        headers.add(subject_index)
    return [line for _, _, line in sorted(kept, key=lambda k: k[:2])]


# ---------- 사전 읽기 ----------

_engine_cache: AsyncEngine | None = None


def _engine() -> AsyncEngine | None:
    global _engine_cache
    if not settings.dict_database_url:
        return None
    if _engine_cache is None:
        _engine_cache = create_async_engine(
            settings.dict_database_url,
            # 읽기만 한다: 실수로 쓰기 문장이 가도 사전 DB 가 거절한다.
            # jit=off: 작은 조회인데 예상 비용이 커 보여서 JIT 준비에 1초 가까이 쓰는 걸 막는다.
            # force_custom_plan: 질문 글자 목록을 매번 값으로 넣어 빠른 목록 비교(해시)를 쓰게 한다
            connect_args={
                "options": "-c default_transaction_read_only=on -c statement_timeout=5000 -c jit=off "
                           "-c plan_cache_mode=force_custom_plan",
                "connect_timeout": 5,
            },
            pool_size=2,
            max_overflow=2,
            pool_pre_ping=True,
        )
    return _engine_cache


def _ko_alias(alias: str) -> str:
    return (
        f"(select a.alias from dict.alias a where a.entity_id = {alias}.entity_id and not a.is_candidate "
        f"and a.alias ~ '[가-힣]' order by a.source_id = 'menu_catalog' desc, length(a.alias) desc limit 1)"
    )


def _popularity(alias: str) -> str:
    return (
        f"coalesce((select max((p.value #>> '{{}}')::numeric) from dict.fact p "
        f"where p.entity_id = {alias}.entity_id "
        f"and p.field in ('character_favourites', 'person_favourites', 'popularity') "
        f"and jsonb_typeof(p.value) = 'number'), 0)"
    )


def _adult(alias: str) -> str:
    # 대상 번호로 바로 찾게 집계 모양으로 쓴다. exists 로 쓰면 사실 105만 줄을 통째로 훑는 계획이 나올 때가 있다
    return (
        f"coalesce((select bool_or(x.value = 'true'::jsonb) from dict.fact x "
        f"where x.entity_id = {alias}.entity_id and x.field = 'is_adult'), false)"
    )


# 작품(work) 인기는 그 시즌들 중 가장 높은 인기
_ENTITY_COLUMNS = f"""
    e.entity_id, e.kind, e.parent_id, e.name_ko, e.name_native, e.name_romaji,
    {_ko_alias('e')} as alias_ko,
    greatest({_popularity('e')}, coalesce((select max((p.value #>> '{{}}')::numeric)
        from dict.entity c join dict.fact p on p.entity_id = c.entity_id
        where c.parent_id = e.entity_id and p.field = 'popularity' and jsonb_typeof(p.value) = 'number'), 0)
    ) as popularity,
    {_adult('e')} as adult
"""

# 별칭 비교용 글자 식. backend/scripts/dictionary_indexes.sql 의 색인 식과 글자 하나까지 같아야 색인을 탄다
# (패턴을 바인드 값이 아니라 SQL 글자로 넣는 이유도 같다. _SQL_DROP 은 코드 상수라 따옴표가 없다)
_NAME_KEY = f"regexp_replace(lower(a.alias), '{_SQL_DROP}', '', 'g')"
_NAME_PIECES = "string_to_array(lower(a.alias), ' ')"

# whole: 질문 글자 목록과 별칭 전체 비교 (alias_name_key 색인)
# part: 질문 단어와 별칭 조각 비교 (alias_name_pieces 색인으로 후보를 좁힌 뒤 조각 글자를 정확히 맞춘다)
_HITS_SQL = text(f"""
with whole as (
    select {_NAME_KEY} as key, a.entity_id
    from dict.alias a
    where not a.is_candidate and {_NAME_KEY} = any(cast(:keys as text[]))
),
part as (
    select w.key, a.entity_id
    from dict.alias a
    join dict.entity pe on pe.entity_id = a.entity_id and pe.kind in ('character', 'person')
    cross join lateral unnest({_NAME_PIECES}) as piece
    join unnest(cast(:words as text[])) as w(key) on w.key = regexp_replace(piece, '{_SQL_DROP}', '', 'g')
    where not a.is_candidate and a.alias like '% %' and {_NAME_PIECES} && cast(:words as text[])
      and a.alias ~ '[가-힣]'
),
hit as (
    select key, entity_id, true as whole from whole
    union
    select key, entity_id, false as whole from part
)
select h.key, bool_or(h.whole) as whole, {_ENTITY_COLUMNS}
from hit h join dict.entity e on e.entity_id = h.entity_id
group by h.key, e.entity_id
""")

_ANCHOR_SQL = text(f"""
select '' as key, true as whole, {_ENTITY_COLUMNS}
from dict.entity e where e.menu_work_id = any(cast(:ids as int[]))
""")

_ENTRIES_SQL = text(f"""
select e.entity_id, e.parent_id, e.name_native, e.name_romaji, {_popularity('e')} as popularity
from dict.entity e
where e.kind = 'entry' and e.parent_id = any(cast(:ids as bigint[])) and not {_adult('e')}
""")

_FACTS_SQL = text(f"""
select f.entity_id, f.field, f.value, f.value_entity_id, s.name as source_name, f.source_url,
       v.name_ko as v_ko, {_ko_alias('v')} as v_alias, v.name_native as v_native, v.name_romaji as v_romaji
from dict.fact f
join dict.source s on s.source_id = f.source_id
left join dict.entity v on v.entity_id = f.value_entity_id
where f.entity_id = any(cast(:ids as bigint[])) and f.field = any(cast(:fields as text[]))
  and not (f.source_id = any(cast(:excluded as text[])))
order by f.entity_id, f.field, f.fact_id
""")

# 거꾸로: 대상이 값으로 들어 있는 사실 (성우 → 맡은 캐릭터, 캐릭터 → 나오는 시즌)
_REVERSE_SQL = text(f"""
select f.value_entity_id as target_id, f.field, f.value, s.name as source_name, f.source_url,
       subj.entity_id, subj.name_ko, {_ko_alias('subj')} as alias_ko, subj.name_native, subj.name_romaji,
       w.name_ko as w_ko, {_ko_alias('w')} as w_alias, w.name_native as w_native, w.name_romaji as w_romaji,
       {_popularity('subj')} as popularity
from dict.fact f
join dict.source s on s.source_id = f.source_id
join dict.entity subj on subj.entity_id = f.entity_id
left join dict.entity w on w.entity_id = subj.parent_id
where f.value_entity_id = any(cast(:ids as bigint[])) and f.field = any(cast(:fields as text[]))
  and not (f.source_id = any(cast(:excluded as text[]))) and not {_adult('subj')}
""")


def _hit(row: Any) -> Hit:
    return Hit(
        key=row["key"], whole=bool(row["whole"]), entity_id=int(row["entity_id"]), kind=row["kind"],
        parent_id=row["parent_id"],
        name=display_name(row["name_ko"], row["alias_ko"], row["name_native"], row["name_romaji"]),
        popularity=float(row["popularity"] or 0), adult=bool(row["adult"]),
    )


async def _rows(conn: AsyncConnection, sql: Any, **params: Any) -> list[Any]:
    return list((await conn.execute(sql, params)).mappings().all())


async def lookup(
    question: str, menu_work_ids: Sequence[int] = (), *, focus_text: str | None = None
) -> DictionaryResult:
    """질문 속 이름(+ 보고 있는 글의 작품)으로 사전 사실을 찾는다. 실패하면 빈 결과.

    넣을 칸은 focus_text(없으면 question)의 낱말로 고른다. 이어 묻기("그럼 몇 화?")는
    대상을 앞 질문에서 찾고 칸은 지금 질문에서 고르려고 둘을 나눠 받는다.
    """
    engine = _engine()
    if engine is None:
        return DictionaryResult()
    spans = question_spans(question[:MAX_LOOKUP_CHARS])
    wanted = wanted_fields(focus_text if focus_text is not None else question)
    try:
        async with engine.connect() as conn:
            anchors: list[Hit] = []
            if menu_work_ids:
                anchors = [_hit(row) for row in await _rows(conn, _ANCHOR_SQL, ids=list(menu_work_ids))]
            hits: list[Hit] = []
            if spans:
                words = [key for key, places in spans.items() if any(end - start == 1 for start, end in places)]
                # "-" 는 비교용 글자에 남지 않아서 아무것과도 안 맞는다 (빈 목록 대신)
                hits = [_hit(row) for row in await _rows(
                    conn, _HITS_SQL, keys=list(spans), words=words or ["-"]
                )]
            targets = _unique(anchors + choose_targets(hits, spans))[:MAX_TARGETS]
            if not targets:
                return DictionaryResult()
            return DictionaryResult(targets=targets, lines=await _collect(conn, targets, wanted))
    except Exception:  # 사전이 없거나 느려도 챗봇은 우리 글로 답한다
        logger.exception("dictionary lookup failed")
        return DictionaryResult(failed=True)


async def _collect(conn: AsyncConnection, targets: Sequence[Hit], wanted: frozenset[str]) -> list[FactLine]:
    work_ids = [t.entity_id for t in targets if t.kind == "work"]
    entries: dict[int, list[Any]] = {}
    if work_ids:
        for row in sorted(await _rows(conn, _ENTRIES_SQL, ids=work_ids), key=lambda r: -float(r["popularity"] or 0)):
            entries.setdefault(row["parent_id"], []).append(row)

    plan: list[tuple[int, str, str]] = []  # (대상 번호, 종류, 이름 앞부분)
    counted: list[FactRow] = []
    for target in targets:
        plan.append((target.entity_id, target.kind, target.name))
        found = entries.get(target.entity_id, [])
        if found and "entry_count" in wanted:
            note = f" (아래에는 인기 순 {MAX_ENTRIES}개만)" if len(found) > MAX_ENTRIES else ""
            counted.append(FactRow(target.entity_id, "entry_count", f"{len(found)}개{note}", "me;nu 정보사전", None))
        for entry in found[:MAX_ENTRIES]:
            entry_name = entry["name_native"] or entry["name_romaji"] or ""
            plan.append((int(entry["entity_id"]), "entry", f"{target.name} · {entry_name}"))
    kinds = {entity_id: kind for entity_id, kind, _ in plan}
    # 질문에 맞는 칸만 가져온다 (시즌 이름 옆 형식 · 연도는 늘 가져온다)
    fields = sorted({
        name for kind in kinds.values() for name in FIELDS[kind] if name in wanted or name in LABEL_ONLY_FIELDS
    })
    raw = [r for r in await _rows(conn, _FACTS_SQL, ids=list(kinds), fields=fields, excluded=list(EXCLUDED_SOURCES))
           if r["field"] in FIELDS[kinds[r["entity_id"]]]]

    # 시즌 이름 옆에 형식 · 연도 (TV, 2019)
    suffix: dict[int, list[str]] = {}
    for r in raw:
        if r["field"] == "format":
            suffix.setdefault(r["entity_id"], []).insert(0, FORMAT_TEXT.get(str(r["value"]), str(r["value"])))
        elif r["field"] == "season_year":
            suffix.setdefault(r["entity_id"], []).append(str(r["value"]))
    subjects = []
    for entity_id, kind, label in plan:
        bits = list(dict.fromkeys(suffix.get(entity_id, [])))
        subjects.append(Subject(entity_id, kind, f"{label} ({', '.join(bits)})" if bits else label))

    # 주연 캐릭터의 일본 성우, 주제가를 부른 사람
    main_characters = [r["value_entity_id"] for r in raw if r["field"] == "character"
                       and isinstance(r["value"], dict) and r["value"].get("role") == "MAIN"]
    songs = [r["value_entity_id"] for r in raw if r["field"] == "theme_song" and r["value_entity_id"]]
    voice: dict[int, str] = {}
    singers: dict[int, list[str]] = {}
    if main_characters or songs:
        for r in await _rows(conn, _FACTS_SQL, ids=main_characters + songs, fields=["voice_actor", "performer"],
                             excluded=list(EXCLUDED_SOURCES)):
            name = display_name(r["v_ko"], r["v_alias"], r["v_native"], r["v_romaji"])
            if r["field"] == "voice_actor" and (r["value"] or {}).get("lang", "ja") == "ja":
                voice.setdefault(r["entity_id"], name)
            elif r["field"] == "performer" and name not in singers.get(r["entity_id"], []):
                singers.setdefault(r["entity_id"], []).append(name)

    has_main_studio = {r["entity_id"] for r in raw if r["field"] == "main_studio"}
    rows: list[FactRow] = []
    for r in raw:
        name, value, ref = r["field"], r["value"], r["value_entity_id"]
        if name in LABEL_ONLY_FIELDS or (name == "studio" and r["entity_id"] in has_main_studio):
            continue
        if ref is not None:
            ref_name = display_name(r["v_ko"], r["v_alias"], r["v_native"], r["v_romaji"])
            if name == "character":
                if not (isinstance(value, dict) and value.get("role") == "MAIN"):
                    continue
                shown = f"{ref_name} — 성우 {voice[ref]}" if ref in voice else ref_name
            elif name == "theme_song":
                by = f" — {' · '.join(singers[ref])}" if singers.get(ref) else ""
                shown = f"{_song_tag(value)} {ref_name}{by}".strip()
            elif name == "staff":
                role = _staff_role(value)
                if role is None:
                    continue
                shown = f"{role} {ref_name}"
            else:
                shown = ref_name
        else:
            shown = ENUM_TEXT.get(name, {}).get(str(value)) or value_text(value)
        rows.append(FactRow(r["entity_id"], name, shown, r["source_name"], r["source_url"]))

    rows += counted + await _reverse_rows(conn, targets, wanted)
    return build_lines(subjects, rows)


async def _reverse_rows(conn: AsyncConnection, targets: Sequence[Hit], fields: frozenset[str]) -> list[FactRow]:
    wanted = [
        (target, spec) for target in targets for spec in REVERSE.get(target.kind, ()) if spec[1] in fields
    ]
    if not wanted:
        return []
    raw = await _rows(conn, _REVERSE_SQL, ids=sorted({t.entity_id for t, _ in wanted}),
                      fields=sorted({spec[0] for _, spec in wanted}), excluded=list(EXCLUDED_SOURCES))
    raw.sort(key=lambda r: -float(r["popularity"] or 0))

    picked: list[tuple[Hit, str, Any]] = []
    for target, (fact_field, line_field, cap) in wanted:
        seen: set[int] = set()
        for r in raw:
            if r["target_id"] != target.entity_id or r["field"] != fact_field or r["entity_id"] in seen:
                continue
            seen.add(r["entity_id"])
            picked.append((target, line_field, r))
            if len(seen) >= cap * 3:  # 같은 작품의 여러 시즌이 겹쳐도 cap 개를 채우게 넉넉히
                break

    # 맡은 캐릭터가 나오는 작품 이름
    voiced = sorted({r["entity_id"] for _, line, r in picked if line == "voiced"})
    work_of: dict[int, str] = {}
    if voiced:
        for r in sorted(await _rows(conn, _REVERSE_SQL, ids=voiced, fields=["character"],
                                    excluded=list(EXCLUDED_SOURCES)), key=lambda r: -float(r["popularity"] or 0)):
            work_of.setdefault(r["target_id"], _parent_name(r))

    rows: list[FactRow] = []
    counts: dict[tuple[int, str], set[str]] = {}
    caps = {(t.entity_id, line): cap for t, (_, line, cap) in wanted}
    for target, line, r in picked:
        subject = display_name(r["name_ko"], r["alias_ko"], r["name_native"], r["name_romaji"])
        work = _parent_name(r)
        if line == "appears_in":
            shown = f"{work} ({ROLE_TEXT.get((r['value'] or {}).get('role'), '역할 모름')})"
        elif line == "voiced":
            shown = f"{subject} — {work_of[r['entity_id']]}" if work_of.get(r["entity_id"]) else subject
        elif line == "used_in":
            shown = f"{work} {_song_tag(r['value'])}".strip()
        elif line in ("directed", "made"):
            shown = work
        else:
            shown = subject
        texts = counts.setdefault((target.entity_id, line), set())
        if shown in texts or len(texts) >= caps[(target.entity_id, line)]:
            continue
        texts.add(shown)
        rows.append(FactRow(target.entity_id, line, shown, r["source_name"], r["source_url"]))
    return rows


def _parent_name(row: Any) -> str:
    """시즌이면 위 작품 이름, 아니면 자기 이름."""
    if row["w_ko"] or row["w_alias"] or row["w_native"] or row["w_romaji"]:
        return display_name(row["w_ko"], row["w_alias"], row["w_native"], row["w_romaji"])
    return display_name(row["name_ko"], row["alias_ko"], row["name_native"], row["name_romaji"])
