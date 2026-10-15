# mal_scorer — MAL 시청 기록으로 "다음에 볼 작품 맞히기" 점수를 매긴다 (M3 채점기)
#
# 유저마다 마지막에 본 작품 하나를 가리고, 그 전 기록만 보고 추천한 상위 10개 안에
# 가린 작품이 있는지 본다. 모델을 고칠 때마다 이 점수로 나아졌는지 확인한다.
#
# 메인 점수는 시리즈 단위다. 정답이 "원피스 극장판 Z"일 때 "원피스"를 추천해도 맞힌 걸로 친다.
# me;nu 는 작품 글을 보여주는 서비스라 같은 시리즈면 같은 관심사로 보기 때문. 작품 단위 점수도 같이 낸다.
#
# 작품 취향만 잰다. 콘텐츠 유형(소식·가이드 등) 취향은 MAL 에 없어서 못 잰다 → M3-2 시뮬레이터 몫.
#
#   python model/scripts/export_mal_sample.py   # 먼저 표본 뽑기 (한 번만)
#   python -m model.offline.mal_scorer          # 채점
#   python -m model.offline.mal_scorer 3000     # 유저 3000명만 (빠르게 확인)

import csv
import json
import math
import pathlib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "mal_sample"
ANILIST = DATA.parent / "anilist" / "media.jsonl"  # model/scripts/fetch_anilist.py 로 받음

K = 10                          # 상위 몇 개 안에 들면 맞힌 걸로 칠지
WATCHED = {"completed", "watching"}  # 가릴 작품은 "봤다"고 할 수 있는 것만
TARGET_AFTER = datetime(2020, 1, 1)  # 마지막 기록이 이보다 오래되면 날짜가 부정확해서 뺀다
MIN_HISTORY = 5                 # 가리고 남은 기록이 이보다 적으면 뺀다
N_CANDIDATES = 2000             # 장르 모델이 고르는 후보: 인기 상위 몇 개 안에서
ONE_PER_SERIES = True           # 추천 10개에 같은 시리즈는 1개만
ADULT_GENRES = {"Hentai", "Erotica"}  # 성인 작품은 추천하지 않는다 (AniList isAdult 도 같이 봄)

# 취향을 잴 때 작품을 무엇으로 표현할지
# "mal": MAL 장르 44개 / "anilist_genre": AniList 장르 / "anilist_tag": AniList 장르 + 태그(관련도 %)
# "anilist_tag_idf": anilist_tag 에서 흔한 태그(예: 남자 주인공)는 덜 친다
FEATURES = "mal"


# ## 1. 데이터 읽기
def load_anime():
    anime = {}
    with open(DATA / "anime.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            anime[int(r["anime_id"])] = {
                "title": r["title"],
                "genres": set(g for g in r["genres"].split("|") if g),
                "start_date": r["start_date"] or None,
            }
    return anime


def load_anilist():
    # MAL id → AniList 장르·태그. 받은 파일이 없으면 빈 dict
    out = {}
    if not ANILIST.exists():
        return out
    with open(ANILIST, encoding="utf-8") as f:
        for line in f:
            for m in json.loads(line)["media"]:
                if m["idMal"]:
                    out[m["idMal"]] = {
                        "genres": set(m["genres"]),
                        "tags": {t["name"]: t["rank"] / 100 for t in m["tags"] if not t["isAdult"]},
                        "adult": m["isAdult"],
                    }
    return out


def build_vectors(anime, anilist, features=FEATURES):
    # 작품 → {특징: 가중치}. 장르는 1, 태그는 관련도(0~1)
    if features == "mal":
        return {a: {g: 1.0 for g in v["genres"]} for a, v in anime.items()}
    vec = {}
    for a, m in anilist.items():
        v = {"g:" + g: 1.0 for g in m["genres"]}
        if features != "anilist_genre":
            v.update({"t:" + t: r for t, r in m["tags"].items()})
        vec[a] = v
    if features == "anilist_tag_idf":
        # 많은 작품에 붙은 특징일수록 가중치를 낮춘다: × log(전체 작품 수 / 그 특징이 붙은 작품 수)
        df = Counter(f for v in vec.values() for f in v)
        n = len(vec)
        vec = {a: {f: w * math.log(n / df[f]) for f, w in v.items() if df[f] < n} for a, v in vec.items()}
    return vec


# 같은 시리즈 묶기. 두 가지 중 하나라도 맞으면 같은 시리즈로 본다
# 1) 속편·전편 관계 (MAL 관계 데이터). 시즌끼리 묶인다: 노라가미 → 노라가미 아라가토
# 2) 제목 앞부분이 같음: "One Piece Movie 14: Stampede", "One Piece Film: Strong World" → "one piece"
# 외전·원작 같은 다른 관계는 안 쓴다. 콜라보 특별편이 원피스·나루토·드래곤볼을 한 덩어리로 엮어 버린다.
SEASON_RELATIONS = {"Sequel", "Prequel"}
_TITLE_TAIL = re.compile(
    r"\s+(\d+(st|nd|rd|th)\s+season|season\s*\d+|part\s*\d+|final\s+season|the\s+movie|"
    r"movie(\s*\d+)?|film|ova|ona|specials?|[ivx]+|\d+)$", re.I)


def series_key(title):
    t = re.sub(r"^gekijouban\s+", "", title.split(":")[0], flags=re.I)
    prev = None
    while prev != t:  # 꼬리를 하나씩 떼어 낸다: "Shingeki no Kyojin Season 3 Part 2" → "Shingeki no Kyojin"
        prev = t
        t = _TITLE_TAIL.sub("", t).strip(" -'°.!")
    return t.lower() if len(t) >= 2 else title.lower()


def load_series(anime):
    parent = {}

    def root(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        a, b = root(a), root(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    with open(DATA / "anime_anime.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["related"] == "1" and r["relation_type"] in SEASON_RELATIONS:
                union(int(r["anime_a"]), int(r["anime_b"]))
    first = {}
    for a in sorted(anime):
        k = series_key(anime[a]["title"])
        if k in first:
            union(a, first[k])
        else:
            first[k] = a
    return {a: root(a) for a in parent}  # 작품 id → 시리즈 대표 id


def load_histories():
    users = defaultdict(list)
    with open(DATA / "interactions.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            users[r["user_id"]].append((datetime.fromisoformat(r["t"]), int(r["anime_id"]), r["status"]))
    return users


# ## 2. 마지막 작품 가리기
# 마지막으로 본 작품이 딱 하나일 때만 쓴다 (같은 시각에 여러 개면 무엇이 마지막인지 모름).
# 가린 작품보다 나중 기록은 "미래"라서 버린다.
def split(rows):
    watched = [r for r in rows if r[2] in WATCHED]
    if not watched:
        return None
    t_last = max(r[0] for r in watched)
    last = [r for r in watched if r[0] == t_last]
    if len(last) != 1 or t_last < TARGET_AFTER:
        return None
    history = [r for r in rows if r[0] < t_last]
    if len(history) < MIN_HISTORY:
        return None
    return {"history": history, "target": last[0][1], "t": t_last}


# ## 3. 비교할 추천 방법
# 방법은 (유저 기록, 공용 정보) → 작품을 좋은 순서로 줄 세우기만 한다.
# 본 작품·방영 전 작품 빼기, 시리즈당 1개는 pick_top 이 공통으로 처리한다.

def popular(case, ctx):
    """인기순. 누구에게나 같은 인기작을 준다. 이것보다 못하면 의미가 없는 바닥 점수."""
    return ctx["by_popularity"]


def genre_scorer(case, ctx):
    # 본 작품들의 장르·태그를 더해 취향을 만들고, 작품별 유사도(0~1)를 돌려주는 함수를 만든다
    vec, vnorm = ctx["vec"], ctx["vnorm"]
    profile = Counter()
    for _, a, status in case["history"]:
        if status in WATCHED and a in vec:
            profile.update(vec[a])
    norm = math.sqrt(sum(v * v for v in profile.values())) or 1.0

    def score(a):
        v = vec.get(a)
        if not v:
            return 0.0
        return sum(profile[f] * w for f, w in v.items()) / (norm * vnorm[a])  # 코사인 유사도

    return score


def genre(case, ctx):
    """장르 취향 (v0 방식). 본 작품들의 장르 비율과 비슷한 작품을 준다. 같은 점수면 인기순."""
    score = genre_scorer(case, ctx)
    # 인기 순위를 두 번째 기준으로 써서 같은 점수일 때 인기작이 먼저 오게 한다
    return sorted(ctx["candidates"], key=lambda a: (-score(a), ctx["pop_rank"][a]))


# 다음 편 보너스. 최근에 본 시리즈일수록 크고, NEXT_TAU 일마다 약 1/e 로 줄어든다
# 값은 EXP-04 에서 연습 3,000명으로 고름 (보너스 2~5, 기간 1~3년이면 비슷)
NEXT_WEIGHT = 5.0
NEXT_TAU = 365


def series_bonus(case, ctx, weight=NEXT_WEIGHT, tau=NEXT_TAU):
    # 보던 시리즈 → 보너스. 그 시리즈의 마지막 기록이 하차(dropped)면 보너스 없음
    ser = lambda a: ctx["series"].get(a, a)
    last = {}  # 시리즈 → (마지막으로 본 시각, 그때 상태)
    for t, a, status in case["history"]:
        if status in WATCHED or status == "dropped":
            s = ser(a)
            if s not in last or t >= last[s][0]:
                last[s] = (t, status)
    return {s: weight * math.exp(-(case["t"] - t).days / tau)
            for s, (t, status) in last.items() if status in WATCHED}


def next_in_series(case, ctx, weight=NEXT_WEIGHT, tau=NEXT_TAU):
    """다음 편 우선 (EXP-04, 보류). 장르 취향 + 보던 시리즈의 안 본 작품에 보너스."""
    ser = lambda a: ctx["series"].get(a, a)
    bonus = series_bonus(case, ctx, weight, tau)
    score = genre_scorer(case, ctx)
    cands = set(ctx["candidates"])
    for s in bonus:  # 보던 시리즈 작품은 인기 상위 밖이어도 후보에 넣는다
        cands.update(ctx["series_items"].get(s, ()))
    n = len(ctx["pop_rank"])
    return sorted(cands, key=lambda a: (-(score(a) + bonus.get(ser(a), 0.0)), ctx["pop_rank"].get(a, n)))


# 다음 편 자리 제한. 추천 10개 중 보던 시리즈 다음 편은 최대 NEXT_SLOTS 개
# EXP-05: 새 시리즈 점수가 장르 취향보다 10% 넘게 안 떨어지는 선에서 전체 최대 → 2개
NEXT_SLOTS = 2


def next_limited(case, ctx, slots=NEXT_SLOTS):
    """다음 편 자리 제한 (EXP-05). 보던 시리즈 다음 편 최대 slots 개를 앞에 두고, 나머지는 장르 취향."""
    ser = lambda a: ctx["series"].get(a, a)
    bonus = series_bonus(case, ctx)
    score = genre_scorer(case, ctx)
    n = len(ctx["pop_rank"])
    cont = [a for s in bonus for a in ctx["series_items"].get(s, ())]
    cont = sorted(cont, key=lambda a: (-(score(a) + bonus[ser(a)]), ctx["pop_rank"].get(a, n)))
    return pick_top(cont, case, ctx)[:slots] + genre(case, ctx)


MODELS = {"인기순": popular, "장르 취향(v0)": genre,
          "다음 편 우선": next_in_series, "다음 편 자리 제한": next_limited}


def aired(a, case, ctx):
    # 가린 시점에 아직 방영 전인 작품은 추천할 수 없었으니 뺀다
    d = ctx["anime"].get(a, {}).get("start_date")
    return d is None or d <= case["t"].date().isoformat()


def pick_top(ranked, case, ctx, one_per_series=ONE_PER_SERIES):
    # 줄 세운 작품에서 위부터 K개를 고른다. one_per_series 면 같은 시리즈는 처음 1개만
    seen = {r[1] for r in case["history"]}
    out, used = [], set()
    for a in ranked:
        if a in seen or a in ctx["adult"] or not aired(a, case, ctx):
            continue
        if one_per_series:
            s = ctx["series"].get(a, a)
            if s in used:
                continue
            used.add(s)
        out.append(a)
        if len(out) == K:
            break
    return out


# ## 4. 채점
# Hit@10: 정답이 상위 10개 안에 있으면 1
# NDCG@10: 위에 있을수록 점수가 크다 (1위 1.0, 2위 0.63, 10위 0.29, 밖이면 0)
# 시리즈 단위는 정답과 같은 시리즈 작품이 처음 나온 순위로 매긴다
# 정답 종류별로도 나눈다: 보던 시리즈(이미 본 시리즈의 다른 작품) / 새 시리즈
def evaluate(cases, ctx, model, one_per_series=ONE_PER_SERIES):
    ser = lambda a: ctx["series"].get(a, a)
    s_hit = s_ndcg = w_hit = 0.0
    by_kind = {"보던": [0, 0], "새": [0, 0]}  # [맞힌 수, 전체 수]
    for case in cases:
        recs = pick_top(model(case, ctx), case, ctx, one_per_series)
        rec_series = [ser(a) for a in recs]
        hit = ser(case["target"]) in rec_series
        if hit:
            s_hit += 1
            s_ndcg += 1 / math.log2(rec_series.index(ser(case["target"])) + 2)
        w_hit += case["target"] in recs
        kind = "보던" if ser(case["target"]) in {ser(r[1]) for r in case["history"]} else "새"
        by_kind[kind][0] += hit
        by_kind[kind][1] += 1
    n = len(cases)
    rate = lambda k: by_kind[k][0] / by_kind[k][1] if by_kind[k][1] else 0.0
    return {"시리즈 Hit@10": s_hit / n, "시리즈 NDCG@10": s_ndcg / n, "작품 Hit@10": w_hit / n,
            "보던 시리즈 Hit@10": rate("보던"), "새 시리즈 Hit@10": rate("새")}


def build_context(cases, anime, series, anilist=None, features=FEATURES):
    # 인기도는 가리고 남은 기록만으로 센다 (가린 작품을 미리 보면 반칙)
    pop = Counter()
    for case in cases:
        pop.update({r[1] for r in case["history"]})
    by_popularity = [a for a, _ in sorted(pop.items(), key=lambda kv: (-kv[1], kv[0])) if a in anime]
    series_items = defaultdict(list)  # 시리즈 → 그 시리즈 작품들
    for a, s in series.items():
        if a in anime:
            series_items[s].append(a)
    anilist = anilist or {}
    vec = build_vectors(anime, anilist, features)
    adult = {a for a, v in anime.items() if v["genres"] & ADULT_GENRES}
    adult |= {a for a, m in anilist.items() if m["adult"]}
    return {
        "anime": anime,
        "vec": vec,
        "vnorm": {a: math.sqrt(sum(w * w for w in v.values())) or 1.0 for a, v in vec.items()},
        "adult": adult,
        "series": series,
        "series_items": series_items,
        "by_popularity": by_popularity,
        "pop_rank": {a: i for i, a in enumerate(by_popularity)},
        "candidates": by_popularity[:N_CANDIDATES],
    }


def main():
    max_users = int(sys.argv[1]) if len(sys.argv) > 1 else None
    anime = load_anime()
    histories = load_histories()
    cases = [c for c in (split(rows) for _, rows in sorted(histories.items())) if c]
    if max_users:
        cases = cases[:max_users]
    anilist = load_anilist()
    ctx = build_context(cases, anime, load_series(anime), anilist)
    cands = set(ctx["candidates"])
    in_cands = sum(c["target"] in cands for c in cases) / len(cases)
    print(f"유저 {len(histories):,}명 중 채점 가능 {len(cases):,}명 · 작품 {len(anime):,}편")
    print(f"정답이 후보(인기 상위 {N_CANDIDATES}편) 안에 있는 비율 {in_cands:.1%}")
    print(f"작품 표현: {FEATURES} · AniList 정보 있는 후보 {len(cands & set(anilist)) / len(cands):.1%}\n")
    cols = ["시리즈 Hit@10", "보던 시리즈 Hit@10", "새 시리즈 Hit@10", "시리즈 NDCG@10", "작품 Hit@10"]
    print(f"{'방법':<14}" + "".join(f"{c:>16}" for c in cols))
    for name, model in MODELS.items():
        m = evaluate(cases, ctx, model)
        print(f"{name:<14}" + "".join(f"{m[c]:>16.3f}" for c in cols))


if __name__ == "__main__":
    main()
