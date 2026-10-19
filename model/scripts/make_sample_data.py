# make_sample_data — v0 테스트용 샘플 데이터(작품·콘텐츠·유저)를 만든다
#
# 진짜 콘텐츠가 나오기 전(10/16)까지 v0 추천을 돌려 보려고 만든 가짜 데이터다.
# - 작품: 실제 애니 18편. 장르·태그는 AniList 형식을 흉내 냈다 (일부 값은 예시)
# - 콘텐츠: 작품마다 유형별로 몇 개씩, 콘텐츠 명세서 v0.2의 칸을 채워서 만든다
# - 유저: 팀원을 본뜬 페르소나 6명
#
#   python model/scripts/make_sample_data.py
#   → model/sample/ 아래에 entities.json · contents.json · users.json
#
# 같은 시드면 항상 같은 결과가 나온다.

import json
import pathlib
import random
from datetime import datetime, timedelta, timezone

SEED = 20261014
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
OUT = pathlib.Path(__file__).resolve().parents[1] / "sample"

# 작품 = entity. attributes 안의 genres·tags 는 entity.attributes 형식을 따른다
ENTITIES = [
    ("frieren", "장송의 프리렌", ["Adventure", "Drama", "Fantasy"], {"Elf": 90, "Travel": 85}),
    ("aot", "진격의 거인", ["Action", "Drama", "Fantasy", "Mystery"], {"Kaiju": 93, "Revenge": 93, "Military": 88}),
    ("kny", "귀멸의 칼날", ["Action", "Adventure", "Drama", "Fantasy", "Supernatural"], {"Demons": 96, "Swordplay": 88}),
    ("jjk", "주술회전", ["Action", "Drama", "Supernatural"], {"Urban Fantasy": 93, "Curses": 90}),
    ("mha", "나의 히어로 아카데미아", ["Action", "Adventure", "Comedy"], {"Super Power": 98, "Superhero": 97}),
    ("onepiece", "원피스", ["Action", "Adventure", "Comedy", "Drama", "Fantasy"], {"Pirates": 98, "Travel": 94}),
    ("deathnote", "데스노트", ["Mystery", "Psychological", "Supernatural", "Thriller"], {"Crime": 96, "Detective": 95}),
    ("tokyoghoul", "도쿄 구울", ["Action", "Drama", "Horror", "Mystery", "Psychological", "Supernatural"], {"Gore": 90, "Urban Fantasy": 89}),
    ("opm", "원펀맨", ["Action", "Comedy", "Sci-Fi", "Supernatural"], {"Superhero": 98, "Parody": 91}),
    ("hxh", "헌터×헌터", ["Action", "Adventure", "Fantasy"], {"Super Power": 91, "Travel": 82}),
    ("spyfamily", "스파이 패밀리", ["Action", "Comedy", "Slice of Life"], {"Espionage": 90, "Found Family": 88}),
    ("haikyu", "하이큐!!", ["Comedy", "Drama", "Sports"], {"Volleyball": 98, "Team Sports": 92}),
    ("bocchi", "봇치 더 록!", ["Comedy", "Music", "Slice of Life"], {"Band": 97, "Social Anxiety": 90}),
    ("oshinoko", "최애의 아이", ["Drama", "Mystery", "Psychological", "Supernatural"], {"Idol": 95, "Reincarnation": 90}),
    ("csm", "체인소 맨", ["Action", "Drama", "Horror", "Supernatural"], {"Demons": 92, "Gore": 90}),
    ("apothecary", "약사의 혼잣말", ["Drama", "Mystery"], {"Medicine": 92, "Historical": 90}),
    ("dungeonmeshi", "던전밥", ["Adventure", "Comedy", "Fantasy"], {"Food": 96, "Dungeon": 92}),
    ("bluelock", "블루 록", ["Drama", "Sports"], {"Football": 98, "Battle Royale": 70}),
]

# (content_type, subcategory, 제목 틀, 스포일러 등급 후보)
TEMPLATES = [
    ("news", "애니화·후속작 발표", "{n} 신작 시즌 제작 확정", [0]),
    ("news", "방영·OTT 공개 일정", "{n} 국내 OTT 공개일 정리", [0]),
    ("news", "PV·키비주얼 공개", "{n} 새 PV에서 확인된 것들", [0, 1]),
    ("guide", "입문 가이드", "{n}, 몇 화까지 보면 빠져들까", [0]),
    ("guide", "정주행 순서", "{n} 시리즈 보는 순서 한 번에 정리", [0]),
    ("guide", "세계관·용어 정리", "{n} 세계관 용어 10개", [1]),
    ("people", "성우 출연작", "{n} 주연 성우의 다른 대표작", [0]),
    ("people", "감독·작가·스튜디오 소개", "{n} 제작 스튜디오 이야기", [0]),
    ("curation", "취향 연결", "{n} 좋아하면 이것도 볼 만해", [0]),
    ("event", "팝업스토어", "{n} 팝업스토어 오픈", [0]),
    ("event", "전시·원화전", "{n} 원화전 일정과 예매 정보", [0]),
    ("goods", "피규어·굿즈 예약", "{n} 신규 피규어 예약 시작", [0]),
    ("behind", "제작 비하인드", "{n} 제작진이 밝힌 비하인드", [1]),
    ("review", "작품 리뷰", "{n} 최종장, 어떻게 끝났나", [2, 3]),
]

REGIONS = ["서울", "서울", "서울", "경기", "부산", "광주", "대구"]

# 팀원을 본뜬 페르소나. 취향은 일부 지어냈다
USERS = [
    # 고어·다크 판타지·아포칼립스
    {"user_id": "seowon", "name": "서원", "content_types": ["review", "behind", "curation"],
     "genres": ["Horror", "Psychological", "Fantasy"], "favorite_works": ["csm", "tokyoghoul", "aot"]},
    # 소년물·성장물·먼치킨
    {"user_id": "junseong", "name": "준성", "content_types": ["news", "guide"],
     "genres": ["Action", "Adventure", "Comedy"], "favorite_works": ["onepiece", "hxh", "opm", "mha"]},
    # 막내. 팝업스토어 오픈런 담당
    {"user_id": "taewoong", "name": "태웅", "content_types": ["event", "goods", "news"],
     "genres": ["Sports", "Comedy"], "favorite_works": ["bluelock", "haikyu"]},
    # 그 시절 명작파
    {"user_id": "guseong", "name": "구성", "content_types": ["people", "behind"],
     "genres": ["Mystery", "Thriller", "Psychological"], "favorite_works": ["deathnote", "hxh"]},
    # 원피스 1화부터 본 큰형
    {"user_id": "beomsu", "name": "범수", "content_types": ["people", "behind", "news"],
     "genres": ["Action", "Adventure", "Drama"], "favorite_works": ["onepiece", "kny"]},
    # 차분하게 혼자 정주행
    {"user_id": "yeonju", "name": "연주", "content_types": ["curation", "guide"],
     "genres": ["Slice of Life", "Mystery", "Drama"], "favorite_works": ["apothecary", "frieren", "bocchi"]},
]


def iso(dt):
    return dt.isoformat()


def main():
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    entities = [
        {"entity_id": eid, "entity_type": "작품", "name": name,
         "attributes": {"genres": genres, "tags": [{"name": k, "rank": v} for k, v in tags.items()]}}
        for eid, name, genres, tags in ENTITIES
    ]

    contents = []
    cid = 1
    for eid, name, _genres, _tags in ENTITIES:
        # 작품마다 유형 5~7개를 골라 콘텐츠를 만든다
        for ctype, sub, title, spoilers in rng.sample(TEMPLATES, rng.randint(5, 7)):
            published = NOW - timedelta(days=rng.uniform(0, 30), hours=rng.uniform(0, 23))
            c = {
                "content_id": cid,
                "topic_id": "anime",
                "content_type": ctype,
                "subcategory": sub,
                "depth": rng.choice(["intro", "mania"]) if ctype in ("guide", "review", "behind") else "intro",
                "title": title.format(n=name),
                "summary": f"{name} 관련 {sub} 콘텐츠 (샘플)",
                "primary_entity_id": eid,
                "spoiler_level": rng.choice(spoilers),
                "spoiler_entity_id": None,
                "published_at": iso(published),
                "expires_at": None,
                "event": None,
                "status": "게시중",
                "is_sample": True,
            }
            if c["spoiler_level"] >= 1:
                c["spoiler_entity_id"] = eid
            if ctype == "event":
                starts = published + timedelta(days=rng.randint(1, 10))
                # 일부는 이미 끝난 행사 (필터 확인용)
                ends = starts + timedelta(days=rng.choice([-20, 14, 21, 30]))
                c["event"] = {"starts_on": starts.date().isoformat(), "ends_on": ends.date().isoformat(),
                              "region": rng.choice(REGIONS)}
            if ctype == "goods":
                c["expires_at"] = iso(published + timedelta(days=rng.choice([5, 20, 40])))
            contents.append(c)
            cid += 1

    users = []
    for u in USERS:
        u = dict(u)
        u["is_sample"] = True
        u["seen_content_ids"] = []
        u["feedback"] = []  # [{content_id, action}] action = like / save / more / less
        users.append(u)

    # 서원: 체인소 맨 콘텐츠 하나는 저장, 하나는 이미 봄
    seowon = next(u for u in users if u["user_id"] == "seowon")
    csm = [c for c in contents if c["primary_entity_id"] == "csm"]
    seowon["feedback"] = [{"content_id": csm[0]["content_id"], "action": "save"}]
    seowon["seen_content_ids"] = [csm[0]["content_id"]]

    # 준성: 히로아카 콘텐츠 하나에 "그만볼래요" (좋아하는 작품이어도 반응이 이기는지 확인)
    junseong = next(u for u in users if u["user_id"] == "junseong")
    mha = [c for c in contents if c["primary_entity_id"] == "mha"]
    junseong["feedback"] = [{"content_id": mha[0]["content_id"], "action": "less"}]

    for fname, data in [("entities.json", entities), ("contents.json", contents), ("users.json", users)]:
        # 윈도우에서 만들어도 줄바꿈을 LF로 맞춘다 (OS마다 파일이 달라지지 않게)
        (OUT / fname).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"작품 {len(entities)} · 콘텐츠 {len(contents)} · 유저 {len(users)} → {OUT}")


if __name__ == "__main__":
    main()
