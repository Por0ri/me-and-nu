# anime_ranker — 유저에게 추천할 작품 순서를 정한다 (v0.5 후보들)
#
# 콘텐츠 추천(v0.py)이 "어떤 작품 글을 올릴지" 정할 때 쓸 작품 취향 부분이다.
# 채점기(eval/next_anime_test.py)가 이 방법들을 불러서 점수를 매긴다.
#
# 방법은 모두 (user, ctx) → 작품 id 를 좋은 순서로 줄 세운 목록 을 돌려준다.
# - user: {"history": [(시각, 작품 id, 상태), ...], "t": 지금 시각}. 정답은 보지 않는다
# - ctx: 모든 유저가 같이 쓰는 정보. build_context 로 만든다
# 줄 세운 목록에서 본 작품·성인 작품·방영 전 작품 빼기, 시리즈당 1개는 pick_top 이 처리한다.

import math
from collections import Counter, defaultdict

K = 10                          # 추천 개수
WATCHED = {"completed", "watching"}  # "봤다"고 칠 상태
ONE_PER_SERIES = True           # 추천 10개에 같은 시리즈는 1개만
N_CANDIDATES = 2000             # 장르 취향이 고르는 후보: 인기 상위 몇 개 안에서
ADULT_GENRES = {"Hentai", "Erotica"}  # 성인 작품은 추천하지 않는다 (AniList isAdult 도 같이 봄)

# 취향을 잴 때 작품을 무엇으로 표현할지 (EXP-06: MAL 장르가 가장 좋았음)
# "mal": MAL 장르 44개 / "anilist_genre": AniList 장르 / "anilist_tag": AniList 장르 + 태그(관련도 %)
# "anilist_tag_idf": anilist_tag 에서 흔한 태그(예: 남자 주인공)는 덜 친다
FEATURES = "mal"

# 다음 편 보너스. 최근에 본 시리즈일수록 크고, NEXT_TAU 일마다 약 1/e 로 줄어든다
# 값은 EXP-04 에서 연습 3,000명으로 고름 (보너스 2~5, 기간 1~3년이면 비슷)
NEXT_WEIGHT = 5.0
NEXT_TAU = 365

# 다음 편 자리 제한. 추천 10개 중 보던 시리즈 다음 편은 최대 NEXT_SLOTS 개
# EXP-05: 새 시리즈 점수가 장르 취향보다 10% 넘게 안 떨어지는 선에서 전체 최대 → 2개
NEXT_SLOTS = 2


# ## 1. 공용 정보 만들기
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


def build_context(anime, series, popularity, anilist=None, features=FEATURES):
    """anime: 작품 id → {title, genres, start_date}, series: 작품 → 시리즈 대표,
    popularity: 작품 → 본 유저 수, anilist: MAL id → {genres, tags, adult}"""
    by_popularity = [a for a, _ in sorted(popularity.items(), key=lambda kv: (-kv[1], kv[0])) if a in anime]
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


# ## 2. 추천 방법
def popular(user, ctx):
    """인기순. 누구에게나 같은 인기작을 준다. 이것보다 못하면 의미가 없는 바닥 점수."""
    return ctx["by_popularity"]


def genre_scorer(user, ctx):
    # 본 작품들의 장르·태그를 더해 취향을 만들고, 작품별 유사도(0~1)를 돌려주는 함수를 만든다
    vec, vnorm = ctx["vec"], ctx["vnorm"]
    profile = Counter()
    for _, a, status in user["history"]:
        if status in WATCHED and a in vec:
            profile.update(vec[a])
    norm = math.sqrt(sum(v * v for v in profile.values())) or 1.0

    def score(a):
        v = vec.get(a)
        if not v:
            return 0.0
        return sum(profile[f] * w for f, w in v.items()) / (norm * vnorm[a])  # 코사인 유사도

    return score


def genre(user, ctx):
    """장르 취향 (v0 방식). 본 작품들의 장르 비율과 비슷한 작품을 준다. 같은 점수면 인기순."""
    score = genre_scorer(user, ctx)
    # 인기 순위를 두 번째 기준으로 써서 같은 점수일 때 인기작이 먼저 오게 한다
    return sorted(ctx["candidates"], key=lambda a: (-score(a), ctx["pop_rank"][a]))


def series_bonus(user, ctx, weight=NEXT_WEIGHT, tau=NEXT_TAU):
    # 보던 시리즈 → 보너스. 그 시리즈의 마지막 기록이 하차(dropped)면 보너스 없음
    ser = lambda a: ctx["series"].get(a, a)
    last = {}  # 시리즈 → (마지막으로 본 시각, 그때 상태)
    for t, a, status in user["history"]:
        if status in WATCHED or status == "dropped":
            s = ser(a)
            if s not in last or t >= last[s][0]:
                last[s] = (t, status)
    return {s: weight * math.exp(-(user["t"] - t).days / tau)
            for s, (t, status) in last.items() if status in WATCHED}


def next_in_series(user, ctx, weight=NEXT_WEIGHT, tau=NEXT_TAU):
    """다음 편 우선 (EXP-04, 보류). 장르 취향 + 보던 시리즈의 안 본 작품에 보너스."""
    ser = lambda a: ctx["series"].get(a, a)
    bonus = series_bonus(user, ctx, weight, tau)
    score = genre_scorer(user, ctx)
    cands = set(ctx["candidates"])
    for s in bonus:  # 보던 시리즈 작품은 인기 상위 밖이어도 후보에 넣는다
        cands.update(ctx["series_items"].get(s, ()))
    n = len(ctx["pop_rank"])
    return sorted(cands, key=lambda a: (-(score(a) + bonus.get(ser(a), 0.0)), ctx["pop_rank"].get(a, n)))


def next_picks(user, ctx, slots=NEXT_SLOTS):
    # 보던 시리즈 다음 편에서 최대 slots 개 (장르 + 다음 편 보너스 순)
    ser = lambda a: ctx["series"].get(a, a)
    bonus = series_bonus(user, ctx)
    score = genre_scorer(user, ctx)
    n = len(ctx["pop_rank"])
    cont = [a for s in bonus for a in ctx["series_items"].get(s, ())]
    cont = sorted(cont, key=lambda a: (-(score(a) + bonus[ser(a)]), ctx["pop_rank"].get(a, n)))
    return pick_top(cont, user, ctx)[:slots]


def next_limited(user, ctx, slots=NEXT_SLOTS):
    """다음 편 자리 제한 (EXP-05). 보던 시리즈 다음 편 최대 slots 개를 앞에 두고, 나머지는 장르 취향."""
    return next_picks(user, ctx, slots) + genre(user, ctx)


def next_plus_new(user, ctx, slots=NEXT_SLOTS):
    """다음 편 + 새 시리즈 (EXP-07). 다음 편 최대 slots 개 + 나머지는 한 번도 안 본 시리즈에서만 장르 취향."""
    ser = lambda a: ctx["series"].get(a, a)
    touched = {ser(a) for _, a, _ in user["history"]}  # 본 적 있는 시리즈 (하차·보류 포함)
    return next_picks(user, ctx, slots) + [a for a in genre(user, ctx) if ser(a) not in touched]


# ## 3. 최종 K개 고르기
def aired(a, user, ctx):
    # 지금 시각에 아직 방영 전인 작품은 추천하지 않는다
    d = ctx["anime"].get(a, {}).get("start_date")
    return d is None or d <= user["t"].date().isoformat()


def pick_top(ranked, user, ctx, one_per_series=ONE_PER_SERIES):
    # 줄 세운 작품에서 위부터 K개를 고른다. one_per_series 면 같은 시리즈는 처음 1개만
    seen = {r[1] for r in user["history"]}
    out, used = [], set()
    for a in ranked:
        if a in seen or a in ctx["adult"] or not aired(a, user, ctx):
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
