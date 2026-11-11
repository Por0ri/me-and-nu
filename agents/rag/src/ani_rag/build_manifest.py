"""정보사전 → 기존 에이전트 입력(manifest) — 글감 고르기 · 사실 문장 · 그림을 코드로 묶는다 (시범 20개)

  uv run python -m ani_rag.build_manifest [--only curation,music]        (agents/rag 폴더에서)

- 구성: 큐레이션 5 · 음악 5 · 인물 5 · 캐릭터 5. 인기작에서 고르고 대표 작품이 겹치지 않게 한다
- 11/9 추가 (--only guide,krva,origin,place,boxoffice,award,bdaymonth): 입문 가이드 · 한국 성우 · 원작 소개 ·
  배경 장소 · 극장판 흥행 · 수상작 · 이달의 생일 (첫 시범 글감과 대표 작품이 겹치지 않게 고른다)
- LLM 은 문구만 쓴다. 무엇을 묶을지 · 어떤 사실인지 · 어떤 그림인지는 여기서 정한다
- 사실은 사전에 있는 것만. 작품 제목은 확정된 entity.name_ko, 인물 · 캐릭터는 name_ko 사실이나 확정 ko 별칭.
  한국어 이름이 없는 인물 · 곡은 원어 표기를 그대로 쓴다 (한글로 지어내지 않음)
- 줄거리 · 소개 글(*_ref)은 details 에 참고용으로만 넣는다. 사실 문장으로 옮기지 않는다
- 결과: .cache/trial/manifests/<request_id>.json · fixtures/<request_id>.json(오프라인 미리보기용 카피) ·
  images/<request_id>/ (글에 쓸 그림만)
- 요청 간격: Kitsu 1.5초, AniList 6초. 받은 응답은 .cache/trial/raw/ 에 저장해 다시 요청하지 않는다
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import pathlib
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from http.client import HTTPException

import psycopg

from ani_rag.anilist_api import post
from ani_rag.config import PACKAGE_ROOT, load_settings

TRIAL = PACKAGE_ROOT / ".cache" / "trial"
UA = "menu-dict/0.1 (school project)"
KITSU = "https://kitsu.io/api/edge/anime"
KITSU_PAUSE = 1.5
ANILIST_PAUSE = 6.0
MAX_FACTS = 30
ALL_TYPES = ("curation", "music", "people", "character")
NEW_TYPES = ("guide", "krva", "origin", "place", "boxoffice", "award", "bdaymonth")
NEW_KINDS = ("basic", "krva", "origin", "author", "place", "boxoffice", "award", "bdaymonth")
BIRTHDAY_MONTH = 11  # 이달의 생일 글을 만들 달
KST = dt.timezone(dt.timedelta(hours=9))
PUBLISHER = {"anilist": "AniList", "animethemes": "AnimeThemes", "syoboi": "쇼보이 캘린더",
             "musicbrainz": "MusicBrainz", "wikidata": "Wikidata", "onnada": "온나다",
             "kobis": "영화관입장권 통합전산망(KOBIS) 오픈 API", "kmrb": "영상물등급위원회", "kowiki": "한국어 위키백과",
             "enwiki": "영어 위키백과", "madb": "미디어예술 데이터베이스", "namuwiki": "나무위키"}
GENERIC_SOURCES = {"kobis"}  # 사실마다 주소가 같은 출처 (출처 이름에 작품 이름을 붙이지 않음)
# 출처는 사이트마다 무슨 정보를 썼는지만 짧게 (11/11 서원: 구구절절 쓰지 말고 간략하게, 그림 출처는 적지 않음)
SOURCE_LABEL = {("anilist", "work"): "AniList 작품 정보", ("anilist", "character"): "AniList 캐릭터 소개",
                ("anilist", "person"): "AniList 인물 정보", ("wikidata", "work"): "Wikidata 작품 정보",
                ("wikidata", "character"): "Wikidata 캐릭터 정보", ("wikidata", "person"): "Wikidata 인물 정보"}
SITE_LABEL = {"animethemes": "AnimeThemes 주제가 정보", "syoboi": "쇼보이 캘린더 주제가 정보",
              "musicbrainz": "MusicBrainz 음악 정보", "kobis": "영화관입장권 통합전산망(KOBIS) 한국 관객 수",
              "onnada": "온나다 캐릭터 정보", "kmrb": "영상물등급위원회 등급 정보", "kowiki": "한국어 위키백과",
              "enwiki": "영어 위키백과", "madb": "미디어예술 데이터베이스", "namuwiki": "나무위키 인물 · 제작사 정보"}
GENRE_KO = {"Action": "액션", "Adventure": "모험", "Comedy": "코미디", "Drama": "드라마", "Fantasy": "판타지",
            "Horror": "공포", "Mahou Shoujo": "마법소녀", "Mecha": "메카", "Music": "음악", "Mystery": "미스터리",
            "Psychological": "심리", "Romance": "로맨스", "Sci-Fi": "SF", "Slice of Life": "일상", "Sports": "스포츠",
            "Supernatural": "초자연", "Thriller": "스릴러"}
FORMAT_KO = {"TV": "TV 시리즈", "TV_SHORT": "TV 단편", "MOVIE": "극장판", "OVA": "OVA", "ONA": "ONA",
             "SPECIAL": "스페셜"}
TAG_KO = {
    "Male Protagonist": "남성 주인공", "Female Protagonist": "여성 주인공", "School": "학교", "Magic": "마법",
    "Primarily Female Cast": "여성 중심 출연진", "Ensemble Cast": "군상극", "Shounen": "소년만화",
    "Urban Fantasy": "도시 판타지", "Super Power": "초능력", "Primarily Teen Cast": "10대 중심 출연진",
    "Primarily Adult Cast": "성인 중심 출연진", "Coming of Age": "성장", "Urban": "도시", "Seinen": "청년만화",
    "Primarily Male Cast": "남성 중심 출연진", "Isekai": "이세계", "Episodic": "에피소드식", "Philosophy": "철학",
    "Swordplay": "검술", "Demons": "악마", "Parody": "패러디", "School Club": "동아리", "War": "전쟁",
    "Military": "군대", "Slapstick": "슬랩스틱", "Anti-Hero": "안티히어로", "Work": "직장",
    "Surreal Comedy": "초현실 코미디", "Politics": "정치", "Gore": "고어", "Found Family": "유사 가족",
    "Historical": "역사물", "Tsundere": "츤데레", "Crime": "범죄", "Guns": "총기", "Henshin": "변신",
    "Shoujo": "소녀만화", "Animals": "동물", "Family Life": "가족", "Iyashikei": "힐링", "Travel": "여행",
    "Medieval": "중세", "Love Triangle": "삼각관계", "Gods": "신", "Rural": "시골", "Mythology": "신화",
    "Aliens": "외계인", "Detective": "탐정", "Revenge": "복수", "Youkai": "요괴", "Survival": "생존",
    "Post-Apocalyptic": "포스트 아포칼립스", "Kuudere": "쿨데레", "Martial Arts": "무술", "Dystopian": "디스토피아",
    "Robots": "로봇", "Artificial Intelligence": "인공지능", "Vampire": "뱀파이어", "Space": "우주",
    "Otaku Culture": "오타쿠 문화", "Police": "경찰", "Food": "음식", "Video Games": "게임", "Assassins": "암살자",
    "Orphan": "고아", "Battle Royale": "배틀 로얄", "Amnesia": "기억 상실", "Royal Affairs": "왕실",
    "Dragons": "드래곤", "Virtual World": "가상 세계", "Idol": "아이돌", "Witch": "마녀",
    "Shapeshifting": "변신 능력", "Body Horror": "신체 공포", "Ghost": "유령", "Satire": "풍자",
    "Samurai": "사무라이", "Kaiju": "괴수", "Cyberpunk": "사이버펑크", "Religion": "종교", "Fugitive": "도망자",
    "Superhero": "슈퍼히어로", "Band": "밴드", "Conspiracy": "음모", "Cohabitation": "동거", "Curses": "저주",
    "Dungeon": "던전", "Twins": "쌍둥이", "Class Struggle": "계급 갈등", "Ninja": "닌자", "Bullying": "괴롭힘",
    "Teacher": "교사", "Elf": "엘프", "Death Game": "데스 게임", "Disability": "장애", "Unrequited Love": "짝사랑",
    "Terrorism": "테러", "Lost Civilization": "잃어버린 문명", "Gangs": "갱단", "Pirates": "해적", "Acting": "연기",
    "Hikikomori": "은둔형 외톨이", "Marriage": "결혼", "Angels": "천사", "Tragedy": "비극",
    "Time Skip": "시간 도약", "Time Manipulation": "시간 조작", "Time Loop": "시간 루프", "Psychological": "심리",
    "Reincarnation": "환생", "Yandere": "얀데레", "Tournament": "토너먼트", "Mafia": "마피아", "Alchemy": "연금술",
    "Yakuza": "야쿠자", "Delinquents": "불량배", "Basketball": "농구", "Baseball": "야구", "Volleyball": "배구",
    "Football": "축구", "Swimming": "수영", "Boxing": "권투", "Tennis": "테니스", "Cycling": "사이클",
    "Racing": "레이싱",
}
SONG_KIND = {"OP": "오프닝", "ED": "엔딩"}
# 소재 겹침 글의 장르 이름표 (11/11 서원: 표지와 맨 위 작은 글씨만 봐도 어떤 장르를 좋아하는 사람에게 권하는지 보이게)
# (이름, 작은 글씨, 맞출 소재, 맞출 장르, 참고 멘트 — 멘트는 그대로 베끼지 말고 글마다 새로 쓰게 한다)
OVERLAP_THEMES = (
    ("다크 판타지", "다크 판타지 추천", {"악마", "고어", "저주", "뱀파이어", "요괴", "신체 공포", "디스토피아"},
     {"판타지", "공포", "초자연"}, ("다크 판타지 좋아하는 사람 여기", "어둡고 잔혹한 세계가 당긴다면")),
    ("복수극", "복수극 추천", {"복수", "안티히어로"}, set(), ("복수극 끝까지 달리고 싶다면", "참았던 분노가 터지는 이야기")),
    ("칼싸움 액션", "칼싸움 액션 추천", {"검술", "사무라이", "닌자", "무술"}, {"액션"},
     ("칼 부딪히는 소리에 심장 뛰는 사람", "검 하나로 승부하는 이야기")),
    ("반전 · 두뇌 싸움", "반전 애니 추천", {"심리", "음모", "데스 게임", "탐정", "범죄"}, {"미스터리", "심리", "스릴러"},
     ("반전에 소름 돋고 싶다면", "머리 쓰는 싸움이 좋다면")),
    ("눈물 나는 이야기", "눈물 버튼 애니 추천", {"비극"}, {"드라마"}, ("눈물 흘리고 싶은 사람", "휴지 챙기고 보는 애니")),
    ("설렘 로맨스", "설렘 로맨스 추천", {"삼각관계", "짝사랑", "결혼", "동거", "츤데레", "쿨데레"}, {"로맨스"},
     ("설렘이 부족한 요즘이라면", "심장이 간질간질해지는 로맨스")),
    ("학원 청춘물", "학원 청춘물 추천", {"학교", "동아리", "성장", "10대 중심 출연진"}, {"일상"},
     ("교복 입은 청춘이 그리울 때", "학교 가는 길이 이야기가 되는")),
    ("이세계 판타지", "이세계 판타지 추천", {"이세계", "환생", "던전", "드래곤", "엘프", "마법", "가상 세계"}, {"판타지", "모험"},
     ("이세계로 떠나고 싶은 날", "검과 마법의 세계로")),
    ("SF · 로봇", "SF · 로봇 애니 추천", {"로봇", "우주", "인공지능", "사이버펑크", "외계인"}, {"SF", "메카"},
     ("로봇과 우주에 설레는 사람", "미래가 궁금해지는 이야기")),
    ("서바이벌", "서바이벌 애니 추천", {"생존", "배틀 로얄", "괴수", "포스트 아포칼립스"}, set(),
     ("살아남아야 끝나는 이야기", "숨 막히는 생존극이 당긴다면")),
    ("힐링 일상", "힐링 애니 추천", {"힐링", "음식", "시골", "가족", "동물"}, {"일상"},
     ("지친 하루 끝에 보는 애니", "보기만 해도 마음이 풀리는")),
    ("열혈 스포츠", "스포츠 애니 추천", {"농구", "야구", "배구", "축구", "수영", "권투", "테니스", "사이클", "레이싱"}, {"스포츠"},
     ("땀 냄새 나는 열혈 스포츠", "승부욕에 불붙는 애니")),
    ("소년 배틀", "배틀 소년만화 추천", {"소년만화", "초능력", "슈퍼히어로", "토너먼트", "고아"}, {"액션", "모험"},
     ("뜨거운 배틀물이 당긴다면", "주인공이 강해지는 맛")),
    ("시간 여행 · 루프", "타임루프 애니 추천", {"시간 루프", "시간 조작", "시간 도약"}, set(),
     ("시간을 되돌릴 수 있다면", "같은 하루가 반복된다면")),
    ("음악 · 아이돌", "음악 애니 추천", {"아이돌", "밴드"}, {"음악"}, ("노래로 이어지는 이야기", "무대 위의 반짝임")),
    ("코미디", "코미디 애니 추천", {"패러디", "슬랩스틱", "초현실 코미디", "풍자"}, {"코미디"},
     ("생각 없이 웃고 싶은 날", "웃다가 배 아픈 애니")),
)


def overlap_theme(shared_tags: list[list[str]], shared_genres: list[list[str]]) -> dict:
    """겹치는 소재 · 장르로 장르 이름표를 고른다. 맞는 게 없으면 가장 많이 겹친 소재 두 개로 만든다"""
    tag_count = Counter(t for tags in shared_tags for t in tags)
    genre_count = Counter(g for genres in shared_genres for g in genres)
    best, best_score = None, 0.0
    for label, eyebrow, tags, genres, hooks in OVERLAP_THEMES:
        score = 2 * sum(tag_count[t] for t in tags) + 0.5 * sum(genre_count[g] for g in genres)
        if score > best_score:
            best, best_score = (label, eyebrow, hooks), score
    if best and best_score >= 4:
        label, eyebrow, hooks = best
    else:
        label = " · ".join(t for t, _ in tag_count.most_common(2)) or "소재가 닮은 애니"
        eyebrow, hooks = f"{label} 애니 추천", ()
    return {"label": label, "eyebrow": eyebrow, "hooks": hooks}
QUOTA = {"like": 3, "overlap": 1, "studio": 1, "themes": 3, "artist": 2, "va": 3, "director": 2, "intro": 2,
         "bdaybook": 1, "birthday": 2, "basic": 2, "krva": 2, "origin": 1, "author": 1, "place": 2, "boxoffice": 3,
         "award": 2, "bdaymonth": 3, "profile": 2}
SPARE = 2  # 그림이 모자라 건너뛸 때를 대비한 예비 글감 수
# 대량 생산 모드 (--mass, 11/11): 시범용 좁은 범위(인기 상위 60 · 150 · 300편 등)를 상위 1,000편으로 넓히고,
# 다른 유형에서 쓴 작품도 다시 고를 수 있게 하고, 이달의 생일의 '한 작품에 한 명' 규칙을 푼다
MASS = False


def top(trial: int, mass: int = 1000) -> int:
    """고르는 범위: 시범이면 trial, 대량 모드면 mass"""
    return mass if MASS else trial


def has_batchim(word: str) -> bool:
    last = word.strip()[-1:] or " "
    return 0xAC00 <= ord(last) <= 0xD7A3 and (ord(last) - 0xAC00) % 28 != 0


def eun(w: str) -> str:
    return f"{w}{'은' if has_batchim(w) else '는'}"


def reul(w: str) -> str:
    return f"{w}{'을' if has_batchim(w) else '를'}"


def wa(w: str) -> str:
    return f"{w}{'과' if has_batchim(w) else '와'}"


def iga(w: str) -> str:
    return f"{w}{'이' if has_batchim(w) else '가'}"


def ida(w: str) -> str:
    return f"{w}{'이다' if has_batchim(w) else '다'}"


def plain(text: str | None, limit: int = 500) -> str:
    cleaned = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()
    return cleaned[:limit]


def ref_text(text: str | None, limit: int = 900) -> str:
    """참고 글: 스포일러 구간(~!...!~)과 꾸밈 표시를 지우고 줄인다 (문장을 옮기지 말고 새로 쓰라는 재료)"""
    cleaned = re.sub(r"~!.*?!~", " ", text or "", flags=re.S)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cleaned)
    return plain(re.sub(r"__|~~~|\*\*", "", cleaned), limit)


def date_ko(d: dict | None) -> str | None:
    if not d or not d.get("year"):
        return None
    parts = [f"{d['year']}년"]
    if d.get("month"):
        parts.append(f"{d['month']}월")
    if d.get("day"):
        parts.append(f"{d['day']}일")
    return " ".join(parts)


# ---------- 일본어 발음 → 한글 (팬 표기 관습: 어두 거센소리, 일본식 성-이름 순) ----------

KANA_ONE = {
    "ア": "아", "イ": "이", "ウ": "우", "エ": "에", "オ": "오", "カ": "카", "キ": "키", "ク": "쿠", "ケ": "케",
    "コ": "코", "ガ": "가", "ギ": "기", "グ": "구", "ゲ": "게", "ゴ": "고", "サ": "사", "シ": "시", "ス": "스",
    "セ": "세", "ソ": "소", "ザ": "자", "ジ": "지", "ズ": "즈", "ゼ": "제", "ゾ": "조", "タ": "타", "チ": "치",
    "ツ": "츠", "テ": "테", "ト": "토", "ダ": "다", "ヂ": "지", "ヅ": "즈", "デ": "데", "ド": "도", "ナ": "나",
    "ニ": "니", "ヌ": "누", "ネ": "네", "ノ": "노", "ハ": "하", "ヒ": "히", "フ": "후", "ヘ": "헤", "ホ": "호",
    "バ": "바", "ビ": "비", "ブ": "부", "ベ": "베", "ボ": "보", "パ": "파", "ピ": "피", "プ": "푸", "ペ": "페",
    "ポ": "포", "マ": "마", "ミ": "미", "ム": "무", "メ": "메", "モ": "모", "ヤ": "야", "ユ": "유", "ヨ": "요",
    "ラ": "라", "リ": "리", "ル": "루", "レ": "레", "ロ": "로", "ワ": "와", "ヲ": "오", "ヴ": "부",
}
KANA_TWO = {
    "キャ": "캬", "キュ": "큐", "キョ": "쿄", "シャ": "샤", "シュ": "슈", "ショ": "쇼", "チャ": "차", "チュ": "추",
    "チョ": "초", "ニャ": "냐", "ニュ": "뉴", "ニョ": "뇨", "ヒャ": "햐", "ヒュ": "휴", "ヒョ": "효", "ミャ": "먀",
    "ミュ": "뮤", "ミョ": "묘", "リャ": "랴", "リュ": "류", "リョ": "료", "ギャ": "갸", "ギュ": "규", "ギョ": "교",
    "ジャ": "자", "ジュ": "주", "ジョ": "조", "ビャ": "뱌", "ビュ": "뷰", "ビョ": "뵤", "ピャ": "퍄", "ピュ": "퓨",
    "ピョ": "표", "ティ": "티", "ディ": "디", "トゥ": "투", "ドゥ": "두", "ファ": "파", "フィ": "피", "フェ": "페",
    "フォ": "포", "ウィ": "위", "ウェ": "웨", "ウォ": "워", "ヴァ": "바", "ヴィ": "비", "ヴェ": "베", "ヴォ": "보",
    "シェ": "셰", "ジェ": "제", "チェ": "체", "イェ": "예", "ツァ": "차",
}
ROMA = {
    "a": "아", "i": "이", "u": "우", "e": "에", "o": "오", "ka": "카", "ki": "키", "ku": "쿠", "ke": "케",
    "ko": "코", "kya": "캬", "kyu": "큐", "kyo": "쿄", "ga": "가", "gi": "기", "gu": "구", "ge": "게", "go": "고",
    "gya": "갸", "gyu": "규", "gyo": "교", "sa": "사", "shi": "시", "si": "시", "su": "스", "se": "세", "so": "소",
    "sha": "샤", "shu": "슈", "sho": "쇼", "za": "자", "ji": "지", "zu": "즈", "ze": "제", "zo": "조", "ja": "자",
    "ju": "주", "jo": "조", "ta": "타", "chi": "치", "ti": "티", "tsu": "츠", "te": "테", "to": "토", "cha": "차",
    "chu": "추", "cho": "초", "da": "다", "de": "데", "do": "도", "na": "나", "ni": "니", "nu": "누", "ne": "네",
    "no": "노", "nya": "냐", "nyu": "뉴", "nyo": "뇨", "ha": "하", "hi": "히", "fu": "후", "hu": "후", "he": "헤",
    "ho": "호", "hya": "햐", "hyu": "휴", "hyo": "효", "ba": "바", "bi": "비", "bu": "부", "be": "베", "bo": "보",
    "bya": "뱌", "byu": "뷰", "byo": "뵤", "pa": "파", "pi": "피", "pu": "푸", "pe": "페", "po": "포", "pya": "퍄",
    "pyu": "퓨", "pyo": "표", "ma": "마", "mi": "미", "mu": "무", "me": "메", "mo": "모", "mya": "먀", "myu": "뮤",
    "myo": "묘", "ya": "야", "yu": "유", "yo": "요", "ra": "라", "ri": "리", "ru": "루", "re": "레", "ro": "로",
    "rya": "랴", "ryu": "류", "ryo": "료", "wa": "와", "wo": "오",
}
MACRON = str.maketrans({"ā": "aa", "ī": "ii", "ū": "uu", "ē": "ei", "ō": "ou"})
JONG_N, JONG_T = 4, 19  # 받침 ㄴ · ㅅ


def add_final(out: list[str], jong: int) -> None:
    """앞 글자에 받침을 붙인다 (이미 받침이 있거나 앞 글자가 없으면 그대로 둠)"""
    if out and 0xAC00 <= ord(out[-1]) <= 0xD7A3 and (ord(out[-1]) - 0xAC00) % 28 == 0:
        out[-1] = chr(ord(out[-1]) + jong)


def kana_to_hangul(text: str) -> str | None:
    """가나 이름 (가타카나 외래어 이름 포함) → 한글. 표에 없는 글자가 있으면 None"""
    s = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] in "・･ 　":
            out.append(" ")
            i += 1
        elif s[i:i + 2] in KANA_TWO:
            out.append(KANA_TWO[s[i:i + 2]])
            i += 2
        elif s[i] == "ー":
            i += 1
        elif s[i] in "ンッ":
            add_final(out, JONG_N if s[i] == "ン" else JONG_T)
            i += 1
        elif s[i] in KANA_ONE:
            out.append(KANA_ONE[s[i]])
            i += 1
        else:
            return None
    return "".join(out).strip() or None


def roma_to_hangul(token: str) -> str | None:
    """로마자 한 단어 → 한글. 해석 못 하는 글자가 있으면 None"""
    s = token.lower().translate(MACRON).replace("-", "")
    out: list[str] = []
    i = 0
    while i < len(s):
        nxt = s[i + 1:i + 2]
        if s[i] == "'":
            i += 1
        elif s[i] == "n" and (not nxt or nxt not in "aiueoy"):
            add_final(out, JONG_N)
            i += 1
        elif s[i] in "kstp" and nxt == s[i] or s[i:i + 3] == "tch":
            add_final(out, JONG_T)
            i += 1
        else:
            for size in (3, 2, 1):
                piece = s[i:i + size]
                if piece in ROMA:
                    out.append(ROMA[piece])
                    i += size
                    break
            else:
                return None
    return "".join(out) or None


def reading(native: str | None, romaji: str | None) -> str | None:
    """일본어 발음대로 적은 한글 이름. 가나 이름은 그 순서대로, 한자 이름은 로마자를 성-이름 순으로 바꿔서"""
    if native and re.fullmatch(r"[぀-ヿ・･ 　]+", native):
        return kana_to_hangul(native)
    tokens = [roma_to_hangul(x) for x in (romaji or "").split()]
    if not tokens or not all(tokens):
        return None
    return " ".join(reversed(tokens))


class Row:
    """사실 한 줄"""

    def __init__(self, r: tuple):
        self.entity, self.field, self.value, self.target, self.source, self.url, self.at, self.fact_id = r


class Ctx:
    """DB 에서 읽은 공통 재료"""

    def __init__(self, cur):
        self.cur = cur
        cur.execute("""select e.parent_id, e.entity_id, (f.value #>> '{}')::int from dict.fact f
                       join dict.entity e using (entity_id) where f.field = 'popularity' and e.kind = 'entry'""")
        self.entry_pop: dict[int, int] = {}
        self.entry_work: dict[int, int] = {}
        self.work_entries: dict[int, list[int]] = defaultdict(list)
        pop: dict[int, int] = defaultdict(int)
        for work, entry, p in cur.fetchall():
            self.entry_pop[entry], self.entry_work[entry] = p, work
            self.work_entries[work].append(entry)
            pop[work] = max(pop[work], p)
        for entries in self.work_entries.values():
            entries.sort(key=lambda e: -self.entry_pop[e])
        self.order = sorted(pop, key=lambda w: -pop[w])
        self.rank = {w: i + 1 for i, w in enumerate(self.order)}
        cur.execute("select entity_id, kind, name_ko, name_native, name_romaji, menu_work_id from dict.entity")
        self.ent: dict[int, tuple] = {r[0]: r[1:] for r in cur.fetchall()}
        self.title = {e: v[1] for e, v in self.ent.items() if v[0] == "work" and v[1]}
        self.menu_id = {e: v[4] for e, v in self.ent.items() if v[0] == "work"}
        self.title_entity = {v: k for k, v in self.title.items()}
        cur.execute("""select entity_id, value #>> '{}', source_id from dict.fact where field = 'name_ko'
                       order by case source_id when 'wikidata' then 0 when 'anilist' then 1 else 2 end desc""")
        self.ko: dict[int, str] = {e: v for e, v, _ in cur.fetchall() if v}
        cur.execute("select entity_id, alias from dict.alias where lang = 'ko' and not is_candidate")
        for e, alias in cur.fetchall():
            self.ko.setdefault(e, alias)
        cur.execute("""select x.source_id, x.id_kind, x.external_id, x.entity_id from dict.external_id x
                       where (x.source_id = 'kitsu' and x.id_kind = 'anime')
                          or (x.source_id = 'anilist' and x.id_kind in ('anime', 'character'))""")
        self.kitsu: dict[int, str] = {}
        self.anilist: dict[int, str] = {}
        for source, _kind, ext, e in cur.fetchall():
            (self.kitsu if source == "kitsu" else self.anilist)[e] = ext
        # 시즌 · 극장판별 한국어 제목 (작품 목록의 값. 극장판 이름을 쓸 때 필요)
        entry_of = {ext: e for e, ext in self.anilist.items() if self.ent[e][0] == "entry"}
        self.entry_ko: dict[int, str] = {}
        with open(load_settings().catalog_dir / "menu_work_entries.csv", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row["title_ko"] and row["anilist_id"] in entry_of:
                    self.entry_ko[entry_of[row["anilist_id"]]] = row["title_ko"]
        cur.execute("""select distinct e.parent_id from dict.fact f join dict.entity e using (entity_id)
                       where f.field = 'is_adult' and f.value = 'true'::jsonb and e.kind = 'entry'""")
        self.adult = {r[0] for r in cur.fetchall()}
        self.used: set[int] = set()
        self.phonetic: dict[int, str] = {}

    def rows(self, ids, fields) -> dict[tuple[int, str], list[Row]]:
        self.cur.execute("""select entity_id, field, value, value_entity_id, source_id, source_url, fetched_at, fact_id
                            from dict.fact where entity_id = any(%s) and field = any(%s)""", (list(ids), list(fields)))
        out: dict[tuple[int, str], list[Row]] = defaultdict(list)
        for r in self.cur.fetchall():
            row = Row(r)
            out[row.entity, row.field].append(row)
        for rows in out.values():  # 값이 여럿이면 AniList 를 먼저 (두 출처가 다를 때 AniList 를 쓰기로 함)
            rows.sort(key=lambda x: (x.source != "anilist", x.fact_id))
        return out

    def name(self, entity: int) -> str:
        """확정 한국어 이름 → 원어 → 로마자"""
        _, _, native, romaji, _ = self.ent[entity]
        return self.ko.get(entity) or native or romaji or ""

    def studio(self, entity: int) -> str:
        _, ko, native, romaji, _ = self.ent[entity]
        return ko or romaji or native or ""

    def char_name(self, entity: int) -> str | None:
        """캐릭터 이름: 확정 한국어가 있으면 그것, 없으면 일본어 발음대로 한글로 (발음 표기는 self.phonetic 에 남김)"""
        if entity in self.ko:
            return self.ko[entity]
        _, _, native, romaji, _ = self.ent[entity]
        name = reading(native, romaji)
        if name:
            self.phonetic[entity] = native or romaji
        return name

    def kitsu_entry(self, work: int) -> int | None:
        return next((e for e in self.work_entries[work] if e in self.kitsu), None)

    def ok_work(self, work: int) -> bool:
        return work in self.title and work not in self.adult and self.kitsu_entry(work) is not None

    def free(self, work: int) -> bool:
        return self.ok_work(work) and (MASS or work not in self.used)  # 대량 모드는 다른 유형과 작품이 겹쳐도 됨


class Deck:
    """글 하나 = 사실 · 출처 · 그림 계획"""

    def __init__(self, ctx: Ctx, kind: str, slug: str, content_type: str, topic: str, works: list[int],
                 cover: list[str], angle: str):
        self.ctx, self.kind, self.content_type, self.topic, self.works, self.cover, self.angle = (
            ctx, kind, content_type, topic, works, cover, angle)
        self.request_id = f"dict-{content_type}-{kind}-{slug}"
        self.facts: list[dict] = []
        self.sources: dict[str, dict] = {}
        self.plan: list[tuple] = []  # ("kitsu", 작품, 장면 수) · ("character", AniList 번호, 이름, 작품)
        self.images: list[dict] = []
        self.page_sources: list[dict] = []  # 사실의 근거 페이지가 곧 그림이 있는 페이지일 때 (캐릭터 페이지)
        self.glossary: list[dict] = []
        self.card_facts: list[str] = []  # 미리보기 카드에 넣을 사실 순서
        self.cover_mode, self.ending_mode, self.plan_reason = "", "", "정보사전 사실만으로 구성한 시범 글"
        self.feed_valid_until: str | None = None
        self.target_cards: int | None = None  # 카드 장수를 글감이 정할 때
        self.phonetic_names: dict[str, str] = {}
        # 장별 계획 (11/11 서원이 정한 카드 틀): 작은 글씨 · 큰 글씨 · 그림 · 쓸 사실. 그림은 받은 뒤 번호로 바꾼다
        self.card_plan: list[dict] = []
        self.resolved_plan: list[dict] = []
        self.logo_qid: str | None = None  # 제작사 글: 표지에 넣을 로고의 위키데이터 번호
        self._source_of: dict[tuple[str, str], str] = {}
        ctx.used.add(works[0])

    def cname(self, cid: int) -> str:
        """캐릭터 이름을 쓰면서, 발음 표기면 기록한다"""
        name = self.ctx.char_name(cid) or ""
        if cid in self.ctx.phonetic:
            self.phonetic_names[name] = self.ctx.phonetic[cid]
        _, _, native, romaji, _ = self.ctx.ent[cid]
        self.lock(native or romaji, name, "character")
        return name

    def pname(self, pid: int) -> str:
        """인물 이름을 쓰면서, 한국어 표기가 있으면 잠근다 (글쓰기에서 다른 표기로 바꾸지 못하게)"""
        name = self.ctx.name(pid)
        _, _, native, romaji, _ = self.ctx.ent[pid]
        if pid in self.ctx.ko:
            self.lock(native or romaji, name, "other")
        return name

    def lock(self, original: str | None, korean: str, category: str) -> None:
        """term_glossary 에 올린다. 생성기는 여기 없는 이름의 새 음차를 금지한다 (prompts.py 363~366행)"""
        if re.fullmatch(r"[가-힣\s]+", original or ""):  # 이미 한글인 이름(한국 성우)은 잠글 필요가 없다
            return
        if original == korean:  # ONE 처럼 원어 그대로 쓰는 이름은 번역할 것이 없다
            return
        if original and korean and all(g["korean"] != korean for g in self.glossary):
            self.glossary.append({"original": original, "korean": korean, "category": category})

    def page_source(self, row: Row) -> None:
        """사실의 근거 페이지를 출처에 더한다 (같은 페이지의 그림이 그 출처에 연결되게)"""
        self.source_for(row)

    def source_label(self, row: Row) -> str:
        """출처 이름 뒤에 붙일 대상 이름 (작품 · 캐릭터 · 인물)"""
        ctx = self.ctx
        if row.source in GENERIC_SOURCES or row.entity not in ctx.ent:
            return ""
        kind = ctx.ent[row.entity][0]
        if kind == "work":
            return f"〈{ctx.title.get(row.entity) or ctx.name(row.entity)}〉"
        if kind == "entry":
            work = ctx.entry_work.get(row.entity)
            return f"〈{ctx.entry_ko.get(row.entity) or ctx.title.get(work) or ctx.name(row.entity)}〉"
        if kind == "character":
            return ctx.char_name(row.entity) or ""
        return ctx.name(row.entity)

    def source_for(self, row: Row) -> str:
        """출처 한 줄 = 사이트 하나 + 쓴 정보 종류 하나 (예: 'AniList 작품 정보', 'AniList 캐릭터 소개').
        링크는 그 종류에서 처음 쓴 페이지. 11/11 서원: 출처가 있다는 게 중요하지만 구구절절 쓰지 말고 간략하게"""
        kind = self.ctx.ent[row.entity][0] if row.entity in self.ctx.ent else ""
        kind = {"entry": "work", "work": "work", "character": "character", "person": "person"}.get(kind, "work")
        key = (row.source, kind if row.source in ("anilist", "wikidata") else "")
        if key not in self._source_of:
            sid = f"{row.source}-{len(self._source_of) + 1}"
            publisher = PUBLISHER.get(row.source, row.source)
            title = SOURCE_LABEL.get((row.source, kind)) or SITE_LABEL.get(row.source) or f"{publisher} 정보"
            self._source_of[key] = sid
            self.sources[sid] = {
                "id": sid, "title": title, "url": row.url, "publisher": publisher, "is_official": False,
                "checked_at": row.at.isoformat() if row.at else None, "role": "reference", "region": "global"}
        return self._source_of[key]

    def page(self, *, work: int | None = None, image: tuple | None = None, eyebrow: str | None = None,
             title: list[str] | None = None, prefix: str | None = None, original: str | None = None,
             facts=(), note: str = "", body: list[str] | None = None, extra: int | None = None) -> None:
        """장 하나의 계획. title 은 그대로 쓰는 큰 글씨, prefix 는 노래 카드의 첫 줄 (둘째 줄은 「노래 제목」).
        body 는 코드가 정하는 본문 앞 문장, extra 는 그 뒤에 글쓰기가 붙일 수 있는 문장 수 (0 이면 없음)"""
        self.card_plan.append({"work": work, "image": image, "eyebrow": eyebrow, "title_lines": title,
                               "title_prefix": prefix, "song_original": original,
                               "fact_ids": [f for f in facts if f], "note": note,
                               "body_head": body, "body_extra_max": extra})

    def fact(self, statement: str, row: Row, fact_type: str = "general", **details) -> str | None:
        if len(self.facts) >= MAX_FACTS:
            return None
        sid = self.source_for(row)
        fid = f"f{len(self.facts) + 1}"
        self.facts.append({"id": fid, "statement": statement, "source_ids": [sid], "verified": True,
                           "fact_type": fact_type,
                           "details": {"dict_fact_id": row.fact_id, "source_url": row.url, **details}})
        return fid

    def manifest(self) -> dict:
        ctx = self.ctx
        for fact in self.facts:
            for title in re.findall(r"〈([^〉]+)〉", fact["statement"]):
                if title in ctx.title_entity:
                    _, _, native, romaji, _ = ctx.ent[ctx.title_entity[title]]
                    self.lock(native or romaji, title, "other")  # work_title 이면 표지에 전부 넣으라는 검사에 걸림
            used = {n: o for n, o in self.phonetic_names.items() if n in fact["statement"]}
            if used:
                fact["details"]["phonetic_names"] = used  # 확정 한국어 이름이 아니라 일본어 발음대로 적은 이름
        evidence = []
        basis = "dictionary" if self.content_type == "curation" and len(self.works) > 1 else "structured_about"
        for work in self.works:
            entries = ctx.work_entries[work]
            aid = ctx.anilist.get(entries[0]) if entries else None
            evidence.append({"menu_work_id": ctx.menu_id[work], "basis": basis, "matched_value": ctx.title[work],
                             "evidence_url": f"https://anilist.co/anime/{aid}", "confidence": 1.0})
        return {
            "request_id": self.request_id, "topic": self.topic, "content_kind": "information",
            "content_type": self.content_type, "menu_work_ids": [ctx.menu_id[w] for w in self.works],
            "work_match_evidence": evidence, "audience_region": "KR", "region_policy": "kr_first",
            "editorial_angle": self.angle, "content_plan_reason": self.plan_reason, "cover_mode": self.cover_mode,
            "ending_mode": self.ending_mode, "target_cards": self.target_cards, "term_glossary": self.glossary,
            "feed_valid_until": self.feed_valid_until,
            "sources": self.all_sources(), "facts": self.facts, "images": self.images,
            "card_plan": self.resolved_plan, "cards": [],
            "prior_image_fingerprints": [], "status": "draft"}

    def all_sources(self) -> list[dict]:
        """앱 상세 화면 맨 아래 '출처' 칸에 그대로 나오는 목록. 11/11 서원: 그림 출처는 적지 않는다"""
        return [*self.sources.values(), *self.page_sources]

    def image_sources(self) -> list[dict]:
        """(지금은 안 씀) 그림을 가져온 페이지 목록. 다시 적기로 하면 all_sources 에 더한다"""
        sources = [*self.sources.values(), *self.page_sources]
        urls = {s["url"] for s in sources}
        for img in self.images:
            if img["id"].startswith("collage-"):
                continue  # 바둑판 그림은 포스터들을 모은 것이라 포스터 출처로 충분
            page = img["source_url"]
            found = re.match(re.escape(KITSU) + r"/(\d+)", page)
            if found:  # 그림 주소는 Kitsu API 라서 사람이 보는 작품 페이지로 바꿔 보여 준다
                page = f"https://kitsu.app/anime/{found.group(1)}"
            if page in urls:
                continue
            urls.add(page)
            work = next((w for w in self.works if [self.ctx.menu_id[w]] == img["menu_work_ids"]), None)
            what = {"catalog_poster": "포스터", "catalog_cover": "포스터", "scene_still": "장면",
                    "character_art": "캐릭터 그림"}.get(img["asset_kind"], "그림")
            if img["asset_kind"] == "character_art" and img["depicted_entities"]:
                label = f"{img['depicted_entities'][0]} {what}"
            else:
                label = f"〈{self.ctx.title[work]}〉 {what}" if work is not None else what
            publisher = img["provenance_source"]["publisher"]
            sources.append({"id": f"image-{len(sources) + 1}", "title": f"{publisher} · {label}", "url": page,
                            "publisher": publisher, "is_official": False, "checked_at": img["rights_checked_at"],
                            "role": "reference", "region": "global"})
        return sources

    def fixture(self) -> dict:
        """오프라인 미리보기용 카피 (사실 문장을 그대로 잘라 넣음, LLM 문구 아님)"""
        n = self.target_cards or max(3, min(5, len(self.images)))
        facts = self.facts
        cards = [{"page": 1, "role": "cover", "eyebrow": "미리보기", "title_lines": self.cover,
                  "fact_ids": [facts[0]["id"]], "image_id": self.images[0]["id"],
                  "menu_work_ids": [self.ctx.menu_id[self.works[0]]]}]
        step = max(1, len(facts) // max(1, n - 2))
        by_id = {f["id"]: f for f in facts}
        if self.card_facts:
            cards[0]["fact_ids"] = [self.card_facts[0]]
        for page in range(2, n):
            if self.card_facts:
                fact = by_id[self.card_facts[min(page - 1, len(self.card_facts) - 1)]]
            else:
                fact = facts[min((page - 2) * step, len(facts) - 1)]
            cards.append({"page": page, "role": "detail", "eyebrow": "미리보기",
                          "title_lines": [f"{page - 1}번째 이야기"],
                          "body_sentences": [fact["statement"][:58]], "fact_ids": [fact["id"]],
                          "image_id": self.images[(page - 1) % len(self.images)]["id"]})
        cards.append({"page": n, "role": "ending", "eyebrow": "미리보기", "title_lines": ["오프라인 미리보기 끝"],
                      "body_sentences": [facts[-1]["statement"][:58]], "fact_ids": [facts[-1]["id"]],
                      "image_id": self.images[(n - 1) % len(self.images)]["id"]})
        return {"content_type": self.content_type, "editorial_angle": "오프라인 레이아웃 확인용 카피",
                "cover_mode": "declarative", "ending_mode": "declarative", "cards": cards}


# ---------- 작품 요약 사실 ----------

WORK_FIELDS = ("format", "start_year", "episodes", "main_studio", "genre", "synopsis_en_ref", "average_score", "tag")


def add_work_summary(deck: Deck, work: int, rows, *, with_episodes: bool = True) -> list[str]:
    """작품 요약 사실들. 만든 사실 번호를 돌려준다 (장별 계획에서 그 작품 카드에 연결)"""
    ctx = deck.ctx
    entry = ctx.work_entries[work][0]
    title = ctx.title[work]
    made: list[str | None] = []
    synopsis = rows.get((entry, "synopsis_en_ref"), [])
    ref = {"synopsis_reference_do_not_copy": plain(synopsis[0].value)} if synopsis else {}
    years, formats = rows.get((entry, "start_year"), []), rows.get((entry, "format"), [])
    if years and formats:
        kind = FORMAT_KO.get(formats[0].value, formats[0].value)
        head = f"〈{title}〉{'은' if has_batchim(title) else '는'}"
        made.append(deck.fact(f"{head} {years[0].value}년에 시작한 {ida(kind)}.", years[0], **ref))
        ref = {}
    studios = [r for r in rows.get((entry, "main_studio"), []) if r.target]
    if studios:
        made.append(deck.fact(f"〈{title}〉의 메인 제작사는 {ida(ctx.studio(studios[0].target))}.", studios[0]))
    genres = [GENRE_KO[r.value] for r in rows.get((entry, "genre"), []) if r.value in GENRE_KO]
    genre_row = rows.get((entry, "genre"), [])
    if genres:
        made.append(deck.fact(f"〈{title}〉의 장르는 {', '.join(genres[:3])}다.", genre_row[0], **ref))
    episodes = rows.get((entry, "episodes"), [])
    if with_episodes and episodes and formats and formats[0].value in ("TV", "TV_SHORT", "ONA"):
        made.append(deck.fact(f"〈{title}〉의 해당 시즌은 총 {episodes[0].value}화다.", episodes[0]))
    score = rows.get((entry, "average_score"), [])
    if score:
        made.append(deck.fact(f"〈{title}〉의 AniList 평균 평점은 100점 만점에 {score[0].value}점이다.", score[0],
                              "score"))
    tag_rows = [r for r in rows.get((entry, "tag"), []) if r.value.get("name") in TAG_KO and not r.value.get("spoiler")
                and not r.value.get("adult") and (r.value.get("rank") or 0) >= 70]
    tag_rows.sort(key=lambda r: -r.value["rank"])
    if tag_rows:
        names = ", ".join(TAG_KO[r.value["name"]] for r in tag_rows[:5])
        made.append(deck.fact(f"〈{title}〉의 주요 소재 · 키워드는 {ida(names)}.", tag_rows[0], "tag"))
    return [f for f in made if f]


def entries_of(ctx: Ctx, works: list[int]) -> list[int]:
    return [ctx.work_entries[w][0] for w in works]


# ---------- 큐레이션 ----------

def pick_curation(ctx: Ctx) -> list[Deck]:
    cur = ctx.cur
    cur.execute("""select entity_id, value_entity_id, (value->>'rating')::int, source_url, fact_id, fetched_at
                   from dict.fact where field = 'recommendation' and value_entity_id is not null""")
    rec: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    rec_row: dict[tuple[int, int], tuple] = {}
    for e, t, r, url, fid, at in cur.fetchall():
        a, b = ctx.entry_work.get(e), ctx.entry_work.get(t)
        if a and b and a != b and (r or 0) > 0:
            rec[a][b] += r
            rec_row.setdefault((a, b), (a, "recommendation", {"rating": r}, b, "anilist", url, at, fid))
    decks: list[Deck] = []
    bands = ((0, 10), (10, 30), (30, 60), (60, 100), (100, 150)) if not MASS else [(i, i + 1) for i in range(1000)]
    for start, stop in bands:
        work = next((w for w in ctx.order[start:stop] if ctx.free(w)
                     and len([b for b in rec[w] if ctx.ok_work(b)]) >= 4), None)
        if work is None:
            continue
        picks = sorted((b for b in rec[work] if ctx.ok_work(b)), key=lambda b: -rec[work][b])[:4]
        title = ctx.title[work]
        obj = '을' if has_batchim(title) else '를'
        d = Deck(ctx, "like", f"w{ctx.menu_id[work]}", "curation", f"〈{title}〉{obj} 좋아한다면 볼 만한 작품",
                 [work, *picks], [f"〈{title}〉", "다음엔 뭘 볼까"],
                 f"〈{title}〉{obj} 좋아한 사람들이 많이 추천한 다음 작품을 소개하는 글. 다른 팬들의 선택이라는 느낌을 "
                 f"살린다 (예: '〈{title}〉 좋아한 사람들이 입 모아 추천한'). 시청 기록이 아니라 추천 기록이라서 "
                 "'본 사람들이 보는'처럼 쓰지 않는다. 작품 카드는 그 작품이 어떤 이야기인지 짧게 소개하고, 점수는 쓰지 않는다")
        rows = ctx.rows(entries_of(ctx, picks), WORK_FIELDS)
        items = []
        for rank, b in enumerate(picks, 1):
            subject = '이' if has_batchim(ctx.title[b]) else '가'
            # 점수는 글에 쓰지 않도록 문장에서 빼고 details 에만 둔다 (11/10 서원: "추천 점수 1863점" 이 어색함)
            fid = d.fact(f"〈{title}〉{obj} 좋아한 사람들에게 많이 추천된 작품 순위에서 〈{ctx.title[b]}〉{subject} "
                         f"{rank}위다.", Row(rec_row[(work, b)]), "user_recommendation",
                         recommendation_rating=rec[work][b])
            items.append((rank, b, [fid, *add_work_summary(d, b, rows)]))
        # 11/11 서원: 협업 필터링처럼 "다른 팬들은 이걸 골랐다" 가 느껴지게
        d.page(work=work, image=("work", work), eyebrow=f"〈{title}〉 팬들의 추천", facts=[items[0][2][0]],
               note=f"큰 글씨: 〈{title}〉 좋아한 사람들이 고른 다음 작품이라는 게 바로 보이는 멘트")
        for rank, b, fids in items:
            d.page(work=b, image=("work", b), eyebrow=f"〈{title}〉 팬 추천 {rank}위", title=[ctx.title[b]], facts=fids,
                   note="이 작품이 어떤 이야기인지 한두 문장 (줄거리 참고 글을 새로 씀)")
        d.plan = [("kitsu", w, 0) for w in [work, *picks]] + [("kitsu", work, 2)]
        decks.append(d)
    decks += pick_overlap(ctx) + pick_studio(ctx)
    return decks


def pick_overlap(ctx: Ctx) -> list[Deck]:
    """기준 작품과 태그 · 장르가 가장 많이 겹치는 인기작을 고른다 (AniList 추천이 아니라 겹침 기준)"""
    works = [w for w in ctx.order[:top(300)] if ctx.ok_work(w)]
    rep_entry = {ctx.work_entries[w][0]: w for w in works}
    rows = ctx.rows(rep_entry, ("tag", "genre"))
    tags: dict[int, dict[str, Row]] = defaultdict(dict)
    genres: dict[int, set[str]] = defaultdict(set)
    for (entry, field), items in rows.items():
        work = rep_entry[entry]
        for row in items:
            if field == "genre" and row.value in GENRE_KO:
                genres[work].add(GENRE_KO[row.value])
            elif field == "tag" and row.value.get("name") in TAG_KO and (row.value.get("rank") or 0) >= 70 \
                    and not row.value.get("spoiler") and not row.value.get("adult"):
                tags[work][TAG_KO[row.value["name"]]] = row
    decks: list[Deck] = []
    for base in ctx.order[:top(60)]:
        if len(decks) == QUOTA["overlap"] + SPARE:
            break
        if not ctx.free(base) or len(tags[base]) < 5:
            continue
        near = [b for b in works if b != base and len(tags[base].keys() & tags[b].keys()) >= 2]
        chosen = sorted(near, key=lambda b: (-(2 * len(tags[base].keys() & tags[b].keys())
                                               + len(genres[base] & genres[b])), ctx.rank[b]))[:5]
        if len(chosen) < 4:
            continue
        title = ctx.title[base]
        wa_ = "과" if has_batchim(title) else "와"
        shared_of = {b: sorted(tags[base].keys() & tags[b].keys(), key=lambda t: -tags[b][t].value["rank"])
                     for b in chosen}
        same_of = {b: sorted(genres[base] & genres[b]) for b in chosen}
        theme = overlap_theme(list(shared_of.values()), list(same_of.values()))
        hooks = f" (참고 멘트: {' / '.join(theme['hooks'])} — 그대로 베끼지 말고 이 글에 맞게)" if theme["hooks"] else ""
        d = Deck(ctx, "overlap", f"w{ctx.menu_id[base]}", "curation", f"〈{title}〉{wa_} 소재가 겹치는 작품들",
                 [base, *chosen], [f"〈{title}〉", "소재가 겹치는 작품"],
                 f"'{theme['label']}' 을 좋아하는 사람에게 〈{title}〉{wa_} 함께 권하는 글. 표지 큰 글씨만 봐도 "
                 f"'{theme['label']}' 추천이라는 게 바로 보여야 한다{hooks}. 작품 카드는 그 작품이 어떤 이야기인지 "
                 "짧게 소개하고, 겹치는 소재를 카드마다 반복하지 않는다")
        summaries = ctx.rows(entries_of(ctx, chosen), WORK_FIELDS)
        items = []
        for b in chosen:
            shared, same = shared_of[b], same_of[b]
            other = ctx.title[b]
            head = f"〈{other}〉{'은' if has_batchim(other) else '는'}"
            text = f"{head} 〈{title}〉{wa_} 소재({', '.join(shared[:4])})가 겹친다."
            if same:
                text += f" 장르({', '.join(same[:2])})도 같다."
            fid = d.fact(text, tags[b][shared[0]], "tag_overlap", shared_tags=shared, shared_genres=same)
            items.append((b, [fid, *add_work_summary(d, b, summaries, with_episodes=False)]))
        d.page(work=base, image=("work", base), eyebrow=theme["eyebrow"], facts=[items[0][1][0]],
               note=f"큰 글씨: '{theme['label']}' 을 좋아하는 사람에게 권하는 글이라는 게 바로 보이는 멘트 + 〈{title}〉")
        for b, fids in items:
            d.page(work=b, image=("work", b), eyebrow=theme["eyebrow"], title=[ctx.title[b]], facts=fids,
                   note="이 작품이 어떤 이야기인지 한두 문장. 겹치는 소재는 반복하지 않는다")
        d.plan = [("kitsu", w, 0) for w in [base, *chosen]] + [("kitsu", base, 2)]
        decks.append(d)
    return decks


def pick_studio(ctx: Ctx) -> list[Deck]:
    cur = ctx.cur
    cur.execute("""select entity_id, value_entity_id, source_url, fact_id, fetched_at from dict.fact
                   where field = 'main_studio' and source_id = 'anilist' and value_entity_id is not null""")
    by: dict[int, dict[int, tuple]] = defaultdict(dict)
    for e, sid, url, fid, at in cur.fetchall():
        work = ctx.entry_work.get(e)
        if work and ctx.ok_work(work) and ctx.rank[work] <= top(150) and e == ctx.work_entries[work][0]:
            by[sid][work] = (e, "main_studio", {}, sid, "anilist", url, at, fid)
    decks: list[Deck] = []
    for sid in sorted(by, key=lambda s: -len(by[s])):
        if len(decks) == QUOTA["studio"] + SPARE:
            break
        works = sorted(by[sid], key=lambda w: ctx.rank[w])
        lead = next((w for w in works if ctx.free(w)), None)
        if lead is None or len(works) < 5:
            continue
        chosen = [lead, *[w for w in works if w != lead][:4]]
        name = ctx.studio(sid)
        d = Deck(ctx, "studio", f"s{sid}", "curation", f"{name} 대표작 모음", chosen,
                 [f"{name}", "대표작 다섯 편"],
                 f"이 작품들이 다 {name}에서 만든 작품이라는 발견을 주는 글. 한 장에 한 작품씩, 어떤 이야기인지 "
                 "한 줄로 말하고 장르 목록은 읊지 않는다")
        rows = ctx.rows(entries_of(ctx, chosen), WORK_FIELDS)
        items = []
        for w in chosen:
            fid = d.fact(f"〈{ctx.title[w]}〉의 메인 제작사는 {ida(name)}.", Row(by[sid][w]), "studio")
            items.append((w, [fid, *add_work_summary(d, w, rows, with_episodes=False)]))
        # 11/11 서원: 제작사인지 바로 알게 '제작사 ○○ 작품 모음', 큰 글씨 제목은 〈〉, 표지는 제작사 로고
        eyebrow = f"제작사 {name} 작품 모음"
        cur.execute("select external_id from dict.external_id where source_id = 'wikidata' and entity_id = %s", (sid,))
        found = cur.fetchone()
        d.logo_qid = found[0] if found else None
        # 표지 (11/11 두 번째 의견): 작은 글씨 없이 큰 글씨 '〈대표작〉 제작사 / ○○ 작품들', 본문은 제작사 특징
        about = ctx.rows([sid], ("founded", "headquarters"))
        traits = []
        founded = about.get((sid, "founded"))
        if founded and isinstance(founded[0].value, dict) and founded[0].value.get("year"):
            traits.append(d.fact(f"{eun(name)} {founded[0].value['year']}년에 세워진 애니메이션 제작사다.", founded[0],
                                 "studio_info"))
        place = about.get((sid, "headquarters"))
        if place and isinstance(place[0].value, dict) and place[0].value.get("ko"):
            traits.append(d.fact(f"{name}의 본사는 {place[0].value['ko']}에 있다.", place[0], "studio_info"))
        namu = ctx.rows([sid], ("namu_profile_ref",)).get((sid, "namu_profile_ref"))
        if namu:  # 나무위키 개요 · 특징 문단 (11/11 서원: 제작사의 특징을 표지에)
            traits.append(d.fact(f"{name}의 소개는 details 의 참고 글에 있다. 문장을 옮기지 말고 제작사의 특징을 새로 쓴다.",
                                 namu[0], "studio_reference",
                                 studio_reference_do_not_copy=ref_text(namu[0].value.get("text", ""), 1200)))
        # 제작사의 결: 이 제작사 인기작들에서 가장 많은 장르 (사전에서 센 값)
        genre_rows = ctx.rows(entries_of(ctx, works), ("genre",))
        counts = Counter(GENRE_KO[r.value] for rs in genre_rows.values() for r in rs if r.value in GENRE_KO)
        if counts and genre_rows:
            top3 = ", ".join(g for g, _ in counts.most_common(3))
            traits.append(d.fact(f"인기 애니 중 {iga(name)} 만든 {len(works)}편에서 가장 많은 장르는 {ida(top3)}.",
                                 next(iter(genre_rows.values()))[0], "studio_info"))
        d.page(work=lead, image=("collage",), eyebrow="", title=[f"〈{ctx.title[lead]}〉 제작사", f"{name} 작품들"],
               facts=[*traits, items[0][1][0]],
               note=("본문: 이 제작사의 특징을 한 문장 (세워진 해 · 본사, 여기 모은 작품들의 장르 · 분위기에서 보이는 결 — "
                     "사실에 있는 만큼만, 지어내지 않는다)"))
        for w, fids in items:
            d.page(work=w, image=("work", w), eyebrow=eyebrow, title=[f"〈{ctx.title[w]}〉"], facts=fids,
                   note="이 작품이 어떤 이야기인지 한두 문장. 제작사 이름은 카드마다 반복하지 않는다")
        d.plan = [("kitsu", w, 0) for w in chosen] + [("kitsu", lead, 2)]
        decks.append(d)
    return decks


# ---------- 음악 ----------

def song_line(ctx: Ctx, d: Deck, song: int, kind: str, lead: str, row: Row, rows) -> tuple | None:
    """곡 하나 = 주제가 사실 + 크레딧. (사실 번호들, 부른 사람들, 원래 곡 제목) 을 돌려준다"""
    ent = ctx.ent[song]
    ja = rows.get((song, "song_title_ja"), [])
    title = ja[0].value if ja else ent[3]
    artists = [d.pname(p.target) for p in rows.get((song, "performer"), []) if p.target]
    if not artists or not title:
        return None
    fids = [d.fact(f"{lead} {SONG_KIND.get(kind, kind)} 곡은 {', '.join(artists[:3])}의 「{title}」이다.", row,
                   "theme_song")]
    credit = rows.get((song, "song_credit"), [])
    if credit:
        c = credit[0].value
        labels = (("작사", "lyricist"), ("작곡", "composer"), ("편곡", "arranger"))
        parts = [f"{label} {c[k]}" for label, k in labels if c.get(k)]
        if parts:
            fids.append(d.fact(f"「{title}」의 작사 · 작곡 · 편곡: {', '.join(parts)}.", credit[0], "song_credit"))
    return [f for f in fids if f], artists, title


# 발매일은 제목이 같은 다른 음반에 붙은 경우가 있어 (약 4%) 글에는 쓰지 않는다
SONG_FIELDS = ("song_title_ja", "performer", "song_credit")


def year_of(years, r: Row) -> int:
    found = years.get((r.entity, "start_year"))
    return found[0].value if found else 9999


def pick_music(ctx: Ctx) -> list[Deck]:
    cur = ctx.cur
    cur.execute("""select entity_id, field, value, value_entity_id, source_id, source_url, fetched_at, fact_id
                   from dict.fact where field = 'theme_song' and value_entity_id is not null""")
    songs_of: dict[int, list[Row]] = defaultdict(list)
    for r in cur.fetchall():
        row = Row(r)
        if row.entity in ctx.entry_work:
            songs_of[ctx.entry_work[row.entity]].append(row)
    cur.execute("""select entity_id, value_entity_id from dict.fact
                   where field = 'performer' and value_entity_id is not null""")
    performers: dict[int, set[int]] = defaultdict(set)
    sung_by: dict[int, set[int]] = defaultdict(set)
    for song, person in cur.fetchall():
        performers[song].add(person)
        sung_by[person].add(song)
    decks: list[Deck] = []
    made = 0
    for work in ctx.order[:top(160)]:
        if made == QUOTA["themes"] + SPARE:
            break
        have = [r for r in songs_of.get(work, []) if performers.get(r.target)]
        if not ctx.free(work) or len(have) < 3:
            continue
        title = ctx.title[work]
        d = Deck(ctx, "themes", f"w{ctx.menu_id[work]}", "music", f"〈{title}〉 주제가 모음", [work],
                 [f"〈{title}〉", "주제가 다시 듣기"],
                 f"〈{title}〉의 OP·ED 를 한 장에 한 곡씩 다시 듣고 싶게 만드는 글. 표지 큰 글씨는 시기가 아니라 노래를 "
                 "내세운 멘트로 쓴다 (11/11 서원 예: '히로아카 노래 들을 사람?', '너, 내 노래 동료가 돼라'). 곡 카드 본문은 "
                 "부른 사람 · 만든 사람 같은 곡 이야기를 짧게 쓴다")
        entries = {r.entity for r in have}
        years = ctx.rows(entries, ("start_year",))
        have.sort(key=lambda r: (year_of(years, r), r.value.get("type") != "OP", r.value.get("sequence") or 0))
        have = have[:8]
        rows = ctx.rows({r.target for r in have}, SONG_FIELDS)
        songs = []
        for r in have:
            year = years.get((r.entity, "start_year"))
            lead = f"〈{title}〉 {year[0].value}년 방영분의" if year else f"〈{title}〉의"
            found = song_line(ctx, d, r.target, r.value.get("type"), lead, r, rows)
            if found:
                songs.append((r, year[0].value if year else None, *found))
        summary = add_work_summary(d, work, ctx.rows([ctx.work_entries[work][0]], WORK_FIELDS))
        d.page(work=work, image=("work", work), eyebrow=f"〈{title}〉 주제가 모음", facts=summary[:1],
               note="큰 글씨: 시기 말고 노래를 내세운 멘트")
        for r, year, fids, artists, original in songs:
            kind = SONG_KIND.get(r.value.get("type"), "주제가")
            singer = " · ".join(a.strip() for a in artists[:2])
            japanese = re.search(r"[ぁ-ヿ㐀-鿿]", singer)  # 한국어 이름이 없는 가수는 첫 줄을 글쓰기가 한국어 발음으로 쓴다
            d.page(work=work, image=("entry", r.entity), eyebrow=f"{year}년 · {kind}" if year else kind,
                   prefix=None if japanese else f"{singer}의", original=original, facts=fids,
                   note=("큰 글씨 두 줄: '부른 사람(한국어 발음 표기)의' / '「노래 제목」'. " if japanese else "")
                   + "본문: 부른 사람 · 만든 사람 같은 곡 이야기를 한두 문장")
        d.plan = [("kitsu_entries", work), ("kitsu", work, 3)]
        decks.append(d)
        made += 1
    decks += pick_artists(ctx, songs_of, sung_by)
    return decks


def pick_artists(ctx: Ctx, songs_of: dict[int, list[Row]], sung_by: dict[int, set[int]]) -> list[Deck]:
    top = set(ctx.order[:1000])
    work_of_song: dict[int, tuple[int, Row]] = {}
    for work, rows in songs_of.items():
        if work in top and ctx.ok_work(work):
            for r in rows:
                old = work_of_song.get(r.target)
                if old is None or ctx.rank[work] < ctx.rank[old[0]]:
                    work_of_song[r.target] = (work, r)
    scored = []
    for person, songs in sung_by.items():
        mine = [s for s in songs if s in work_of_song]
        if person in ctx.ko and len(mine) >= 3:
            scored.append((-len(mine), min(ctx.rank[work_of_song[s][0]] for s in mine), person))
    decks: list[Deck] = []
    for _, _, person in sorted(scored):
        if len(decks) == QUOTA["artist"] + SPARE:
            break
        mine = sorted((s for s in sung_by[person] if s in work_of_song), key=lambda s: ctx.rank[work_of_song[s][0]])
        lead = next((work_of_song[s][0] for s in mine if ctx.free(work_of_song[s][0])), None)
        if lead is None:
            continue
        name = ctx.name(person)
        song_works = list(dict.fromkeys(work_of_song[s][0] for s in mine[:8] if ctx.ok_work(work_of_song[s][0])))
        works = [lead, *[w for w in song_works if w != lead]]
        d = Deck(ctx, "artist", f"p{person}", "music", f"{name}의 애니 주제가", works,
                 [f"{name}", "애니 주제가 모음"],
                 f"{iga(name)} 부른 애니 주제가를 한 장에 한 곡씩 보여 주는 글. 이 목소리가 그 작품 주제가에도 "
                 "있었다는 발견이 재미의 중심이다. 표지 큰 글씨는 노래를 내세운 멘트로 쓴다")
        rows = ctx.rows(mine, SONG_FIELDS)
        extra = ctx.rows([person], ("artist_type", "artist_begin", "artist_area"))
        begin = extra.get((person, "artist_begin"), [])
        when = date_ko(begin[0].value) if begin else None
        first = d.fact(f"{name}의 활동 시작일은 {ida(when)}.", begin[0], "artist_info") if when else None
        songs = []
        for s in mine[:8]:
            work, row = work_of_song[s]
            found = song_line(ctx, d, s, row.value.get("type"), f"〈{ctx.title[work]}〉의", row, rows)
            if found:
                songs.append((work, *found))
        d.pname(person)
        summary = add_work_summary(d, lead, ctx.rows([ctx.work_entries[lead][0]], WORK_FIELDS))
        # 11/11 서원: 모든 장의 작은 글씨는 "○○의 애니 주제가 모음", 큰 글씨는 "〈애니〉의 「노래」"
        eyebrow = f"{name}의 애니 주제가 모음"
        # 11/11 서원: 표지에 가수 사진 (AniList 인물 사진이 있을 때, 없으면 작품 장면)
        ctx.cur.execute("""select external_id from dict.external_id
                           where source_id = 'anilist' and id_kind = 'staff' and entity_id = %s""", (person,))
        staff = ctx.cur.fetchone()
        bio = person_bio(ctx, d, person, name, "어떤 가수인지 (활동 · 소속 · 목소리나 노래의 특징이 참고 글에 있으면 그것)")
        d.page(work=lead, image=("staff", staff[0]) if staff else ("still", lead), eyebrow=eyebrow,
               facts=[bio, first, *summary[:1]],
               note=("큰 글씨: 이 가수의 노래를 내세운 멘트. 본문: 이 가수가 어떤 사람인지 한 문장 (참고 글에 있는 만큼만, "
                     "목소리 특징이 있으면 그것을 먼저)"))
        for work, fids, _artists, original in songs:
            d.page(work=work, image=("work", work), eyebrow=eyebrow, prefix=f"〈{ctx.title[work]}〉의",
                   original=original, facts=fids, note="어떤 곡인지(오프닝 · 엔딩, 함께 부른 사람 등) 한두 문장")
        d.plan = [("kitsu", w, 0) for w in works] + [("kitsu", lead, 2)]
        if staff:
            d.plan.append(("staff", staff[0], name, lead))
        decks.append(d)
    return decks


# ---------- 인물 ----------

def cast_of(ctx: Ctx, field: str = "voice_actor") -> tuple[dict[int, list[tuple[int, int, str, Row]]],
                                                          dict[int, set[tuple[int, str]]]]:
    """캐릭터 → (작품, 역할) · 성우 → [(작품, 캐릭터, 역할, 사실)]. field 로 일본어 · 한국어 성우를 고른다"""
    cur = ctx.cur
    cur.execute("""select entity_id, value_entity_id, value->>'role' from dict.fact
                   where field = 'character' and source_id = 'anilist' and value_entity_id is not null""")
    appears: dict[int, set[tuple[int, str]]] = defaultdict(set)
    for e, cid, role in cur.fetchall():
        work = ctx.entry_work.get(e)
        # 스페셜에서만 주연인 캐릭터를 작품 주연으로 세지 않도록 대표 시즌만 본다
        if work and ctx.ok_work(work) and e == ctx.work_entries[work][0]:
            appears[cid].add((work, role))
    cur.execute("""select entity_id, field, value, value_entity_id, source_id, source_url, fetched_at, fact_id
                   from dict.fact where field = %s and source_id = 'anilist'
                     and value_entity_id is not null""", (field,))
    roles: dict[int, list[tuple[int, int, str, Row]]] = defaultdict(list)
    for r in cur.fetchall():
        row = Row(r)
        for work, role in appears.get(row.entity, ()):
            roles[row.target].append((work, row.entity, role, row))
    return roles, appears


def pick_people(ctx: Ctx) -> list[Deck]:
    """성우 출연작: 인기 상위 300편에서 주연 4명 이상 · 서로 다른 작품 4개 이상

    형식(표지에 성우, 한 장에 캐릭터 한 명, 마지막 장 회수)은 사실 묶음으로 유도한다. 기존 생성 코드는 입력의
    editorial_angle · cover_mode · ending_mode · content_plan_reason 을 글쓰기에 넘기지 않는다.
    표지 문구는 코드에 넣지 않고, 글마다 다른 재료(시간 · 장르 · 같은 해)를 사실로 준다
    """
    roles, _ = cast_of(ctx)
    scored = []
    for person, items in roles.items():
        if person not in ctx.ko:
            continue
        picks: dict[int, tuple[int, Row]] = {}
        taken: set[int] = set()
        for work, cid, role, row in sorted(items, key=lambda i: (ctx.rank[i[0]], i[3].fact_id)):
            if (role == "MAIN" and ctx.rank[work] <= top(300) and cid in ctx.anilist and ctx.char_name(cid)
                    and work not in picks and cid not in taken):
                picks[work] = (cid, row)
                taken.add(cid)
        if len(picks) >= 4:
            scored.append((-len(picks), sum(ctx.rank[w] for w in picks), person, picks))
    decks: list[Deck] = []
    for index, (_, _, person, picks) in enumerate(sorted(scored, key=lambda s: s[:3])[:QUOTA["va"] + SPARE]):
        favs = ctx.rows([c for c, _ in picks.values()], ("character_favourites",))
        if all((c, "character_favourites") in favs for c, _ in picks.values()):
            works = sorted(picks, key=lambda w: -favs[(picks[w][0], "character_favourites")][0].value)
        else:
            works = sorted(picks, key=lambda w: ctx.rank[w])
        works, rep, name = works[:5], works[0], ctx.name(person)
        d = Deck(ctx, "va", f"p{person}", "people", f"성우 {name}의 캐릭터들", [rep], [f"성우 {name}"],
                 f"{name}이라는 한 사람의 목소리로 이어지는 캐릭터들을 보여 준다. 캐릭터마다 어떤 인물인지 한 줄로 "
                 "말하고 장르 목록은 읊지 않는다")
        d.cover_mode, d.ending_mode = "surprising_fact", "fan_empathy"
        d.plan_reason = "표지에서 성우를 드러내고, 다음 카드부터 한 장에 캐릭터 한 명씩, 마지막 장에서 표지를 회수한다"
        chain = ", ".join(f"〈{ctx.title[w]}〉의 {d.cname(picks[w][0])}" for w in works)
        hook = d.fact(f"{eun(name)} {reul(chain)} 모두 연기했다.", picks[works[0]][1], "voice_role_summary",
                      dict_fact_ids=[picks[w][1].fact_id for w in works])
        d.card_facts.append(hook)
        add_va_angle(ctx, d, name, works, index)
        items = []
        for i, w in enumerate(works):
            cid, row = picks[w]
            cn = d.cname(cid)
            fid = d.fact(f"{eun(name)} 〈{ctx.title[w]}〉에서 주연 {reul(cn)} 연기했다.", row, "voice_role")
            d.page_source(row)
            d.card_facts.append(fid)
            items.append((w, cid, cn, [fid, describe_character(ctx, d, cid, cn)]))
            # 표지 사실은 가장 유명한 캐릭터 그림에 연결, 나머지는 자기 사실에만 연결
            d.plan.append(("character", ctx.anilist[cid], cn, w, [hook, fid] if i == 0 else [fid], w == rep))
        d.plan.append(("kitsu", rep, 1))
        add_person_facts(ctx, d, person, name)
        summaries = ctx.rows(entries_of(ctx, works), WORK_FIELDS)
        for w in works:
            add_work_summary(d, w, summaries, with_episodes=False)
        add_voice_pages(ctx, d, f"성우 {name}", hook, items)
        d.plan.append(("kitsu", rep, 2))  # 마지막 장용 포스터 + 예비 장면
        decks.append(d)
    decks += pick_directors(ctx)
    return decks


PROFILE_LINE = re.compile(r"(?:__|\*\*)?\s*(?:Height|Weight)\s*:?\s*(?:__|\*\*)?\s*:?[^\n]*\n?", re.I)


def describe_character(ctx: Ctx, d: Deck, cid: int, name: str) -> str | None:
    """캐릭터 소개 참고 글을 사실로 (성격 · 역할 · 특징을 새로 쓰는 재료). 키 · 몸무게 줄은 뺀다 (11/11 서원)"""
    found = ctx.rows([cid], ("character_description_ref",)).get((cid, "character_description_ref"))
    if not found:
        return None
    text = ref_text(PROFILE_LINE.sub("", found[0].value if isinstance(found[0].value, str) else str(found[0].value)),
                    700)
    if len(text) < 80:
        return None
    return d.fact(f"{name}의 소개는 details 의 참고 글에 있다. 문장을 옮기지 말고 성격 · 역할 · 특징을 새로 쓴다.",
                  found[0], "character_reference", character_reference_do_not_copy=text)


def person_bio(ctx: Ctx, d: Deck, pid: int, name: str, what: str) -> str | None:
    """인물 소개 참고 글 (한국어 위키백과 첫 문단 + AniList 인물 소개)을 사실로. 표지 본문에 '어떤 사람인지'를 쓰는 재료
    (11/11 서원: 가수 · 감독 · 작가의 특징을 첫 장에). 문장은 옮기지 않고 새로 쓴다"""
    about = ctx.rows([pid], ("namu_profile_ref", "wiki_intro_ref", "person_description_ref"))
    parts, first = [], None
    # 나무위키 개요 · 특징 문단을 먼저 (목소리 · 연출 · 작풍 같은 특징이 있음, 11/11 서원)
    for field in ("namu_profile_ref", "wiki_intro_ref", "person_description_ref"):
        found = about.get((pid, field))
        if found:
            value = found[0].value
            parts.append(value.get("text", "") if isinstance(value, dict) else str(value))
            first = first or found[0]
    text = ref_text(PROFILE_LINE.sub("", "\n".join(parts)), 1500)
    if not first or len(text) < 30:
        return None
    return d.fact(f"{name}의 소개는 details 의 참고 글에 있다. 문장을 옮기지 말고 {what}을 새로 쓴다.", first,
                  "person_reference", person_reference_do_not_copy=text)


def add_voice_pages(ctx: Ctx, d: Deck, eyebrow: str, hook: str | None, items: list[tuple]) -> None:
    """성우 모음 장 계획 (11/11 서원)
    - 모든 장 작은 글씨 '성우 ○○', 다음 장부터 큰 글씨 '〈애니〉의 캐릭터 이름'
    - 표지와 2장이 겹치지 않게: 가장 유명한 캐릭터는 표지에만 (그 캐릭터 그림 + 같은 목소리를 묻는 멘트)"""
    rep_work, rep_cid, rep_cn, rep_fids = items[0]
    work_title = f"〈{ctx.title[rep_work]}〉"
    d.page(work=rep_work, image=("character", ctx.anilist[rep_cid]), eyebrow=eyebrow, facts=[hook, *rep_fids],
           note=(f"큰 글씨: {work_title}의 {wa(rep_cn)} 같은 목소리의 캐릭터가 궁금해지는 질문형 멘트 "
                 f"(예: '{wa(rep_cn)} 같은 성우는 누구?'). 본문: '{work_title}의 {wa(rep_cn)} 같은 목소리를 가진 캐릭터는 "
                 "누가 있을까?' 같은 한 문장. 글마다 표현을 바꾼다"))
    for w, cid, cn, fids in items[1:]:
        d.page(work=w, image=("character", ctx.anilist[cid]), eyebrow=eyebrow, title=[f"〈{ctx.title[w]}〉의", cn],
               facts=fids, note="이 캐릭터가 어떤 인물인지 한 줄 (소개 참고 글이 있으면 새로 씀). 성우 이름은 반복하지 않는다")


def add_va_angle(ctx: Ctx, d: Deck, name: str, works: list[int], index: int) -> None:
    """글마다 다른 각도의 재료를 사실로 준다 (시간의 폭 · 장르의 폭 · 같은 해의 주연)"""
    entry_of = {w: ctx.work_entries[w][0] for w in works}
    rows = ctx.rows(entry_of.values(), ("start_year", "genre"))
    years = {w: rows[(e, "start_year")][0] for w, e in entry_of.items() if (e, "start_year") in rows}
    options: list[tuple[str, str, Row]] = []
    if len(years) >= 2:
        first, last = min(years, key=lambda w: years[w].value), max(years, key=lambda w: years[w].value)
        gap = years[last].value - years[first].value
        if gap >= 4:
            span = (f"{name}의 주연작은 〈{ctx.title[first]}〉({years[first].value}년 시작)부터 "
                    f"〈{ctx.title[last]}〉({years[last].value}년 시작)까지 {gap}년에 걸쳐 있다.")
            options.append(("시간의 폭", span, years[first]))
    genre_rows = [r for e in entry_of.values() for r in rows.get((e, "genre"), []) if r.value in GENRE_KO]
    names = sorted({GENRE_KO[r.value] for r in genre_rows})
    if len(names) >= 3:
        options.append(("장르의 폭", f"{name}의 주연작 장르는 {', '.join(names[:5])}에 걸쳐 있다.", genre_rows[0]))
    by_year: dict[int, list[int]] = defaultdict(list)
    for w, row in years.items():
        by_year[row.value].append(w)
    same = next((v for v in by_year.values() if len(v) >= 2), None)
    if same:
        y = years[same[0]].value
        both = f"〈{ctx.title[same[0]]}〉와 〈{ctx.title[same[1]]}〉"
        options.append(("같은 해의 주연들", f"{eun(name)} {y}년에 {both}의 주연을 같은 해에 맡았다.", years[same[0]]))
    if options:
        label, text, row = options[index % len(options)]
        d.topic = f"성우 {name}의 캐릭터들 ({label})"
        d.fact(text, row, "angle_material", angle=label)


def add_person_facts(ctx: Ctx, d: Deck, person: int, name: str) -> None:
    d.pname(person)
    info = ctx.rows([person], ("birth_date", "years_active"))
    birth = info.get((person, "birth_date"), [])
    when = date_ko(birth[0].value) if birth else None
    if when:
        d.fact(f"{name}의 생년월일은 {ida(when)}.", birth[0], "person_info")
    active = info.get((person, "years_active"), [])
    if active and active[0].value:
        d.fact(f"{name}의 활동 시작 연도는 {active[0].value[0]}년이다.", active[0], "person_info")


def pick_directors(ctx: Ctx) -> list[Deck]:
    cur = ctx.cur
    cur.execute("""select entity_id, field, value, value_entity_id, source_id, source_url, fetched_at, fact_id
                   from dict.fact where field = 'director' and source_id = 'anilist' and value_entity_id is not null""")
    works: dict[int, dict[int, Row]] = defaultdict(dict)
    for r in cur.fetchall():
        row = Row(r)
        w = ctx.entry_work.get(row.entity)
        if w and ctx.ok_work(w) and ctx.rank[w] <= 1000 and row.target in ctx.ko:
            works[row.target].setdefault(w, row)
    decks: list[Deck] = []
    for person in sorted((p for p, v in works.items() if len(v) >= 3),
                         key=lambda p: min(ctx.rank[w] for w in works[p])):
        if len(decks) == QUOTA["director"] + SPARE:
            break
        mine = sorted(works[person], key=lambda w: ctx.rank[w])
        lead = next((w for w in mine if ctx.free(w)), None)
        if lead is None:
            continue
        name = ctx.name(person)
        chosen = [lead, *[w for w in mine[:5] if w != lead]]
        d = Deck(ctx, "director", f"p{person}", "people", f"{name} 감독 작품 모음", chosen,
                 [f"{name} 감독", "이 작품들도 만들었다"],
                 f"이 작품들 감독이 다 {name}였다는 발견을 주는 글. 한 장에 한 작품씩, 어떤 이야기인지와 시작 "
                 "연도를 짧게 말한다")
        add_person_facts(ctx, d, person, name)
        years = ctx.rows([ctx.work_entries[w][0] for w in chosen], ("start_year",))
        made = {}
        for w in chosen:
            year = years.get((ctx.work_entries[w][0], "start_year"))
            suffix = f" ({year[0].value}년 시작)" if year else ""
            made[w] = d.fact(f"{eun(name)} 〈{ctx.title[w]}〉의 감독이다{suffix}.", works[person][w], "director")
        summaries = ctx.rows(entries_of(ctx, chosen), WORK_FIELDS)
        items = [(w, [made[w], *add_work_summary(d, w, summaries, with_episodes=False)]) for w in chosen]
        # 11/11 서원: 모든 장 작은 글씨 '○○ 감독 작품 모음', 큰 글씨는 애니 제목만
        eyebrow = f"{name} 감독 작품 모음"
        bio = person_bio(ctx, d, person, name, "어떤 감독인지 (경력 · 소속 · 연출 특징이 참고 글에 있으면 그것)")
        d.page(work=lead, image=("still", lead), eyebrow=eyebrow, facts=[bio, made[lead]],
               note=("큰 글씨: 이 작품들 감독이 같은 사람이라는 발견이 보이는 멘트. 본문: 이 감독이 어떤 사람인지 한 문장 "
                     "(참고 글에 있는 만큼만, 연출 특징이 있으면 그것을 먼저)"))
        for w, fids in items:
            d.page(work=w, image=("work", w), eyebrow=eyebrow, title=[f"〈{ctx.title[w]}〉"], facts=fids,
                   note="이 작품이 어떤 이야기인지 한두 문장과 시작 연도. 감독 이름은 카드마다 반복하지 않는다")
        d.plan = [("kitsu", w, 0) for w in chosen] + [("kitsu", lead, 2)]
        decks.append(d)
    return decks


# ---------- 캐릭터 ----------

CHAR_FIELDS = ("birthday", "age", "blood_type", "voice_actor", "voice_actor_ko")


def add_char_facts(ctx: Ctx, d: Deck, cid: int, title: str, rows, *, with_birthday: bool = True) -> list[str]:
    """캐릭터 기본 사실 (성우 · 생일 · 나이 · 혈액형). 만든 사실 번호를 돌려준다"""
    name = d.cname(cid)
    made: list[str | None] = []
    for field, label in (("voice_actor", "일본어 성우"), ("voice_actor_ko", "한국어 성우")):
        people = [r for r in rows.get((cid, field), []) if r.target]
        if people:
            made.append(d.fact(f"{name}의 {label}는 {ida(d.pname(people[0].target))}.", people[0], "voice_role"))
    bday = rows.get((cid, "birthday"), [])
    if bday and with_birthday:
        made.append(d.fact(f"{name}의 생일은 {bday[0].value['month']}월 {bday[0].value['day']}일이다.", bday[0],
                           "birthday"))
    age = rows.get((cid, "age"), [])
    if age and re.fullmatch(r"\d{1,3}", str(age[0].value)):
        made.append(d.fact(f"〈{title}〉에서 {eun(name)} {age[0].value}세다.", age[0], "character_info"))
    blood = rows.get((cid, "blood_type"), [])
    if blood:
        made.append(d.fact(f"{name}의 혈액형은 {blood[0].value}형이다.", blood[0], "character_info"))
    return [f for f in made if f]


def main_casts(ctx: Ctx) -> dict[int, dict[int, Row]]:
    """작품 → {주연 캐릭터: 사실} (대표 시즌 기준)"""
    ctx.cur.execute("""select entity_id, field, value, value_entity_id, source_id, source_url, fetched_at, fact_id
                       from dict.fact where field = 'character' and source_id = 'anilist'
                         and value_entity_id is not null and value->>'role' = 'MAIN'""")
    mains: dict[int, dict[int, Row]] = defaultdict(dict)
    for r in ctx.cur.fetchall():
        row = Row(r)
        w = ctx.entry_work.get(row.entity)
        if w and ctx.ok_work(w) and row.entity == ctx.work_entries[w][0] and ctx.char_name(row.target):
            mains[w].setdefault(row.target, row)
    return mains


def pick_character(ctx: Ctx) -> list[Deck]:
    """주연 소개 (11/11 서원): 표지는 작품 그림, 다음 장부터 주연 한 명씩 성격 · 역할 · 특징. 성우 · 키는 안 씀.
    그래서 소개 글(80자 이상)이 있는 주연이 3명 이상인 작품만 고른다"""
    mains = main_casts(ctx)
    described = ctx.rows({c for w in ctx.order[:top(300)] for c in mains.get(w, {})}, ("character_description_ref",))

    def has_desc(c: int) -> bool:
        found = described.get((c, "character_description_ref"))
        return bool(found) and len(ref_text(PROFILE_LINE.sub("", str(found[0].value)))) >= 80

    decks: list[Deck] = []
    for w in ctx.order[:top(300)]:
        if len(decks) == QUOTA["intro"] + SPARE:
            break
        cids = [c for c in sorted(mains.get(w, {}), key=lambda c: mains[w][c].fact_id)
                if c in ctx.anilist and has_desc(c)][:4]
        if not ctx.free(w) or len(cids) < 3:
            continue
        title = ctx.title[w]
        d = Deck(ctx, "intro", f"w{ctx.menu_id[w]}", "character", f"〈{title}〉 주연 캐릭터 소개", [w],
                 [f"〈{title}〉", "주연 캐릭터 소개"],
                 f"〈{title}〉 주연들을 처음 보는 사람에게 소개하는 글. 한 장에 한 명씩 성격 · 역할 · 특징을 소개 참고 "
                 "글에서 새로 쓴다. 성우 이름 · 키 · 몸무게는 쓰지 않는다")
        summary = add_work_summary(d, w, ctx.rows([ctx.work_entries[w][0]], WORK_FIELDS))
        items = []
        for c in cids:
            cn = d.cname(c)
            role = d.fact(f"{eun(cn)} 〈{title}〉의 주연 캐릭터다.", mains[w][c], "character_role")
            items.append((c, cn, [role, describe_character(ctx, d, c, cn)]))
        eyebrow = f"〈{title}〉 주연 소개"
        d.page(work=w, image=("work", w), eyebrow=eyebrow, facts=summary[:2],
               note="큰 글씨: 이 작품의 주연들을 만나 보자는 멘트")
        for c, cn, fids in items:
            d.page(work=w, image=("character", ctx.anilist[c]), eyebrow=eyebrow, title=[cn], facts=fids,
                   note="성격 · 역할 · 특징을 한두 문장 (성우 · 키 · 몸무게는 쓰지 않는다)")
        d.plan = [("kitsu", w, 2)] + [("character", ctx.anilist[c], cn, w) for c, cn, _ in items]
        decks.append(d)
    decks += pick_birthday_book(ctx, mains)
    decks += pick_profile(ctx, mains)
    decks += pick_birthdays(ctx, mains)
    return decks


def pick_birthday_book(ctx: Ctx, mains: dict[int, dict[int, Row]]) -> list[Deck]:
    """작품별 주연 생일 묶음집: 인기 상위 300편에서 생일이 있는 주연 4명 이상 (날짜와 상관없는 상시 글)"""
    decks: list[Deck] = []
    for w in ctx.order[:top(300)]:
        if len(decks) == QUOTA["bdaybook"] + SPARE:
            break
        if not ctx.free(w):
            continue
        rows = ctx.rows(mains.get(w, {}), ("birthday", "character_favourites"))
        have = [c for c in mains.get(w, {}) if (c, "birthday") in rows and c in ctx.anilist]
        if len(have) < 4:
            continue
        fav = {c: rows[(c, "character_favourites")][0].value for c in have if (c, "character_favourites") in rows}
        picked = sorted(have, key=lambda c: (-fav.get(c, 0), mains[w][c].fact_id))[:4]
        dates = {c: rows[(c, "birthday")][0].value for c in picked}
        picked.sort(key=lambda c: (dates[c]["month"], dates[c]["day"]))
        born = {c: f"{dates[c]['month']}월 {dates[c]['day']}일" for c in picked}
        title = ctx.title[w]
        d = Deck(ctx, "bdaybook", f"w{ctx.menu_id[w]}", "character", f"〈{title}〉 주연 생일 모음", [w],
                 [f"〈{title}〉", "주연 생일 모음"], f"{title} 주연들의 생일을 한 글에 모은다")
        d.plan_reason = "표지 → 한 장에 캐릭터 한 명(이름 · 생일 · 사진) → 마무리. 날짜와 상관없이 볼 수 있는 상시 글"
        summary = ", ".join(f"{d.cname(c)} {born[c]}" for c in picked)
        first_row = rows[(picked[0], "birthday")][0]
        hook = d.fact(f"〈{title}〉 주연들의 생일은 {summary}이다.", first_row, "birthday_summary")
        d.card_facts.append(hook)
        born_fid = {}
        for i, c in enumerate(picked):
            name = d.cname(c)
            fid = d.fact(f"{name}의 생일은 {born[c]}이다.", rows[(c, "birthday")][0], "birthday")
            born_fid[c] = fid
            d.card_facts.append(fid)
            d.plan.append(("character", ctx.anilist[c], name, w, [hook, fid] if i == 0 else [fid], True))
        details = ctx.rows(picked, CHAR_FIELDS)
        for c in picked:
            add_char_facts(ctx, d, c, title, details, with_birthday=False)
        add_work_summary(d, w, ctx.rows([ctx.work_entries[w][0]], WORK_FIELDS))
        d.plan.append(("kitsu", w, 2))  # 표지용 포스터 + 예비 장면
        # 11/11 서원: 2장부터 작은 글씨는 〈작품명〉만, 한 장에 한 명(이름 · 생일), 따로 마무리 장을 만들지 않는다
        d.page(work=w, image=("work", w), eyebrow=f"〈{title}〉 주연 생일 모음", facts=[hook],
               note="큰 글씨: 이 작품 주연들의 생일을 모았다는 멘트")
        for i, c in enumerate(picked):
            last = i == len(picked) - 1
            d.page(work=w, image=("character", ctx.anilist[c]), eyebrow=f"〈{title}〉", title=[d.cname(c), born[c]],
                   facts=[born_fid[c]],
                   note="이 캐릭터 생일에 붙이는 짧은 한마디" + (
                       ". 마지막 장이라 '달력 알림이 울릴 때마다 네 사람 이름부터 생각날 듯' 같은 마무리 한마디를 이어 써도 된다"
                       if last else ""))
        decks.append(d)
    return decks


HEIGHT_RE = re.compile(r"Height\s*:?\s*(?:__|\*\*)?\s*:?\s*[^\n]{0,30}?(\d{2,3}(?:\.\d)?)\s*cm", re.I)
WEIGHT_RE = re.compile(r"Weight\s*:?\s*(?:__|\*\*)?\s*:?\s*[^\n]{0,30}?(\d{2,3}(?:\.\d)?)\s*kg", re.I)


def profile_note(name: str, says: list[str]) -> str:
    """프로필 덧붙임 한 문장 안내 (11/11 서원: 눈에 띄는 1~2명만, 딱딱한 설명조 말고)"""
    return (f"본문 첫 줄(숫자)은 코드가 넣는다. 덧붙임은 한 문장: {name} 입장에서 이 점을 팬이 툭 던지듯 짧게 말한다 — "
            f"{' · '.join(says)}. 사실 문장 구조를 그대로 옮기지 않는다. '남성 캐릭터인' 같은 설명조, 첫 줄 숫자 되풀이, "
            "'프로필' · '비교' · '마지막' · 'TMI' 같은 말은 쓰지 않는다")


def pick_profile(ctx: Ctx, mains: dict[int, dict[int, Row]]) -> list[Deck]:
    """주연 프로필 비교 (11/11 서원 아이디어): 키 · 혈액형 · 나이 (+ 있으면 몸무게).
    - 키 · 몸무게는 AniList 소개 글의 'Height · Weight' 줄, 혈액형 · 나이는 AniList 칸
    - 나이는 시간 도약(Time Skip)이 있는 작품과 '16-17'처럼 여러 값인 경우 뺀다 (작품 안에서 나이를 먹으니까)
    - 2칸 이상 있는 주연이 3명 이상인 작품만. 표지는 주연 얼굴 바둑판"""
    works = [w for w in ctx.order[:top(300)] if ctx.free(w)]
    chars = {c for w in works for c in mains.get(w, {})}
    rows = ctx.rows(chars, ("character_description_ref", "blood_type", "age", "gender"))
    tags = ctx.rows([ctx.work_entries[w][0] for w in works], ("tag",))
    decks: list[Deck] = []
    for w in works:
        if len(decks) == QUOTA["profile"] + SPARE:
            break
        skip_age = any(r.value.get("name") == "Time Skip" for r in tags.get((ctx.work_entries[w][0], "tag"), []))
        cands = []
        gender: dict[int, tuple[str, Row]] = {}  # 덧붙임 고르기용 (카드 칸 수에는 안 셈)
        raw_age: dict[int, int] = {}
        for c in sorted(mains.get(w, {}), key=lambda c: mains[w][c].fact_id):
            if c not in ctx.anilist:
                continue
            if (g := rows.get((c, "gender"))):
                gender[c] = (str(g[0].value).strip().lower(), g[0])
            if (a0 := rows.get((c, "age"))) and (m := re.match(r"\s*(\d{1,4})", str(a0[0].value))):
                raw_age[c] = int(m.group(1))  # 고르기용 나이 (화면에 안 쓰는 작품이어도 어린 캐릭터를 거르려고)
            found = rows.get((c, "character_description_ref"))
            text = str(found[0].value) if found else ""
            p = {}
            if (h := HEIGHT_RE.search(text)):
                p["height"] = (h.group(1), found[0])
            if (wt := WEIGHT_RE.search(text)):
                p["weight"] = (wt.group(1), found[0])
            b = rows.get((c, "blood_type"))
            blood = re.fullmatch(r"\s*(AB|A|B|O)\s*(Rh\s*[+-])?\s*", str(b[0].value), re.I) if b else None
            if blood:  # A · B · O · AB(+Rh)만 ('Klaxosaur' 같은 설정 값은 뺌, 11/11)
                rh = f" ({blood.group(2).replace(' ', '')})" if blood.group(2) else ""
                p["blood"] = (f"{blood.group(1).upper()}형{rh}", b[0])
            a = rows.get((c, "age"))
            if a and re.fullmatch(r"\d{1,3}", str(a[0].value)) and not skip_age:
                p["age"] = (a[0].value, a[0])
            if len(p) >= 2:
                cands.append((c, p))
        if len(cands) < 3:
            continue
        cands = cands[:4]
        title = ctx.title[w]
        d = Deck(ctx, "profile", f"w{ctx.menu_id[w]}", "character", f"〈{title}〉 주연 프로필", [w],
                 [f"〈{title}〉", "주연 프로필"],
                 f"〈{title}〉 주연들의 키 · 혈액형 · 나이를 한 명씩 보여 주는 프로필 비교 글. 사실에 있는 항목만 쓴다")
        items = []
        names = {c: d.cname(c) for c, _ in cands}
        for c, p in cands:
            cn = names[c]
            fids = []
            if "height" in p:
                fids.append(d.fact(f"〈{title}〉에서 {cn}의 키는 {p['height'][0]}cm다.", p["height"][1], "character_info"))
            if "blood" in p:
                fids.append(d.fact(f"{cn}의 혈액형은 {ida(p['blood'][0])}.", p["blood"][1], "character_info"))
            if "weight" in p:
                fids.append(d.fact(f"〈{title}〉에서 {cn}의 몸무게는 {p['weight'][0]}kg다.", p["weight"][1],
                                   "character_info"))
            if "age" in p:
                fids.append(d.fact(f"〈{title}〉에서 {eun(cn)} {p['age'][0]}세다.", p["age"][1], "character_info"))
            # 본문 첫 문장 (11/11 서원: 키 · 혈액형 · 몸무게 · 나이만 '161cm · O형' 처럼)
            line = " · ".join(v for v in (f"{p['height'][0]}cm" if "height" in p else "",
                                          p["blood"][0] if "blood" in p else "",
                                          f"{p['weight'][0]}kg" if "weight" in p else "",
                                          f"{p['age'][0]}세" if "age" in p else "") if v)
            items.append((c, cn, [f for f in fids if f], line))
        # 덧붙임 (11/11 서원): 눈에 띄는 1~2명에게만 한 문장, 나머지 카드는 숫자만.
        # 고르는 순서 ① 가장 큰 사람과 가장 작은 사람의 키 차이 10cm 이상 → 작은 쪽 카드
        # ② 여자인데 170cm 이상(13세 이상) · 남자인데 160cm 이하(16세 이상, 어린 남자는 원래 작으니까)
        # ③ 둘 다 없으면 가장 큰 한 명. 나이를 모르면 ②에 넣는다
        tall = [(float(p["height"][0]), c) for c, p in cands if "height" in p]
        point: dict[int, tuple[list, list[str]]] = {}  # 캐릭터 → (근거 사실, 글쓰기에 줄 포인트)
        if len(tall) >= 2:
            first_row = next(p["height"][1] for c, p in cands if "height" in p)
            (low_h, low_c), (top_h, top_c) = min(tall), max(tall)
            if top_h - low_h >= 10:
                gap = d.fact(f"〈{title}〉에서 {eun(names[top_c])} {names[low_c]}보다 {top_h - low_h:g}cm 크다.",
                             first_row, "character_compare")
                point[low_c] = ([gap], [f"가장 큰 {wa(names[top_c])} {top_h - low_h:g}cm 차이"])
            for h, c in sorted(tall, key=lambda x: -x[0]):
                g = gender.get(c)
                age = raw_age.get(c, 99)
                if not g or not ((g[0] == "female" and h >= 170 and age >= 13) or
                                 (g[0] == "male" and h <= 160 and age >= 16)):
                    continue
                if c not in point and len(point) >= 2:
                    continue
                female = g[0] == "female"
                fid = d.fact(f"{names[c]}의 성별은 {'여성' if female else '남성'}이다.", g[1], "character_info")
                got, says = point.setdefault(c, ([], []))
                got.append(fid)
                says.append(f"{'여자' if female else '남자'} 주연치고 {'큰' if female else '작은'} {h:g}cm")
            if not point and len(tall) == len(cands) >= 3 and [h for h, _ in tall].count(top_h) == 1:
                best = d.fact(f"〈{title}〉 주연 {len(tall)}명 중 키가 가장 큰 사람은 {names[top_c]}({top_h:g}cm)다.",
                              first_row, "character_compare")
                point[top_c] = ([best], [f"주연 {len(tall)}명 중 가장 큰 키"])
        fields = [label for key, label in (("height", "키"), ("blood", "혈액형"), ("weight", "몸무게"), ("age", "나이"))
                  if any(key in p for _, p in cands)]
        # 표지 (11/11 서원): 큰 글씨 '〈작품〉 주연들 / 키 · 혈액형 TMI', 그 밖의 문구는 넣지 않는다
        d.page(work=w, image=("collage",), eyebrow="", title=[f"〈{title}〉 주연들", f"{' · '.join(fields)} TMI"],
               facts=[items[0][2][0]], body=[], extra=0)
        for c, cn, fids, line in items:
            if c in point:
                got, says = point[c]
                d.page(work=w, image=("character", ctx.anilist[c]), eyebrow=f"〈{title}〉", title=[cn],
                       facts=[*fids, *got], body=[line], extra=1,
                       note=profile_note(cn, says))
            else:
                d.page(work=w, image=("character", ctx.anilist[c]), eyebrow=f"〈{title}〉", title=[cn],
                       facts=fids, body=[line], extra=0, note="본문은 코드가 넣는다 (숫자만). 덧붙이지 않는다")
        d.plan = [("character", ctx.anilist[c], cn, w) for c, cn, _, _ in items] + [("kitsu", w, 1)]
        decks.append(d)
    return decks


def pick_birthdays(ctx: Ctx, mains: dict[int, dict[int, Row]]) -> list[Deck]:
    """오늘부터 14일 안에 생일인 인기작 주연. 흐름: 표지(언제 · 누구) → 작품 → 짧은 줄거리 → 성격 → 축하"""
    today = dt.datetime.now(KST).date()
    candidates = [(w, c) for w in ctx.order[:300] if ctx.ok_work(w) for c in mains.get(w, {})]
    rows = ctx.rows({c for _, c in candidates}, ("birthday", "character_description_ref"))
    picks = []
    for w, c in candidates:
        found = rows.get((c, "birthday"))
        if not found or c not in ctx.anilist or (c, "character_description_ref") not in rows:
            continue
        try:
            when = dt.date(today.year, found[0].value["month"], found[0].value["day"])
        except ValueError:
            continue
        if 0 <= (when - today).days <= 14:
            picks.append((not ctx.free(w), when, ctx.rank[w], w, c))
    decks: list[Deck] = []
    seen: set[int] = set()
    for _, when, _, w, c in sorted(picks, key=lambda p: p[:3]):
        if len(decks) == QUOTA["birthday"] + SPARE:
            break
        if w in seen:
            continue
        seen.add(w)
        title = ctx.title[w]
        deck_name = ctx.char_name(c)
        d = Deck(ctx, "birthday", f"c{c}", "character", f"{deck_name} 생일 ({when.month}월 {when.day}일)", [w],
                 [f"{when.month}월 {when.day}일", f"{deck_name}"], f"{title} {deck_name}의 생일을 축하한다")
        name = d.cname(c)
        d.feed_valid_until = (when + dt.timedelta(days=1)).isoformat()
        d.plan_reason = "표지(언제 · 누구 생일) → 나오는 작품 → 줄거리 아주 짧게 → 캐릭터 성격 → 생일 축하로 마무리"
        first = d.fact(f"{name}의 생일은 {when.month}월 {when.day}일이다.", rows[(c, "birthday")][0], "birthday")
        role = d.fact(f"{eun(name)} 〈{title}〉의 주연 캐릭터다.", mains[w][c], "character_role")
        entry = ctx.work_entries[w][0]
        story = ctx.rows([entry], ("synopsis_en_ref",)).get((entry, "synopsis_en_ref"))
        card_facts = [first, role]
        if story:
            note = f"〈{title}〉의 이야기는 details 의 참고 글에 있다. 문장을 옮기지 말고 아주 짧게 새로 쓴다."
            card_facts.append(d.fact(note, story[0], "story_reference",
                                     story_reference_do_not_copy=ref_text(story[0].value, 700)))
        desc = rows[(c, "character_description_ref")][0]
        card_facts.append(d.fact(f"{name}의 성격은 details 의 참고 글에 있다. 문장을 옮기지 말고 새로 쓴다.", desc,
                                 "personality_reference", personality_reference_do_not_copy=ref_text(desc.value)))
        d.card_facts = [cf for cf in card_facts if cf]
        add_char_facts(ctx, d, c, title, ctx.rows([c], CHAR_FIELDS), with_birthday=False)
        add_work_summary(d, w, ctx.rows([entry], WORK_FIELDS))
        d.plan = [("character", ctx.anilist[c], name, w, [first], True), ("kitsu", w, 3)]
        decks.append(d)
    return decks


# ---------- 11/9 추가: 입문 가이드 · 한국 성우 · 원작 · 배경 장소 · 흥행 · 수상 · 이달의 생일 ----------

FACT_COLS = "entity_id, field, value, value_entity_id, source_id, source_url, fetched_at, fact_id"
SOURCE_KO = {"MANGA": "만화", "LIGHT_NOVEL": "라이트노벨", "NOVEL": "소설", "WEB_NOVEL": "웹소설",
             "ORIGINAL": "애니메이션 오리지널", "VIDEO_GAME": "게임", "VISUAL_NOVEL": "비주얼 노벨",
             "MULTIMEDIA_PROJECT": "미디어믹스 기획"}
WRITTEN_SOURCES = ("MANGA", "LIGHT_NOVEL", "NOVEL", "WEB_NOVEL")
GUIDE_FIELDS = ("format", "start_year", "episodes", "main_studio", "director", "source_material", "synopsis_en_ref")
ROLE_KO = {"MAIN": "주연", "SUPPORTING": "조연", "BACKGROUND": "단역"}
PLACE_NAME = {"도쿄도": "도쿄", "삿포로시": "삿포로"}
NOT_A_PLACE = ("일본", "목록", "지리")  # 나라 전체 · 작품 안 세계는 '같은 도시' 묶음이 안 된다
AWARD_DECKS = (("Japan Academy Prize for Animation of the Year", "일본 아카데미상 애니메이션 작품상", "japan-academy"),
               ("Animation Kobe Theatrical Film Award", "애니메이션 고베 작품상", "kobe"),
               # 11/11 늘림: 수상작이 사전에 3편 이상 있는 상 (Synergy Award 는 어떤 상인지 확인 못 해서 뺌)
               ("Animation Kobe Television Award", "애니메이션 고베 작품상 TV 부문", "kobe-tv"),
               ("Japan Media Arts Festival Animation Division Excellence Award",
                "문화청 미디어 예술제 애니메이션 부문 우수상", "media-arts"),
               ("Mainichi Film Award for Best Animation Film", "마이니치 영화 콩쿠르 애니메이션 영화상", "mainichi"),
               ("Ōfuji Noburō Award", "오후지 노부로상", "ofuji"),
               ("Nihon SF Taisho Award", "일본 SF 대상", "nihon-sf"),
               ("Annecy Cristal for a Feature Film", "안시 국제 애니메이션 영화제 장편 크리스털상", "annecy"))
NOT_AUTHORS = ("Hajime Yatate",)  # 선라이즈 제작진의 공동 필명이라 '한 작가' 묶음이 안 된다
# 소개 글에 성격을 말하는 낱말이 있어야 성격 카드를 만든다 (없거나 모호하면 카드를 뺀다)
PERSONALITY_WORDS = re.compile(
    r"\b(personality|cheerful|kind|gentle|shy|timid|cold|calm|serious|stubborn|energetic|friendly|quiet|lazy|arrogant"
    r"|caring|optimistic|hard-?working|cool-headed|hot-headed|loyal|mischievous|clumsy|rude|selfish|honest|brave"
    r"|polite|introverted|outgoing|tsundere|airheaded|naive|childish|mature|strict|sadistic|reserved|aloof|carefree"
    r"|easygoing|confident|cowardly|compassionate|sarcastic|blunt|straightforward)\b", re.I)


def mark_trial_works(ctx: Ctx) -> None:
    """첫 시범 글감의 대표 작품은 새 글감에서 다시 고르지 않는다 (같은 작품만 눈에 띄지 않게)"""
    by_menu = {v: k for k, v in ctx.menu_id.items()}
    for path in (TRIAL / "manifests").glob("*.json"):
        if not any(f"-{k}-" in path.stem for k in NEW_KINDS):
            for number in json.loads(path.read_text(encoding="utf-8"))["menu_work_ids"][:1]:
                ctx.used.add(by_menu[number])


def facts_of(ctx: Ctx, field: str) -> dict[int, list[Row]]:
    """칸 하나의 사실 전부 → {대상: [사실]}"""
    ctx.cur.execute(f"select {FACT_COLS} from dict.fact where field = %s", (field,))
    out: dict[int, list[Row]] = defaultdict(list)
    for r in ctx.cur.fetchall():
        row = Row(r)
        out[row.entity].append(row)
    return out


def movie_title(ctx: Ctx, entry: int, work: int) -> str | None:
    """작품이 극장판 하나면 작품 제목, 시리즈 안의 극장판이면 그 극장판의 한국어 제목"""
    return ctx.title[work] if len(ctx.work_entries[work]) == 1 else ctx.entry_ko.get(entry)


def add_cast_lines(d: Deck, w: int, title: str, cast: dict[int, Row], limit: int = 3) -> None:
    """인기 많은 주연부터 이름 · 일본어 성우 · 그림 계획"""
    ctx = d.ctx
    favs = ctx.rows(cast, ("character_favourites", "voice_actor"))
    def fav(c: int) -> int:
        found = favs.get((c, "character_favourites"))
        return found[0].value if found else 0
    for c in sorted(cast, key=lambda c: (-fav(c), cast[c].fact_id))[:limit]:
        name = d.cname(c)
        fid = d.fact(f"{eun(name)} 〈{title}〉의 주연 캐릭터다.", cast[c], "character_role")
        va = [r for r in favs.get((c, "voice_actor"), []) if r.target]
        if va:
            d.fact(f"{name}의 일본어 성우는 {ida(d.pname(va[0].target))}.", va[0], "voice_role")
        if c in ctx.anilist:
            d.plan.append(("character", ctx.anilist[c], name, w, [fid], True))


def pick_guide(ctx: Ctx) -> list[Deck]:
    """작품 기본 가이드: 처음 보는 사람용. 어떤 이야기 → 볼 순서 · 분량 → 만든 사람 → 주연과 성우"""
    mains = main_casts(ctx)
    decks: list[Deck] = []
    for w in ctx.order[:top(200)]:
        if len(decks) == QUOTA["basic"] + SPARE:
            break
        entries = ctx.work_entries[w]
        rows = ctx.rows(entries, GUIDE_FIELDS)
        lead = entries[0]
        story = rows.get((lead, "synopsis_en_ref"))
        if not ctx.free(w) or not story or len(mains.get(w, {})) < 2:
            continue
        title = ctx.title[w]
        d = Deck(ctx, "basic", f"w{ctx.menu_id[w]}", "guide", f"〈{title}〉 입문 가이드", [w], [f"〈{title}〉", "처음 본다면"],
                 "처음 보는 사람을 위한 입문 가이드. 1장 표지, 2장 어떤 이야기인지(참고 글을 짧게 새로 쓴다), "
                 "3장 볼 순서와 분량, 4장 만든 사람(제작사 · 감독 · 원작), 5장 주연 캐릭터와 성우.")
        d.target_cards = 5
        d.plan_reason = "표지 → 어떤 이야기 → 볼 순서 · 분량 → 만든 사람 → 주연과 성우"
        d.fact(f"〈{title}〉의 줄거리는 details 의 참고 글에 있다. 문장을 옮기지 말고 짧게 새로 쓴다.", story[0],
               "story_reference", story_reference_do_not_copy=ref_text(story[0].value, 700))
        items = []
        for e in entries:
            fmt, year = rows.get((e, "format")), rows.get((e, "start_year"))
            if fmt and year:
                items.append((year[0].value, e, fmt[0]))
        items.sort(key=lambda x: x[:2])
        if items:
            counts = Counter(FORMAT_KO.get(x[2].value, x[2].value) for x in items)
            parts = " · ".join(f"{k} {v}편" for k, v in counts.most_common())
            d.fact(f"{eun(f'〈{title}〉')} {parts}으로 이뤄져 있다.", items[0][2], "viewing_order")
        for year, e, fmt in [x for x in items if x[2].value in ("TV", "TV_SHORT", "ONA", "MOVIE")][:5]:
            label, kind = ctx.entry_ko.get(e) or title, FORMAT_KO[fmt.value]
            eps = rows.get((e, "episodes"))
            if eps and fmt.value != "MOVIE":
                d.fact(f"{year}년 시작한 〈{label}〉({kind})는 총 {eps[0].value}화다.", eps[0], "viewing_order")
            else:
                d.fact(f"〈{label}〉는 {year}년에 나온 {ida(kind)}.", fmt, "viewing_order")
        studios = [r for r in rows.get((lead, "main_studio"), []) if r.target]
        if studios:
            d.fact(f"〈{title}〉의 메인 제작사는 {ida(ctx.studio(studios[0].target))}.", studios[0], "studio")
        directors = [r for r in rows.get((lead, "director"), []) if r.target]
        if directors:
            d.fact(f"〈{title}〉의 감독은 {ida(d.pname(directors[0].target))}.", directors[0], "director")
        source = rows.get((lead, "source_material"))
        if source and source[0].value in SOURCE_KO:
            d.fact(f"〈{title}〉의 원작 형태는 {ida(SOURCE_KO[source[0].value])}.", source[0], "source_material")
        d.plan = [("kitsu", w, 3)]
        add_cast_lines(d, w, title, mains[w])
        d.plan.append(("kitsu_entries", w))
        decks.append(d)
    return decks


def kr_name(ctx: Ctx, person: int) -> str:
    """한국 성우 이름: 한글 원어 칸을 먼저 쓴다 (별칭에는 '(성우)' 같은 구분 표시가 붙어 있음)"""
    native = (ctx.ent[person][2] or "").strip()
    return native if re.fullmatch(r"[가-힣\s]+", native) else re.sub(r"\s*\([^)]*\)$", "", ctx.name(person))


def pick_krva(ctx: Ctx) -> list[Deck]:
    """한국 성우 모음: 인기 상위 1,000편에서 주연 4명 이상. 형식은 일본 성우 모음과 같다"""
    roles, _ = cast_of(ctx, "voice_actor_ko")
    scored = []
    for person, items in roles.items():
        picks: dict[int, tuple[int, Row]] = {}
        taken: set[int] = set()
        for work, cid, role, row in sorted(items, key=lambda i: (ctx.rank[i[0]], i[3].fact_id)):
            if (role == "MAIN" and ctx.rank[work] <= 1000 and cid in ctx.anilist and ctx.char_name(cid)
                    and work not in picks and cid not in taken):
                picks[work] = (cid, row)
                taken.add(cid)
        if len(picks) >= 4 and re.search(r"[가-힣]", kr_name(ctx, person)):
            scored.append((sum(sorted(ctx.rank[w] for w in picks)[:5]), person, picks))
    decks: list[Deck] = []
    for index, (_, person, picks) in enumerate(sorted(scored, key=lambda s: s[:2])):
        if len(decks) == QUOTA["krva"] + SPARE:
            break
        ordered = sorted(picks, key=lambda w: ctx.rank[w])
        rep = next((w for w in ordered if ctx.free(w)), None)
        if rep is None:
            continue
        works = [rep, *[w for w in ordered if w != rep]][:5]
        name = kr_name(ctx, person)
        d = Deck(ctx, "krva", f"p{person}", "people", f"한국 성우 {name}의 캐릭터들", [rep], [f"성우 {name}"],
                 f"국내 더빙으로 {name}의 목소리를 들어 본 사람이 반가워할 글. 표지에서 성우를 드러내고 한 장에 캐릭터 "
                 "한 명씩, 마지막 장에서 표지를 회수한다")
        d.cover_mode, d.ending_mode = "surprising_fact", "fan_empathy"
        d.plan_reason = "표지에서 한국 성우를 드러내고, 다음 카드부터 한 장에 캐릭터 한 명씩, 마지막 장에서 표지를 회수한다"
        chain = ", ".join(f"〈{ctx.title[w]}〉의 {d.cname(picks[w][0])}" for w in works)
        hook = d.fact(f"{eun(name)} {reul(chain)} 모두 한국어로 연기했다.", picks[works[0]][1], "voice_role_summary",
                      dict_fact_ids=[picks[w][1].fact_id for w in works])
        d.card_facts.append(hook)
        add_va_angle(ctx, d, name, works, index)
        d.topic = d.topic.replace("성우", "한국 성우", 1) if "한국 성우" not in d.topic else d.topic
        items = []
        for i, w in enumerate(works):
            cid, row = picks[w]
            cn = d.cname(cid)
            fid = d.fact(f"{eun(name)} 〈{ctx.title[w]}〉에서 주연 {cn}의 한국어 목소리를 맡았다.", row, "voice_role")
            d.page_source(row)
            d.card_facts.append(fid)
            items.append((w, cid, cn, [fid, describe_character(ctx, d, cid, cn)]))
            d.plan.append(("character", ctx.anilist[cid], cn, w, [hook, fid] if i == 0 else [fid], w == rep))
        add_person_facts(ctx, d, person, name)
        summaries = ctx.rows(entries_of(ctx, works), WORK_FIELDS)
        for w in works:
            add_work_summary(d, w, summaries, with_episodes=False)
        d.plan.append(("kitsu", rep, 2))
        add_voice_pages(ctx, d, f"성우 {name}", hook, items)
        decks.append(d)
    return decks


def origin_of(ctx: Ctx) -> tuple[dict[int, Row], dict]:
    """작품 → 원작 사실 (애니가 만화 · 소설 원작으로 기록된 작품만, 애니보다 먼저 나온 가장 이른 원작)"""
    rep = {ctx.work_entries[w][0]: w for w in ctx.order[:1000] if ctx.ok_work(w)}
    rows = ctx.rows(rep, ("source_material", "source_work", "source_info", "start_year"))
    out: dict[int, Row] = {}
    for e, w in rep.items():
        material = rows.get((e, "source_material"))
        if not material or material[0].value not in WRITTEN_SOURCES:
            continue
        form = "MANGA" if material[0].value == "MANGA" else "NOVEL"
        same = [r for r in rows.get((e, "source_work"), []) if r.value.get("format") == form and r.value.get("start")]
        if not same:
            continue
        first = min(same, key=lambda r: (r.value["start"].get("year", 9999), r.value["start"].get("month") or 12))
        year = rows.get((e, "start_year"))
        if year and first.value["start"].get("year", 9999) <= year[0].value:
            out[w] = first
    return out, rows


def story_author(value: dict) -> dict | None:
    return next((a for a in value.get("authors") or [] if re.search(r"story|original", a.get("role") or "", re.I)), None)


def pick_origin(ctx: Ctx) -> list[Deck]:
    """원작 소개 (한 작품의 원작 정보) + 같은 작가의 원작 애니 모음"""
    origins, rows = origin_of(ctx)
    ctx.cur.execute("select external_id, entity_id from dict.external_id where source_id = 'anilist' and id_kind = 'staff'")
    staff = dict(ctx.cur.fetchall())
    def author_name(value: dict) -> tuple[int | None, str | None]:
        a = story_author(value)
        pid = staff.get(str(a["anilist_staff"])) if a else None
        return pid, ctx.ko.get(pid) if pid else None
    decks: list[Deck] = []
    for w in sorted(origins, key=lambda x: ctx.rank[x]):
        if len([d for d in decks if d.kind == "origin"]) == QUOTA["origin"] + SPARE:
            break
        row = origins[w]
        v = row.value
        pid, author = author_name(v)
        if not ctx.free(w) or not author or not v.get("volumes") or v.get("status") != "FINISHED":
            continue
        entry = ctx.work_entries[w][0]
        title, kind = ctx.title[w], SOURCE_KO[rows[(entry, "source_material")][0].value]
        d = Deck(ctx, "origin", f"w{ctx.menu_id[w]}", "guide", f"〈{title}〉의 원작 이야기", [w],
                 [f"〈{title}〉", "원작은 어떤 작품일까"],
                 "애니를 본 사람이 원작이 궁금해지게 만드는 글. 1장 표지, 2장 원작이 어떤 작품인지(작가 · 형태), "
                 "3장 연재 기간과 분량, 4장 애니 정보로 마무리.")
        d.target_cards = 4
        d.plan_reason = "표지 → 원작 소개 → 연재 기간 · 분량 → 애니 정보"
        d.pname(pid)
        d.fact(f"〈{title}〉의 원작은 {author}의 {ida(kind)}.", row, "source_work")
        start, end = date_ko(v["start"]), date_ko(v.get("end"))
        d.fact(f"〈{title}〉 원작은 {start}부터 {end}까지 연재됐다.", row, "source_work")
        amount = f"총 {v['volumes']}권" + (f", {v['chapters']}화" if v.get("chapters") else "")
        d.fact(f"〈{title}〉 원작 분량은 {ida(amount)}.", row, "source_work")
        info = rows.get((entry, "source_info"), [])
        magazines = [m["ko"] for r in info for m in r.value.get("magazine") or [] if m.get("ko")]
        if magazines:
            d.fact(f"〈{title}〉 원작의 연재지는 {ida(magazines[0])}.", next(r for r in info if r.value.get("magazine")),
                   "source_info")
        add_work_summary(d, w, ctx.rows([entry], WORK_FIELDS))
        d.plan = [("kitsu", w, 3), ("kitsu_entries", w)]
        decks.append(d)
    by_author: dict[int, list[int]] = defaultdict(list)
    for w, row in origins.items():
        pid, name = author_name(row.value)
        a = story_author(row.value)
        if pid and name and a["name"] not in NOT_AUTHORS:
            by_author[pid].append(w)
    made = 0
    for pid in sorted(by_author, key=lambda p: (-len(by_author[p]), min(ctx.rank[w] for w in by_author[p]))):
        if made == QUOTA["author"] + SPARE or len(by_author[pid]) < 3:
            break
        mine = sorted(by_author[pid], key=lambda w: ctx.rank[w])
        lead = next((w for w in mine if ctx.free(w)), None)
        if lead is None:
            continue
        name = ctx.ko[pid]
        chosen = [lead, *[w for w in mine if w != lead]][:5]
        d = Deck(ctx, "author", f"p{pid}", "people", f"{name} 원작 애니 모음", chosen, [name, "원작 애니 모음"],
                 f"{name}의 작품이 원작인 애니를 묶는다. 표지에서 작가를 드러내고 한 장에 작품 하나씩 소개한다")
        d.pname(pid)
        summaries = ctx.rows(entries_of(ctx, chosen), WORK_FIELDS)
        items = []
        for w in chosen:
            kind = SOURCE_KO[rows[(ctx.work_entries[w][0], "source_material")][0].value]
            fid = d.fact(f"〈{ctx.title[w]}〉의 원작은 {name}의 {ida(kind)}.", origins[w], "source_work")
            items.append((w, [fid, *add_work_summary(d, w, summaries, with_episodes=False)]))
        # 11/11 서원: 표지는 작가 중심 (작은 글씨 비움, 큰 글씨 '○○ 작가의 원작 애니 모음', 본문은 어떤 작가인지)
        intro = person_bio(ctx, d, pid, name, "어떤 이야기를 쓰는 작가인지 (작풍 · 특징이 참고 글에 있으면 그것)")
        eyebrow = f"{name} 원작 애니 모음"
        # 11/11 서원: 표지는 그 작가가 쓴 책 사진 → 원작 책 표지 바둑판 (AniList 만화 · 소설 표지)
        d.page(work=lead, image=("collage",), eyebrow="", title=[f"{name} 작가의", "원작 애니 모음"],
               facts=[intro, items[0][1][0]],
               note=("본문: 이 작가가 어떤 이야기를 쓰는 사람인지 한 문장 (예: '독특한 캐릭터 설정과 막힘없는 이야기가 "
                     "특징인 ○○ 작가의 작품들'). 참고 글 · 사실에 있는 만큼만 쓰고, 없으면 대표작 이름으로 소개한다"))
        for w, fids in items:
            d.page(work=w, image=("work", w), eyebrow=eyebrow, title=[f"〈{ctx.title[w]}〉"], facts=fids,
                   note="이 작품이 어떤 이야기인지 한두 문장. 작가 이름은 카드마다 반복하지 않는다")
        # 책 표지: 원작 사실의 출처가 AniList 만화 · 소설 페이지라 그 번호로 표지를 받는다 (책이 3권 이상일 때 바둑판)
        books = list(dict.fromkeys(
            (m.group(1), w) for w in chosen for m in [re.search(r"anilist\.co/manga/(\d+)", origins[w].url or "")] if m))
        d.plan = ([("manga_cover", mid, w, ctx.title[w]) for mid, w in books]
                  + [("kitsu", w, 0) for w in chosen] + [("kitsu", lead, 2)])
        decks.append(d)
        made += 1
    return decks


def pick_place(ctx: Ctx) -> list[Deck]:
    """배경 장소 모음: 위키데이터에 같은 도시가 배경으로 기록된 작품 3편 이상"""
    by: dict[str, dict[int, Row]] = defaultdict(dict)
    for e, items in facts_of(ctx, "narrative_location").items():
        w = ctx.entry_work.get(e)
        if w and ctx.ok_work(w):
            for r in items:
                label = r.value.get("ko")
                if label and not any(x in label for x in NOT_A_PLACE):
                    by[PLACE_NAME.get(label, label)].setdefault(w, r)
    decks: list[Deck] = []
    for place in sorted(by, key=lambda p: -len(by[p])):
        if len(decks) == QUOTA["place"] + SPARE or len(by[place]) < 3:
            break
        works = sorted(by[place], key=lambda w: ctx.rank[w])
        lead = next((w for w in works if ctx.free(w)), None)
        if lead is None:
            continue
        chosen = [lead, *[w for w in works if w != lead]][:5]
        d = Deck(ctx, "place", by[place][lead].value["wikidata"], "curation", f"{iga(place)} 배경인 애니", chosen,
                 [f"{place}", "이곳이 배경인 애니"],
                 f"{iga(place)} 배경인 애니를 묶어 그 도시로 떠나는 기분을 주는 글. 작품마다 그곳에서 어떤 이야기가 "
                 "펼쳐지는지 한 줄로 말한다")
        summaries = ctx.rows(entries_of(ctx, chosen), WORK_FIELDS)
        items = []
        for w in chosen:
            fid = d.fact(f"〈{ctx.title[w]}〉의 배경 중에는 {iga(place)} 있다.", by[place][w], "narrative_location")
            items.append((w, [fid, *add_work_summary(d, w, summaries, with_episodes=False)]))
        # 11/11 서원: 작품마다 카드가 있어야 함 (#30 도쿄 글에 〈아키라〉 카드가 없었음)
        eyebrow = f"{iga(place)} 배경인 애니"
        d.page(work=lead, image=("still", lead), eyebrow=eyebrow, facts=[items[0][1][0]],
               note=(f"큰 글씨: {place}로 떠나는 기분이 드는 멘트. 본문: 이 글에 나오는 작품을 한두 개 들어 "
                     f"(예: '〈작품〉처럼 {iga(place)} 배경인 애니, 도시의 얼굴은 이렇게 달라') {place}의 여러 얼굴을 예고"))
        for w, fids in items:
            d.page(work=w, image=("work", w), eyebrow=eyebrow, title=[ctx.title[w]], facts=fids,
                   note=f"이 작품에서 {place}가 어떤 무대인지, 어떤 이야기인지 한두 문장")
        d.plan = [("kitsu", w, 0) for w in chosen] + [("kitsu", lead, 2)]
        decks.append(d)
    return decks


def money(amount: float, unit: str) -> str:
    if unit == "yen":
        return f"{amount / 1e8:,.1f}".rstrip("0").rstrip(".") + "억 엔"
    return f"약 {amount / 1e8:,.2f}".rstrip("0").rstrip(".") + "억 달러"


def ranking_deck(ctx: Ctx, slug: str, topic: str, cover: list[str], brief: str, summary: str,
                 chosen: list[tuple], names: dict[int, str], amount_fact, amount_short, info, start: int = 1) -> Deck:
    """순위 글 하나 (11/11 서원: 표지는 포스터 바둑판 + 순위 이름, 다음 장부터 작은 글씨에 순위를 꼭).
    start: 첫 장의 순위 (100만 클럽처럼 6위부터 이어질 때)"""
    works = list(dict.fromkeys(w for _, _, w, _ in chosen))
    d = Deck(ctx, "boxoffice", slug, "curation", topic, works, cover, brief)
    order = ", ".join(f"{i}위 〈{names[e]}〉" for i, (_, e, _, _) in enumerate(chosen, start))
    head = d.fact(f"{summary} {order} 순서다.", chosen[0][3], "ranking_summary")
    items = []
    for i, (amount, e, w, row) in enumerate(chosen, start):
        title = names[e]
        fids = [d.fact(amount_fact(title, amount), row, "box_office", rank=i)]
        year = info.get((e, "start_year"))
        if year:
            fids.append(d.fact(f"〈{title}〉는 {year[0].value}년에 나온 극장판이다.", year[0]))
        studios = [r for r in info.get((e, "main_studio"), []) if r.target]
        if studios:
            fids.append(d.fact(f"〈{title}〉의 메인 제작사는 {ida(ctx.studio(studios[0].target))}.", studios[0], "studio"))
        directors = [r for r in info.get((e, "director"), []) if r.target]
        if directors:
            fids.append(d.fact(f"〈{title}〉의 감독은 {ida(d.pname(directors[0].target))}.", directors[0], "director"))
        story = ctx.rows([e], ("synopsis_en_ref",)).get((e, "synopsis_en_ref"))
        if story:
            fids.append(d.fact(f"〈{title}〉의 이야기는 details 의 참고 글에 있다. 문장을 옮기지 말고 아주 짧게 새로 쓴다.",
                               story[0], "story_reference",
                               story_reference_do_not_copy=ref_text(story[0].value, 500)))
        items.append((i, e, w, title, amount, fids))
    d.page(work=works[0], image=("collage",), eyebrow="극장판 흥행 순위", title=cover, facts=[head],
           note="본문 한 줄: 무엇을 기준으로 한 순위인지 자연스럽게")
    for i, e, w, title, amount, fids in items:
        d.page(work=w, image=("entry", e), eyebrow=f"{i}위", title=[title, amount_short(amount)], facts=fids,
               note="이 작품이 어떤 이야기인지 한두 문장 (나온 해 · 감독은 필요할 때만)")
    # 그림: 극장판 Kitsu 포스터, 없으면 AniList 포스터 (순위는 그림과 상관없이 매긴다 — 11/11 무한성편 · 슬램덩크가 빠졌던 문제)
    d.plan = [("kitsu_one", e, w) if e in ctx.kitsu else ("anilist_cover", e, w) for _, e, w, _ in chosen]
    if ctx.kitsu_entry(chosen[0][2]) is not None:
        d.plan.append(("kitsu", chosen[0][2], 1))
    return d


def pick_boxoffice(ctx: Ctx) -> list[Deck]:
    """극장판 흥행 순위
    - 한국 관객 수 (KOBIS, 11/11 서원 결정): 전체 상위 5편 + 극장판이 4편 넘게 맞춰진 시리즈별 상위 5편
    - 흥행 수입 (위키데이터): 통화별 상위 5편 (시리즈 하나에 한 편)"""
    decks: list[Deck] = []

    def rankable(w: int | None) -> bool:
        """순위에는 그림 유무와 상관없이 넣는다 (Kitsu 가 없는 작품도). 제목이 있고 성인물이 아니면 된다"""
        return bool(w) and w in ctx.title and w not in ctx.adult

    def japanese(e: int, rows) -> bool:
        """일본 애니만 (11/11: 달러 순위 1위에 중국 애니 〈나타지마동요해〉가 들어갔던 문제)"""
        country = rows.get((e, "country"))
        return not country or country[0].value == "JP"

    kr = facts_of(ctx, "kr_audience")
    kr_info = ctx.rows(kr, ("format", "start_year", "main_studio", "director", "country"))
    kr_movies = []
    for e, items in kr.items():
        w = ctx.entry_work.get(e)
        if rankable(w) and japanese(e, kr_info):
            best = max(items, key=lambda r: r.value["audience"])
            kr_movies.append((best.value["audience"], e, w, best))
    kr_movies.sort(key=lambda m: -m[0])
    kr_names = {e: movie_title(ctx, e, w) or r.value.get("title_ko") or ctx.title[w] for _, e, w, r in kr_movies}
    kr_brief = ("한국 극장 누적 관객 수로 일본 애니 극장판을 줄 세운 글. 순위와 관객 수가 재미의 중심이다. 영화관입장권 통합전산망 "
                "주간 박스오피스에 오른 기록 기준이라 표지 본문에서 '한국 극장 관객 수 기준' 정도로 한 번 밝힌다")
    kr_summary = "영화관입장권 통합전산망 주간 박스오피스 기준 한국 누적 관객 수로 일본 애니 극장판을 줄 세우면"
    def kr_fact(title, n):
        return f"〈{title}〉의 한국 누적 관객 수는 {n:,}명이다."
    def kr_short(n):
        return f"{n // 10000:,}만 명"
    if len(kr_movies) >= 5:
        decks.append(ranking_deck(ctx, "kr", "한국 관객 수 상위 일본 애니 극장판", ["한국 관객 수 순위"], kr_brief,
                                  kr_summary, kr_movies[:5], kr_names, kr_fact, kr_short, kr_info))
    box = facts_of(ctx, "box_office")
    info = ctx.rows(box, ("format", "start_year", "main_studio", "director", "country"))
    box = {e: items for e, items in box.items() if japanese(e, info)}
    for unit, slug, label in (("yen", "jpy", "엔화"), ("United States dollar", "usd", "달러")):
        movies = []
        for e, items in box.items():
            best = max((r for r in items if r.value.get("unit") == unit), key=lambda r: r.value["amount"], default=None)
            w = ctx.entry_work.get(e)
            fmt = info.get((e, "format"))
            if best and rankable(w) and fmt and fmt[0].value == "MOVIE" and movie_title(ctx, e, w):
                movies.append((best.value["amount"], e, w, best))
        chosen, seen = [], set()
        for m in sorted(movies, key=lambda m: -m[0]):
            if m[2] not in seen:
                seen.add(m[2])
                chosen.append(m)
        chosen = chosen[:5]
        if len(chosen) < 5:
            continue
        names = {e: movie_title(ctx, e, w) for _, e, w, _ in chosen}
        decks.append(ranking_deck(
            ctx, slug, f"{label} 흥행 수입 기록 상위 극장판", [f"{label} 흥행 수입 순위"],
            f"{label} 흥행 수입으로 극장판 다섯 편을 줄 세운 글. 순위와 금액이 재미의 중심이다. 흥행 수입이 알려진 "
            "극장판끼리의 순위라 '역대 1위'라고 단정하지 말고 '흥행 수입이 알려진 극장판 중'이라고 한 번 밝힌다. "
            "출처 이름은 쓰지 않는다",
            f"{label} 흥행 수입이 알려진 극장판을 줄 세우면", chosen, names,
            lambda title, amount, unit=unit: f"〈{title}〉의 흥행 수입은 {ida(money(amount, unit))}.",
            lambda amount, unit=unit: money(amount, unit), info))
    by_work: dict[int, list[tuple]] = defaultdict(list)
    for m in kr_movies:
        by_work[m[2]].append(m)
    series = {w for w, items in by_work.items() if len(items) >= 4}
    for w, items in sorted(by_work.items(), key=lambda kv: -len(kv[1])):
        if w not in series:
            continue
        title = ctx.title[w]
        decks.append(ranking_deck(ctx, f"kr-w{ctx.menu_id[w]}", f"〈{title}〉 극장판 한국 관객 수 순위",
                                  [f"〈{title}〉 극장판", "한국 관객 수 순위"], kr_brief,
                                  f"〈{title}〉 극장판을 " + kr_summary.split("관객 수로 ")[0] + "관객 수로 줄 세우면",
                                  items[:5], kr_names, kr_fact, kr_short, kr_info))
    decks += kobis_extra_decks(ctx, kr_movies, kr_names, kr_info, kr_brief, kr_summary, kr_fact, kr_short, series)
    return decks


def kobis_extra_decks(ctx: Ctx, movies: list[tuple], names, info, brief, summary, fact, short,
                      series: set[int]) -> list[Deck]:
    """KOBIS 로 더 만드는 순위 글 (11/11 서원 OK): 감독별 · 연대별 · 2020년대 해마다 · 100만 클럽 · 오래 걸린 · 개봉 첫 주"""
    decks: list[Deck] = []
    base = summary.split("관객 수로 ")[0]  # '영화관입장권 통합전산망 주간 박스오피스 기준 한국 누적 '

    def opened(m: tuple) -> int:
        return int((m[3].value.get("open_date") or "0")[:4])

    # 감독별: 극장판 4편 이상 · 두 작품 이상 (한 시리즈뿐인 감독은 시리즈 순위와 겹쳐서 뺌) · 한국어 이름 있음
    directed = ctx.rows([e for _, e, _, _ in movies], ("director",))
    by_person: dict[int, list[tuple]] = defaultdict(list)
    for m in movies:
        found = [r for r in directed.get((m[1], "director"), []) if r.target]
        if found:
            by_person[found[0].target].append(m)
    for person, items in sorted(by_person.items(), key=lambda kv: -len(kv[1])):
        works = {w for _, _, w, _ in items}
        if len(items) < 4 or person not in ctx.ko or (len(works) < 2 and works <= series):
            continue
        name = ctx.name(person)
        decks.append(ranking_deck(ctx, f"kr-p{person}", f"{name} 감독 극장판 한국 관객 수 순위",
                                  [f"{name} 감독 극장판", "한국 관객 수 순위"], brief,
                                  f"{name} 감독의 극장판을 {base}관객 수로 줄 세우면", items[:5], names, fact, short, info))
    # 연대별
    for decade in (2000, 2010, 2020):
        items = [m for m in movies if decade <= opened(m) < decade + 10]
        if len(items) >= 5:
            decks.append(ranking_deck(ctx, f"kr-{decade}s", f"{decade}년대 한국 관객 수 상위 일본 애니 극장판",
                                      [f"{decade}년대 일본 애니 극장판", "한국 관객 수 순위"], brief,
                                      f"{decade}년대에 개봉한 일본 애니 극장판을 {base}관객 수로 줄 세우면",
                                      items[:5], names, fact, short, info))
    # 2020년대 해마다 (해마다 순위는 비슷한 글이 반복돼서 2020년대만)
    for year in range(2020, dt.date.today().year + 1):
        items = [m for m in movies if opened(m) == year]
        if len(items) >= 5:
            decks.append(ranking_deck(ctx, f"kr-{year}", f"{year}년 개봉 일본 애니 극장판 한국 관객 수 순위",
                                      [f"{year}년 극장가 일본 애니", "한국 관객 수 순위"], brief,
                                      f"{year}년에 개봉한 일본 애니 극장판을 {base}관객 수로 줄 세우면",
                                      items[:5], names, fact, short, info))
    # 100만 클럽: 1~5위는 전체 순위 글에 있으니 6위부터
    club = [m for m in movies if m[0] >= 1_000_000][5:10]
    if len(club) >= 3:
        decks.append(ranking_deck(ctx, "kr-million", "한국 관객 100만을 넘은 일본 애니 극장판 (6위부터)",
                                  ["관객 100만 클럽", "6위부터"], brief,
                                  f"{base}관객 수 100만을 넘은 일본 애니 극장판을 6위부터 이어 세우면",
                                  club, names, fact, short, info, start=6))
    # 오래 걸린 극장판: 외국 영화 주간 10위 안에 든 주 수
    stay = sorted(((m[3].value.get("weeks_in_top10") or 0, m[1], m[2], m[3]) for m in movies), key=lambda m: -m[0])[:5]
    if len(stay) == 5 and stay[-1][0] >= 4:
        decks.append(ranking_deck(
            ctx, "kr-longrun", "한국 극장에 오래 걸린 일본 애니 극장판", ["오래 걸린 일본 애니 극장판", "주간 10위 안에 든 주"],
            "외국 영화 주간 박스오피스 10위 안에 든 주 수로 줄 세운 글. 오래 사랑받은 극장판이라는 점이 재미의 중심이다",
            "영화관입장권 통합전산망 외국 영화 주간 박스오피스 10위 안에 든 주 수로 일본 애니 극장판을 줄 세우면",
            stay, names, lambda title, n: f"〈{title}〉는 외국 영화 주간 박스오피스 10위 안에 {n}주 동안 들었다.",
            lambda n: f"{n}주", info))
    # 개봉 첫 주 기록: 처음 주간 10위 안에 든 주까지의 누적 관객
    first = sorted(((m[3].value.get("first_week_audience") or 0, m[1], m[2], m[3]) for m in movies),
                   key=lambda m: -m[0])[:5]
    if len(first) == 5 and first[-1][0] > 0:
        decks.append(ranking_deck(
            ctx, "kr-opening", "개봉 첫 주에 관객이 몰린 일본 애니 극장판", ["개봉 첫 주 기록", "일본 애니 극장판"],
            "처음 주간 박스오피스 10위 안에 든 주까지 모은 관객 수로 줄 세운 글. 개봉하자마자 몰린 관심이 재미의 중심이다",
            "영화관입장권 통합전산망 주간 박스오피스 기준, 처음 10위 안에 든 주까지 모은 누적 관객 수로 줄 세우면",
            first, names, lambda title, n: f"〈{title}〉는 처음 주간 10위 안에 든 주까지 누적 관객 {n:,}명을 모았다.",
            short, info))
    return decks


def pick_award(ctx: Ctx) -> list[Deck]:
    """수상작 모음: 상 하나에 수상작 5편까지 (위키데이터 수상 기록)"""
    awards = facts_of(ctx, "award")
    decks: list[Deck] = []
    for en, ko, slug in AWARD_DECKS:
        won: dict[int, tuple[int, Row]] = {}
        for e, items in awards.items():
            w = ctx.entry_work.get(e)
            if w and ctx.ok_work(w):
                for r in items:
                    if r.value.get("en") == en and w not in won:
                        won[w] = (e, r)
        if len(won) < 3:
            continue
        works = sorted(won, key=lambda w: ctx.rank[w])[:5]
        d = Deck(ctx, "award", slug, "curation", f"{ko} 수상작 모음", works, [ko, "수상작 모음"],
                 f"{reul(ko)} 받은 작품을 묶는 글. 작품마다 어떤 이야기인지 짧게 소개하고, 상 이름은 카드마다 반복하지 않는다")
        summaries = ctx.rows(entries_of(ctx, works), WORK_FIELDS)
        items = []
        for w in works:
            e, row = won[w]
            title = movie_title(ctx, e, w) or ctx.title[w]
            when = f"{row.value['year']}년 " if row.value.get("year") else ""
            fid = d.fact(f"{eun(f'〈{title}〉')} {when}{reul(ko)} 받았다.", row, "award")
            items.append((w, e, title, row.value.get("year"), [fid, *add_work_summary(d, w, summaries,
                                                                                    with_episodes=False)]))
        # 11/11 서원: 표지는 포스터 바둑판 + 큰 글씨 '○○ 수상작 모음', 다음 장부터 큰 글씨는 애니 제목만
        d.page(work=works[0], image=("collage",), title=[ko, "수상작 모음"], facts=[items[0][4][0]],
               note="본문 한 줄: 이 상을 받은 작품들을 모았다는 말 (상 설명은 사실에 있는 만큼만)")
        for w, e, title, year, fids in items:
            d.page(work=w, image=("entry", e) if e in ctx.kitsu else ("work", w),
                   eyebrow=f"{year}년 수상" if year else "수상작", title=[title], facts=fids,
                   note="이 작품이 어떤 이야기인지 한두 문장")
        d.plan = [("kitsu_one", won[w][0], w) if won[w][0] in ctx.kitsu else ("kitsu", w, 0) for w in works]
        d.plan.append(("kitsu", works[0], 1))
        decks.append(d)
    return decks


def pick_birthday_month(ctx: Ctx) -> list[Deck]:
    """이달의 생일: 그 달이 생일인 캐릭터 전부가 대상. 시범은 성격 카드 있는 글 · 없는 글을 섞어 고른다

    카드: 1장 표지(날짜 · 이름 · 축하를 한 장에) → 2장 작품 + 짧은 줄거리 → 3장 성격 (소개 글에 성격이 있을 때만)
    """
    today = dt.datetime.now(KST).date()
    ctx.cur.execute(f"select {FACT_COLS} from dict.fact where field = 'birthday' and value->>'month' = %s",
                    (str(BIRTHDAY_MONTH),))
    birthdays: dict[int, Row] = {}
    for r in ctx.cur.fetchall():
        row = Row(r)
        birthdays.setdefault(row.entity, row)
    ctx.cur.execute(f"""select {FACT_COLS} from dict.fact where field = 'character' and source_id = 'anilist'
                        and value_entity_id = any(%s)""", (list(birthdays),))
    best: dict[int, tuple[tuple, int, Row]] = {}
    for r in ctx.cur.fetchall():
        row = Row(r)
        w = ctx.entry_work.get(row.entity)
        if not w or not ctx.ok_work(w) or row.target not in ctx.anilist or not ctx.char_name(row.target):
            continue
        key = (row.value.get("role") != "MAIN", ctx.rank[w], row.fact_id)
        if row.target not in best or key < best[row.target][0]:
            best[row.target] = (key, w, row)
    described = ctx.rows(best, ("character_description_ref",))

    def personality(c: int) -> Row | None:
        """3장(성격 · 특징) 재료. 11/11 서원: 성격 낱말이 없어도 소개 글이 있으면 특징으로 3장을 만든다 (#24 아르민)"""
        found = described.get((c, "character_description_ref"))
        text = ref_text(PROFILE_LINE.sub("", str(found[0].value))) if found else ""
        return found[0] if len(text) >= 80 else None

    main_count = sum(1 for v in best.values() if v[2].value.get("role") == "MAIN")
    print(f"{BIRTHDAY_MONTH}월 생일: 글로 만들 수 있는 캐릭터 {len(best):,}명 (주연 {main_count:,}명) · "
          f"성격 카드 가능 {sum(1 for c in best if personality(c)):,}명", flush=True)
    limit = QUOTA["bdaymonth"] + SPARE
    with_card, without, seen = [], [], set()
    for c in sorted(best, key=lambda c: best[c][0]):
        if MASS or best[c][1] not in seen:  # 대량 모드는 한 작품에서 여러 명 (생일이 있는 캐릭터 전부)
            bucket = with_card if personality(c) else without
            if len(bucket) < limit:
                bucket.append(c)
                seen.add(best[c][1])
    keep = QUOTA["bdaymonth"] - 1  # 시범: 성격 카드 있는 글 2개 + 없는 글 1개
    next_month = dt.date(today.year + (BIRTHDAY_MONTH == 12), BIRTHDAY_MONTH % 12 + 1, 1)
    decks: list[Deck] = []
    order = (sorted([*with_card, *without], key=lambda c: best[c][0]) if MASS
             else [*with_card[:keep], *without[:1], *with_card[keep:], *without[1:]])
    for c in order:
        _, w, role_row = best[c]
        bday = birthdays[c]
        try:
            when = dt.date(today.year, BIRTHDAY_MONTH, bday.value["day"])
        except (KeyError, TypeError, ValueError):
            continue
        title, deck_name, desc = ctx.title[w], ctx.char_name(c), personality(c)
        three = (f"{title} {deck_name}의 생일 글. 딱 3장. 1장(표지)은 날짜 · 이름 · 생일 축하 인사를 한 장에 같이 쓴다. "
                 "2장은 나오는 작품과 아주 짧은 줄거리를 한 장에 합친다. 3장은 캐릭터의 성격이나 특징 · 역할을 참고 글로 "
                 "새로 쓰고, 참고 글이 없으면 프로필(나이 · 혈액형 · 성우 등 사실에 있는 것)을 쓴다. 작품명은 〈〉로 감싼다 "
                 "(예: 〈원피스〉). 축하만 있는 카드나 줄거리만 있는 카드를 따로 만들지 않는다.")
        two = (f"{title} {deck_name}의 생일 글. 딱 2장. 1장(표지)은 날짜 · 이름 · 생일 축하 인사를 한 장에 같이 쓴다. "
               "2장은 나오는 작품과 아주 짧은 줄거리를 한 장에 합친다. 작품명은 〈〉로 감싼다. 성격 카드는 만들지 않는다.")
        profile_rows = ctx.rows([c], CHAR_FIELDS)
        has_profile = any((c, f) in profile_rows for f in ("age", "blood_type", "voice_actor"))
        d = Deck(ctx, "bdaymonth", f"c{c}", "character", f"{deck_name} 생일 ({when.month}월 {when.day}일)", [w],
                 [f"{when.month}월 {when.day}일", f"{deck_name}"], three if desc or has_profile else two)
        d.target_cards = 3 if desc or has_profile else 2
        d.feed_valid_until = next_month.isoformat()
        d.plan_reason = "표지(날짜 · 이름 · 축하) → 작품 + 짧은 줄거리" + (" → 성격" if desc else "")
        name = d.cname(c)
        first = d.fact(f"{name}의 생일은 {when.month}월 {when.day}일이다.", bday, "birthday")
        role_name = ROLE_KO.get(role_row.value.get("role"), "등장")
        role = d.fact(f"{eun(name)} 〈{title}〉의 {role_name} 캐릭터다.", role_row, "character_role")
        entry = ctx.work_entries[w][0]
        story = ctx.rows([entry], ("synopsis_en_ref",)).get((entry, "synopsis_en_ref"))
        cards = [first, role]
        story_fid = None
        if story:
            story_fid = d.fact(f"〈{title}〉의 이야기는 details 의 참고 글에 있다. 문장을 옮기지 말고 아주 짧게 새로 쓴다.",
                               story[0], "story_reference", story_reference_do_not_copy=ref_text(story[0].value, 700))
        desc_fid = None
        if desc:
            desc_fid = d.fact(f"{name}의 성격 · 특징은 details 의 참고 글에 있다. 문장을 옮기지 말고 새로 쓴다.", desc,
                              "personality_reference",
                              personality_reference_do_not_copy=ref_text(PROFILE_LINE.sub("", str(desc.value))))
            cards.append(desc_fid)
        d.card_facts = [x for x in cards if x]
        profile = add_char_facts(ctx, d, c, title, profile_rows, with_birthday=False)
        # 11/11 서원: 같은 유형은 같은 모양으로 — 모든 장 작은 글씨 없음, 표지 큰 글씨 고정, 작품명은 〈〉,
        # 표지는 그 캐릭터 얼굴 · 2장은 그 애니 그림 · 3장은 성격(소개 글이 없으면 프로필)
        d.page(work=w, image=("character", ctx.anilist[c]), eyebrow="",
               title=[f"〈{title}〉 {name}", f"{when.month}월 {when.day}일, 생일 축하해!"], facts=[first],
               note="본문은 비우거나 아주 짧은 축하 한마디")
        d.page(work=w, image=("work", w), eyebrow="", facts=[role, story_fid],
               note="나오는 작품과 아주 짧은 줄거리. 작품명은 〈〉로 감싼다")
        if desc_fid:
            d.page(work=w, image=("still", w), eyebrow="", facts=[desc_fid],
                   note="이 캐릭터의 성격이나 특징 · 역할을 한두 문장")
        elif profile:
            d.page(work=w, image=("still", w), eyebrow="", facts=profile,
                   note="이 캐릭터의 프로필(나이 · 혈액형 · 성우 등 사실에 있는 것만)을 짧게")
        add_work_summary(d, w, ctx.rows([entry], WORK_FIELDS))
        d.plan = [("character", ctx.anilist[c], name, w, [first], True), ("kitsu", w, 3)]
        decks.append(d)
    return decks


# ---------- 그림 ----------

last_kitsu = 0.0
last_anilist = 0.0


def get_bytes(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):  # 없음 · 기한 지난 서명 주소는 다시 해도 같음 (11/11)
                raise
            time.sleep(10 * (attempt + 1))
        except (OSError, HTTPException):
            time.sleep(10 * (attempt + 1))
    raise SystemExit(f"요청이 4번 연속 실패함: {url}")


def kitsu_json(path: str) -> dict:
    """Kitsu 응답 하나 (받은 것은 파일로 재사용)"""
    global last_kitsu
    cache = TRIAL / "raw" / "kitsu" / (hashlib.sha1(path.encode()).hexdigest() + ".json")
    if cache.exists():
        text = cache.read_text(encoding="utf-8")
        # Kitsu 그림 주소는 15분짜리 서명 주소라, 10분 넘은 응답은 다시 받는다
        # (11/11: 기한 지난 주소를 4번 다시 받다가 대량 글감 만들기가 멈춤)
        if "X-Amz-Expires" not in text or time.time() - cache.stat().st_mtime < 600:
            return json.loads(text)
    time.sleep(max(0.0, last_kitsu + KITSU_PAUSE - time.time()))
    last_kitsu = time.time()
    data = json.loads(get_bytes(f"{KITSU}/{path}", {"Accept": "application/vnd.api+json"}))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def anilist_portraits(ids: list[str]) -> dict[str, str]:
    """캐릭터 번호 → 그림 주소 (한 번에 50명, 받은 것은 파일로 재사용)"""
    global last_anilist
    cache = TRIAL / "raw" / "anilist_portraits.json"
    known: dict[str, str] = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    missing = sorted({i for i in ids if i not in known}, key=int)
    for s in range(0, len(missing), 50):
        time.sleep(max(0.0, last_anilist + ANILIST_PAUSE - time.time()))
        last_anilist = time.time()
        query = "query ($ids: [Int]) { Page(page: 1, perPage: 50) { characters(id_in: $ids) { id image { large } } } }"
        data, _ = post(query, {"ids": [int(i) for i in missing[s:s + 50]]})
        if data.get("errors"):
            raise SystemExit(f"AniList 오류: {data['errors'][:1]}")
        for c in data["data"]["Page"]["characters"]:
            known[str(c["id"])] = (c["image"] or {}).get("large") or ""
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known, ensure_ascii=False), encoding="utf-8")
    return known


def anilist_staff_photos(ids: list[str]) -> dict[str, str]:
    """AniList 인물 번호 → 사진 주소 (한 번에 50명, 받은 것은 재사용). 기본 그림(default)은 빈 값"""
    global last_anilist
    cache = TRIAL / "raw" / "anilist_staff_photos.json"
    known: dict[str, str] = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    missing = sorted({i for i in ids if i not in known}, key=int)
    for s in range(0, len(missing), 50):
        time.sleep(max(0.0, last_anilist + ANILIST_PAUSE - time.time()))
        last_anilist = time.time()
        query = "query ($ids: [Int]) { Page(page: 1, perPage: 50) { staff(id_in: $ids) { id image { large } } } }"
        data, _ = post(query, {"ids": [int(i) for i in missing[s:s + 50]]})
        if data.get("errors"):
            raise SystemExit(f"AniList 오류: {data['errors'][:1]}")
        for p in data["data"]["Page"]["staff"]:
            url = (p.get("image") or {}).get("large") or ""
            known[str(p["id"])] = "" if "default" in url.rsplit("/", 1)[-1] else url
        for i in missing[s:s + 50]:
            known.setdefault(i, "")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known, ensure_ascii=False), encoding="utf-8")
    return known


def anilist_covers(ids: list[str], media: str = "ANIME") -> dict[str, str]:
    """AniList 작품 번호 → 표지 주소 (한 번에 50편, 받은 것은 재사용).
    ANIME: Kitsu 포스터가 없는 극장판. MANGA: 원작 책 표지 (11/11 서원: 작가 글 표지에 책 사진)"""
    global last_anilist
    cache = TRIAL / "raw" / ("anilist_covers.json" if media == "ANIME" else "anilist_manga_covers.json")
    known: dict[str, str] = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    missing = sorted({i for i in ids if i not in known}, key=int)
    for s in range(0, len(missing), 50):
        time.sleep(max(0.0, last_anilist + ANILIST_PAUSE - time.time()))
        last_anilist = time.time()
        query = (f"query ($ids: [Int]) {{ Page(page: 1, perPage: 50) {{ media(id_in: $ids, type: {media}) "
                 "{ id coverImage { extraLarge large } } } }")
        data, _ = post(query, {"ids": [int(i) for i in missing[s:s + 50]]})
        if data.get("errors"):
            raise SystemExit(f"AniList 오류: {data['errors'][:1]}")
        for m in data["data"]["Page"]["media"]:
            cover = m.get("coverImage") or {}
            known[str(m["id"])] = cover.get("extraLarge") or cover.get("large") or ""
        for i in missing[s:s + 50]:
            known.setdefault(i, "")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known, ensure_ascii=False), encoding="utf-8")
    return known


def image_size(data: bytes) -> tuple[int, int, str] | None:
    """PNG · JPEG 크기와 형식 (파일을 해석하지 않고 머리말만 읽음)"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return *struct.unpack(">II", data[16:24]), "image/png"
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h, "image/jpeg"
        if marker == 0xFF:
            i += 1
            continue
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def add_image(deck: Deck, url: str, page: str, publisher: str, hosts: list[str], work: int, kind: str, label: str,
              entities: list[str], related: list[str] | None = None, tagged: bool = True) -> None:
    """글에 쓸 그림 하나. tagged=False 는 대표 작품 밖의 그림 (근거 페이지에 있는 그림으로만 연결)"""
    if any(img["url"] == url for img in deck.images):
        return
    folder = TRIAL / "images" / deck.request_id
    folder.mkdir(parents=True, exist_ok=True)
    # 확장자는 주소의 경로에서만 (11/11: '.jpg?x-amz-...' 서명값이 붙은 Kitsu 주소로 저장이 안 돼 대량 글감 만들기가 멈춤)
    suffix = pathlib.PurePosixPath(urllib.parse.urlsplit(url).path).suffix.lower()[:5]
    path = folder / (hashlib.sha1(url.encode()).hexdigest()[:16] + suffix)
    if path.exists():
        data = path.read_bytes()
    else:
        try:
            data = get_bytes(url)
        except (urllib.error.HTTPError, SystemExit):  # 그림 하나를 못 받으면 그 그림만 건너뜀 (11/11)
            return
        path.write_bytes(data)
    size = image_size(data)
    if size is None:
        return
    if size[0] * size[1] > 30_000_000 or len(data) > 15_000_000:
        # 저장 한도(4,000만 픽셀 · 20MB)를 넘는 아주 큰 포스터는 줄여서 쓴다 (11/11: 5829×8275 포스터로 실행이 멈춤)
        from PIL import Image
        with Image.open(path) as source:
            small = source.convert("RGB")
        small.thumbnail((3000, 3000))
        path = path.with_suffix(".jpg")
        small.save(path, "JPEG", quality=90)
        data = path.read_bytes()
        size = image_size(data)
    title = f"{deck.ctx.title[work]} — {label}"
    if not entities and tagged and kind in ("catalog_poster", "catalog_cover", "scene_still"):
        # 검수 · 그림 고르기가 어느 작품 그림인지 알게 작품 이름을 붙인다
        # (11/11: 〈종말의 세라프〉 포스터를 검수가 겉모습만 보고 귀멸이라고 막음)
        entities = [deck.ctx.title[work]]
        label = f"{label} · {deck.ctx.title[work]}"
    evidence = {"id": f"{publisher.lower()}_image_reference", "title": title, "url": page, "publisher": publisher,
                "is_official": False, "role": "reference", "region": "global", "allowed_asset_hosts": hosts}
    deck.images.append({
        "id": f"{publisher.lower()}-{hashlib.sha1(url.encode()).hexdigest()[:16]}", "url": url, "local_path": str(path),
        "source_url": page, "provenance_source": evidence, "menu_work_ids": [deck.ctx.menu_id[work]] if tagged else [],
        "official": False, "related_fact_ids": related or [],
        "rights_status": "public_editorial_risk_accepted", "width": size[0], "height": size[1], "media_type": size[2],
        "file_size_bytes": len(data), "fingerprint": hashlib.sha256(data).hexdigest(), "asset_kind": kind,
        "source_description": f"{publisher} {label}", "depicted_entities": entities, "asset_metadata_confidence": 0.98,
        "visual_match": "context", "rights_evidence_url": page,
        "rights_checked_at": dt.datetime.now(dt.UTC).isoformat(),
        "attribution_text": f"공개 {publisher} 카탈로그 이미지 참고: {title} (사용 허가 확인과는 별개)",
        "download_status": "available"})


def collect_images(deck: Deck) -> None:
    """글 하나의 그림 계획대로 받는다 (캐릭터 그림은 번호로, 작품 그림은 Kitsu 에서)"""
    ctx = deck.ctx
    portraits = anilist_portraits([p[1] for p in deck.plan if p[0] == "character"])
    covers = anilist_covers([ctx.anilist[p[1]] for p in deck.plan if p[0] == "anilist_cover" and p[1] in ctx.anilist])
    photos = anilist_staff_photos([p[1] for p in deck.plan if p[0] == "staff"])
    books = anilist_covers([p[1] for p in deck.plan if p[0] == "manga_cover"], "MANGA")
    for step in deck.plan:
        if step[0] == "manga_cover":  # 원작 책 표지 (AniList 만화 · 소설)
            _, mid, work, label = step
            if books.get(mid):
                add_image(deck, books[mid], f"https://anilist.co/manga/{mid}", "AniList", ["s4.anilist.co"], work,
                          "catalog_cover", "book cover", [label])
            continue
        if step[0] == "staff":  # 가수 사진 (AniList 인물 사진, 기본 그림이면 안 씀)
            _, sid, name, work = step
            if photos.get(sid):
                add_image(deck, photos[sid], f"https://anilist.co/staff/{sid}", "AniList", ["s4.anilist.co"], work,
                          "editorial_photo", f"artist photo {name}", [name])
            continue
        if step[0] == "anilist_cover":  # Kitsu 포스터가 없는 극장판 (11/11: 순위에서 빠지지 않게)
            _, entry, work = step
            aid = ctx.anilist.get(entry)
            if aid and covers.get(aid):
                add_image(deck, covers[aid], f"https://anilist.co/anime/{aid}", "AniList", ["s4.anilist.co"], work,
                          "catalog_cover", "catalog poster", [])
            continue
        if step[0] == "kitsu_entries":  # 한 작품의 시즌 · 극장판마다 포스터
            work = step[1]
            for entry in [e for e in ctx.work_entries[work] if e in ctx.kitsu][:8]:
                kid = ctx.kitsu[entry]
                poster = kitsu_json(kid)["data"]["attributes"].get("posterImage") or {}
                url = poster.get("original") or poster.get("large")
                if url:
                    add_image(deck, url, f"{KITSU}/{kid}", "Kitsu", ["media.kitsu.app"], work, "catalog_poster",
                              "catalog poster", [])
            continue
        if step[0] == "kitsu_one":  # 극장판 하나의 포스터
            _, entry, work = step
            kid = ctx.kitsu[entry]
            poster = kitsu_json(kid)["data"]["attributes"].get("posterImage") or {}
            url = poster.get("original") or poster.get("large")
            if url:
                add_image(deck, url, f"{KITSU}/{kid}", "Kitsu", ["media.kitsu.app"], work, "catalog_poster",
                          "catalog poster", [])
            continue
        if step[0] == "character":
            _, aid, name, work, *rest = step
            if portraits.get(aid):
                add_image(deck, portraits[aid], f"https://anilist.co/character/{aid}", "AniList", ["s4.anilist.co"],
                          work, "character_art", f"character art {name}", [name], rest[0] if rest else None,
                          rest[1] if len(rest) > 1 else True)
            continue
        _, work, scenes = step
        kid = ctx.kitsu[ctx.kitsu_entry(work)]
        attrs = kitsu_json(kid)["data"]["attributes"]
        endpoint = f"{KITSU}/{kid}"
        poster = attrs.get("posterImage") or {}
        url = poster.get("original") or poster.get("large")
        if url:
            add_image(deck, url, endpoint, "Kitsu", ["media.kitsu.app"], work, "catalog_poster", "catalog poster", [])
        if scenes:
            episodes = kitsu_json(f"{kid}/episodes?page%5Blimit%5D=20&sort=number")["data"]
            picked = [e for e in episodes if e["attributes"].get("thumbnail")]
            gap = max(1, len(picked) // max(1, scenes))
            for e in picked[::gap][:scenes]:
                thumb = e["attributes"]["thumbnail"]
                if thumb.get("original"):
                    add_image(deck, thumb["original"], f"{endpoint}/episodes", "Kitsu", ["media.kitsu.app"], work,
                              "scene_still", "episode still", [])


def studio_logo(qid: str) -> pathlib.Path | None:
    """제작사 로고: 위키데이터 로고(P154) 파일을 위키미디어 공용에서 PNG(너비 800)로 받는다. 없으면 None (결과는 재사용)"""
    cache = TRIAL / "raw" / "logos" / f"{qid}.png"
    if cache.exists():
        return cache if cache.stat().st_size else None
    cache.parent.mkdir(parents=True, exist_ok=True)
    try:
        entity = json.loads(get_bytes(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"))
        claims = entity["entities"][next(iter(entity["entities"]))]["claims"].get("P154") or []
        name = claims[0]["mainsnak"]["datavalue"]["value"] if claims else None
        data = (get_bytes("https://commons.wikimedia.org/wiki/Special:FilePath/"
                          + urllib.parse.quote(name.replace(" ", "_")) + "?width=800") if name else b"")
    except (urllib.error.URLError, KeyError, ValueError):
        data = b""
    if data and image_size(data) is None:  # PNG · JPEG 가 아니면 쓰지 않는다
        data = b""
    cache.write_bytes(data)
    time.sleep(1.0)
    return cache if data else None


def make_collage(deck: Deck) -> None:
    """순위 · 수상작 표지: 받은 포스터를 바둑판으로 한 장에 모은다 (11/11 서원). 카드 비율 327:408 의 4배 크기.
    제작사 글이면 바둑판을 어둡게 깔고 가운데 흰 판에 제작사 로고를 넣는다"""
    from PIL import Image  # 이 기능에서만 쓴다

    items = max(3, len(deck.card_plan) - 1)  # 표지 다음 장들(순위 · 수상작)의 포스터만
    if deck.kind == "author":  # 작가 글: 원작 책 표지만
        posters = [img for img in deck.images if "/manga/" in img["source_url"]]
    elif deck.kind == "profile":  # 프로필 글: 주연 얼굴
        posters = [img for img in deck.images if img["asset_kind"] == "character_art"]
    else:
        posters = [img for img in deck.images if img["asset_kind"] in ("catalog_poster", "catalog_cover")]
    posters = posters[:min(6, items)]
    if len(posters) < 3:
        return
    width, height = 1308, 1632
    cols = 2 if len(posters) <= 4 else 3
    rows = -(-len(posters) // cols)
    cell_w, cell_h = width // cols, height // rows
    canvas = Image.new("RGB", (width, height), (17, 17, 21))
    for k, img in enumerate(posters):
        with Image.open(img["local_path"]) as source:
            tile = source.convert("RGB")
        # 칸에 맞추되 작은 그림(캐릭터 230×345)은 2배까지만 키운다 (더 키우면 흐려짐)
        scale = min((cell_w - 16) / tile.width, (cell_h - 16) / tile.height, 2.0)
        tile = tile.resize((max(1, int(tile.width * scale)), max(1, int(tile.height * scale))), Image.LANCZOS)
        row, col = divmod(k, cols)
        in_row = min(cols, len(posters) - row * cols)  # 마지막 줄이 덜 차면 가운데로
        left = (width - in_row * cell_w) // 2 + col * cell_w
        canvas.paste(tile, (left + (cell_w - tile.width) // 2, row * cell_h + (cell_h - tile.height) // 2))
    logo = studio_logo(deck.logo_qid) if deck.logo_qid else None
    if logo:
        canvas = Image.blend(canvas, Image.new("RGB", canvas.size, (10, 10, 14)), 0.6)
        with Image.open(logo) as source:
            mark = source.convert("RGBA")
        panel_w = int(width * 0.74)
        mark.thumbnail((panel_w - 120, 420))
        panel = Image.new("RGBA", (panel_w, mark.height + 120), (255, 255, 255, 245))
        top = int(height * 0.32) - panel.height // 2  # 글씨가 올라가는 아래쪽을 비워 둔다
        canvas.paste(panel, ((width - panel_w) // 2, top), panel)
        canvas.paste(mark, ((width - mark.width) // 2, top + 60), mark)
    path = TRIAL / "images" / deck.request_id / "collage.png"
    canvas.save(path, "PNG", optimize=True)
    data = path.read_bytes()
    first = posters[0]
    titles = [deck.ctx.title[w] for w in deck.works]
    deck.images.insert(0, {
        # 주소: 그림 사용 조건이 '허용된 사이트 주소'를 보므로 첫 포스터 주소를 쓰되, 그대로 쓰면 그림 고르기 단계가
        # 1위 포스터와 같은 그림으로 묶어 버려서 구분용 질의값을 붙인다 (11/11 바둑판 표지가 빠지던 문제)
        **first, "id": f"collage-{hashlib.sha1(data).hexdigest()[:16]}",
        "url": f"{first['url']}{'&' if '?' in first['url'] else '?'}collage={hashlib.sha1(data).hexdigest()[:16]}",
        "local_path": str(path), "menu_work_ids": [deck.ctx.menu_id[w] for w in deck.works], "related_fact_ids": [],
        "width": width, "height": height, "media_type": "image/png", "file_size_bytes": len(data),
        "fingerprint": hashlib.sha256(data).hexdigest(),
        "source_description": (f"제작사 로고와 작품 포스터 {len(posters)}장을 모은 표지" if logo
                               else f"카탈로그 포스터 {len(posters)}장을 바둑판으로 모은 그림"),
        "depicted_entities": titles,
        "attribution_text": f"공개 Kitsu 카탈로그 포스터 {len(posters)}장을 한 장에 모음 (사용 허가 확인과는 별개)"})


def resolve_plan(deck: Deck) -> bool:
    """장별 계획의 그림 열쇠를 실제 그림 번호로 바꾼다. 장마다 다른 그림, 그 카드 작품의 그림만 쓴다.
    알맞은 그림이 없는 작품 카드는 뺀다 (다른 작품 그림을 붙이면 검수에서 막힘 — 11/10 종말의 세라프 카드)"""
    ctx = deck.ctx
    used: set[str] = set()

    def match(img: dict, key: tuple) -> bool:
        kind = key[0]
        if kind == "collage":
            return img["id"].startswith("collage-")
        if img["id"].startswith("collage-"):
            return False
        if kind == "character":
            return img["source_url"] == f"https://anilist.co/character/{key[1]}"
        if kind == "staff":
            return img["source_url"] == f"https://anilist.co/staff/{key[1]}"
        if kind == "entry":
            kid, aid = ctx.kitsu.get(key[1]), ctx.anilist.get(key[1])
            return ((bool(kid) and img["asset_kind"] == "catalog_poster" and img["source_url"] == f"{KITSU}/{kid}")
                    or (bool(aid) and img["asset_kind"] == "catalog_cover"
                        and img["source_url"] == f"https://anilist.co/anime/{aid}"))
        work = key[1]
        if img["menu_work_ids"] != [ctx.menu_id[work]]:
            return False
        if kind == "still":
            return img["asset_kind"] == "scene_still"
        if kind == "work":
            entry = ctx.kitsu_entry(work)
            return (img["asset_kind"] == "catalog_poster" and entry is not None
                    and img["source_url"] == f"{KITSU}/{ctx.kitsu[entry]}")
        return kind == "any_of_work"

    resolved = []
    for page in deck.card_plan:
        work = page.get("work")
        keys = [k for k in (page.get("image"), ("work", work) if work else None, ("still", work) if work else None,
                            ("any_of_work", work) if work else None) if k]
        img = next((i for k in keys for i in deck.images if i["id"] not in used and match(i, k)), None)
        if img is None:
            if not resolved:
                return False  # 표지 그림이 없으면 글을 만들지 않는다
            continue
        used.add(img["id"])
        resolved.append({key: value for key, value in page.items() if key not in ("image", "work")}
                        | {"image_id": img["id"]})
    if len(resolved) < min(3, len(deck.card_plan)):
        return False
    for number, page in enumerate(resolved, 1):
        page["page"] = number
        page["role"] = "cover" if number == 1 else ("ending" if number == len(resolved) else "detail")
    deck.resolved_plan = resolved
    deck.target_cards = len(resolved)
    return True


def main() -> None:
    global MASS
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=",".join(ALL_TYPES))
    parser.add_argument("--mass", action="store_true", help="대량 생산: 넓은 범위 · 작품 겹침 허용 · 결과는 .cache/mass")
    parser.add_argument("--cap", type=int, default=100, help="대량 생산에서 유형마다 최대 글 수 (이달의 생일은 전부)")
    parser.add_argument("--count", action="store_true", help="그림을 받지 않고 고른 글감 수만 센다")
    args = parser.parse_args()
    known = (*ALL_TYPES, *NEW_TYPES)
    only = [t for t in known if t in args.only.split(",")]
    out = TRIAL
    if args.mass:
        MASS = True
        out = PACKAGE_ROOT / ".cache" / "mass"
        for kind in QUOTA:
            QUOTA[kind] = args.cap
        QUOTA["bdaymonth"] = 10 ** 6
        QUOTA["birthday"] = 0  # 예전 '다가오는 생일'은 이달의 생일로 바뀜
    settings = load_settings()
    pickers = {"curation": pick_curation, "music": pick_music, "people": pick_people, "character": pick_character,
               "guide": pick_guide, "krva": pick_krva, "origin": pick_origin, "place": pick_place,
               "boxoffice": pick_boxoffice, "award": pick_award, "bdaymonth": pick_birthday_month}
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        ctx = Ctx(cur)
        if set(only) & set(NEW_TYPES) and not MASS:
            mark_trial_works(ctx)
        decks = [d for t in known if t in only for d in pickers[t](ctx)]
        ctx.cur = None  # 그림 단계에서는 DB 를 쓰지 않음
        conn.rollback()
    if args.count:
        picked = Counter(d.kind for d in decks)
        print("고른 글감 (그림 받기 전, 유형마다 최대 " + (f"{args.cap}개" if MASS else "시범 할당량") + "):")
        for kind, n in picked.most_common():
            print(f"  {kind}: {min(n, QUOTA[kind])}")
        print(f"  합계 {sum(min(n, QUOTA[k]) for k, n in picked.items())}")
        return
    (out / "manifests").mkdir(parents=True, exist_ok=True)
    (out / "fixtures").mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = defaultdict(int)
    for d in decks:
        if written[d.kind] >= QUOTA[d.kind]:
            continue  # 목표를 채웠으면 예비 글감은 그림도 받지 않음
        if MASS and (out / "manifests" / f"{d.request_id}.json").exists():
            written[d.kind] += 1  # 이미 만든 글감은 건너뜀 (끊겨도 이어서)
            continue
        collect_images(d)
        if any(p["image"] == ("collage",) for p in d.card_plan):
            make_collage(d)
        if d.card_plan and not resolve_plan(d):
            print(f"건너뜀 {d.request_id}: 장별 계획에 맞는 그림이 모자람 (그림 {len(d.images)}장)")
            continue
        if len(d.images) < (d.target_cards or 3) or not d.facts:
            print(f"건너뜀 {d.request_id}: 그림 {len(d.images)}장 · 사실 {len(d.facts)}개")
            continue
        written[d.kind] += 1
        (out / "manifests" / f"{d.request_id}.json").write_text(
            json.dumps(d.manifest(), ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "fixtures" / f"{d.request_id}.json").write_text(
            json.dumps(d.fixture(), ensure_ascii=False, indent=1), encoding="utf-8")
        sizes = sorted((img["width"], img["height"]) for img in d.images)
        print(f"{d.request_id} | {d.topic} | 사실 {len(d.facts)} · 그림 {len(d.images)} · "
              f"그림 크기 {sizes[0]}~{sizes[-1]}")


if __name__ == "__main__":
    main()
