# make_chip_catalog — 온보딩 Q2 칩 목록과 작품 ↔ 칩 연결을 만든다 (model/catalog/chips.csv · work_chips.csv)
#
# 재료: AniList 작품 정보 (model/data/anilist/media.jsonl · media_recent.jsonl, 커밋 안 함) + me;nu 작품 목록 (model/catalog)
# chips.csv: AniList 장르 · 태그 전부 (성인 장르 · 태그는 뺌). 화면 칩은 show = main 인 35개 (온보딩 시안의 장르 16 · 소재 19)
#   나머지는 off 로 두고 한국어 이름은 비워 둠 (name_check = 입력 필요). 더보기가 정해지면 show · 한국어 이름만 고치면 됨
# work_chips.csv: me;nu 작품(시리즈)마다 붙는 칩
#   한 편: 장르 = AniList 장르 그대로. 태그 = 관련도 60% 이상 (50% 와 붙는 작품 비율 차이 3%p, 예시로 60% 가 더 정확해서 고름)
#   스포일러 표시가 붙은 태그는 뺌 (온보딩 힌트에 스포일러가 보이면 안 됨)
#   시리즈: 그 칩이 붙은 편들의 인기 합이 시리즈 인기 합의 절반 이상일 때만 붙임. 한 편이라도 붙으면 붙이던 규칙은
#   번외편 하나로 칩이 붙음 (진격의 거인 코미디 = 인기 0.3% 특별편. 화면 칩 10개 이상 붙는 작품 81 → 24개)
#
#   python model/scripts/make_chip_catalog.py
import csv
import json
import pathlib
import re
from collections import Counter, defaultdict

MODEL = pathlib.Path(__file__).resolve().parents[1]
CATALOG = MODEL / "catalog"
ANILIST = MODEL / "data" / "anilist"
TAG_MIN_RANK = 60
SERIES_MIN_SHARE = 0.5
ADULT_GENRES = {"Hentai"}

# 온보딩 시안 (Q2 칩 목록 · 저장 값) 그대로: (한국어 이름, 코드, AniList 이름)
MAIN_GENRES = [
    ("코미디", "comedy", "Comedy"), ("액션", "action", "Action"), ("드라마", "drama", "Drama"),
    ("로맨스", "romance", "Romance"), ("판타지", "fantasy", "Fantasy"), ("일상", "slice_of_life", "Slice of Life"),
    ("모험", "adventure", "Adventure"), ("초자연", "supernatural", "Supernatural"), ("SF", "sci_fi", "Sci-Fi"),
    ("미스터리", "mystery", "Mystery"), ("심리", "psychological", "Psychological"), ("스포츠", "sports", "Sports"),
    ("호러", "horror", "Horror"), ("로봇", "mecha", "Mecha"), ("음악", "music", "Music"), ("스릴러", "thriller", "Thriller"),
]
MAIN_THEMES = [
    ("학원", "school", "School"), ("비극", "tragedy", "Tragedy"), ("마법", "magic", "Magic"),
    ("성장", "coming_of_age", "Coming of Age"), ("검술", "swordplay", "Swordplay"), ("하렘", "harem", "Female Harem"),
    ("초능력", "super_power", "Super Power"), ("고어", "gore", "Gore"), ("이세계", "isekai", "Isekai"),
    ("복수", "revenge", "Revenge"), ("전쟁", "war", "War"), ("시간 여행", "time_manipulation", "Time Manipulation"),
    ("범죄", "crime", "Crime"), ("힐링", "iyashikei", "Iyashikei"), ("아포칼립스", "post_apocalyptic", "Post-Apocalyptic"),
    ("서바이벌", "survival", "Survival"), ("무술", "martial_arts", "Martial Arts"), ("던전", "dungeon", "Dungeon"),
    ("게임 세계", "virtual_world", "Virtual World"),
]
FIELDS = ["kind", "code", "anilist_name", "category", "works_pct", "display_name", "name_check", "show", "sort_order"]


def code_of(name, taken):
    """AniList 이름 → 저장 코드 (소문자 · 밑줄, 백엔드 규칙 ^[a-z][a-z0-9_-]*$). 숫자로 시작하면 t_ 를 붙임"""
    code = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    code = code if code[:1].isalpha() else f"t_{code}"
    base, n = code, 2
    while code in taken:
        code, n = f"{base}_{n}", n + 1
    return code


def load_series():
    """시리즈(me;nu 작품 번호)마다 장르 · 태그별 인기 비중 = 그 칩이 붙은 편들의 인기 합 / 시리즈 인기 합.
    태그는 관련도 60% 이상인 편만 붙은 걸로 침 (성인 · 스포일러 태그 제외). 태그 분류와 나온 태그 이름도 같이"""
    with open(CATALOG / "menu_work_entries.csv", encoding="utf-8-sig", newline="") as f:
        entries = list(csv.DictReader(f))
    work_of = {int(r["anilist_id"]): int(r["menu_work_id"]) for r in entries}
    popularity = {int(r["anilist_id"]): float(r["popularity"]) for r in entries}
    media = {}                                                   # 두 파일에 다 있는 편은 뒤 파일 것 하나만 (service.load_features 와 같음)
    for name in ("media.jsonl", "media_recent.jsonl"):
        for line in open(ANILIST / name, encoding="utf-8"):
            media.update((m["id"], m) for m in json.loads(line).get("media") or [] if m["id"] in work_of)
    total, genres, tags, category, seen_tags = Counter(), defaultdict(Counter), defaultdict(Counter), {}, set()
    for a, m in media.items():
        w, p = work_of[a], popularity[a]
        total[w] += p
        for g in set(m.get("genres") or []) - ADULT_GENRES:
            genres[w][g] += p
        for t in m.get("tags") or []:
            if t.get("isAdult"):
                continue
            category[t["name"]] = t.get("category") or ""
            seen_tags.add(t["name"])
            if t["rank"] >= TAG_MIN_RANK and not (t.get("isMediaSpoiler") or t.get("isGeneralSpoiler")):
                tags[w][t["name"]] += p

    def share(pops):
        return defaultdict(dict, {w: {k: p / total[w] for k, p in c.items()} for w, c in pops.items()})
    return share(genres), share(tags), category, seen_tags


def main():
    with open(CATALOG / "menu_works.csv", encoding="utf-8-sig", newline="") as f:
        works = [int(r["menu_work_id"]) for r in csv.DictReader(f)]
    genres, tags, category, seen_tags = load_series()
    links = {("genre", g): {w for w in works if genres[w].get(g, 0) >= SERIES_MIN_SHARE}
             for g in {g for w in works for g in genres[w]}}
    links.update({("theme", t): {w for w in works if tags[w].get(t, 0) >= SERIES_MIN_SHARE} for t in seen_tags})

    rows, taken = [], {"genre": set(), "theme": set()}
    for kind, main_rows in (("genre", MAIN_GENRES), ("theme", MAIN_THEMES)):
        for order, (label, code, name) in enumerate(main_rows, start=1):
            if (kind, name) not in links:
                raise SystemExit(f"AniList 데이터에 없는 칩: {kind} {name}")
            taken[kind].add(code)
            rows.append({"kind": kind, "code": code, "anilist_name": name, "display_name": label, "name_check": "",
                         "show": "main", "sort_order": order})
    main_names = {(r["kind"], r["anilist_name"]) for r in rows}
    rest = sorted((key for key in links if key not in main_names), key=lambda k: (k[0], -len(links[k]), k[1]))
    for order, (kind, name) in enumerate(rest, start=100):
        rows.append({"kind": kind, "code": code_of(name, taken[kind]), "anilist_name": name, "display_name": "",
                     "name_check": "입력 필요", "show": "off", "sort_order": order})
        taken[kind].add(rows[-1]["code"])
    for r in rows:
        r["category"] = "Genre" if r["kind"] == "genre" else category.get(r["anilist_name"], "")
        r["works_pct"] = round(100 * len(links[(r["kind"], r["anilist_name"])]) / len(works), 1)
    rows.sort(key=lambda r: (r["kind"] != "genre", r["sort_order"]))

    with open(CATALOG / "chips.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with open(CATALOG / "work_chips.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["menu_work_id", "kind", "code"])
        for w in works:
            for r in rows:
                if w in links[(r["kind"], r["anilist_name"])]:
                    writer.writerow([w, r["kind"], r["code"]])
    shown = Counter(r["kind"] for r in rows if r["show"] == "main")
    print(f"칩 {len(rows)}개 (화면 장르 {shown['genre']} · 소재 {shown['theme']}) → {CATALOG / 'chips.csv'}")
    print(f"작품 {len(works)}개 연결 → {CATALOG / 'work_chips.csv'}")


if __name__ == "__main__":
    main()
