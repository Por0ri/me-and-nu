# anime_ranker — 유저에게 추천할 애니 순서를 정한다 (v0.5 후보들)
#
# 콘텐츠 추천(content_v0.py)이 "어떤 작품 글을 올릴지" 정할 때 쓸 작품 취향 부분이다.
# 채점기(eval/next_anime_test.py)가 이 방법들을 불러서 점수를 매긴다.
#
# 방법은 모두 (user, ctx) → 작품 id 를 좋은 순서로 줄 세운 목록 을 돌려준다.
# - user: {"history": History, "t": 지금 시각(초)}. 정답은 보지 않는다
#   History 는 유저 기록을 칸별 배열로 담은 것 (t 시각(초) · anime 작품 id · status 상태 번호 · score 점수 · fav 최애)
#   온보딩에서 고른 장르가 있으면 user["genres"] 에 넣는다
# - ctx: 모든 유저가 같이 쓰는 정보. build_context 로 만든다
# 줄 세운 목록에서 본 작품·성인 작품·방영 전 작품 빼기, 시리즈당 1개는 pick_top 이 처리한다.
# 계산은 numpy 로 한 번에 한다 (작품마다 따로 계산하면 느림).

import math
from collections import Counter, defaultdict, namedtuple
from datetime import datetime, timedelta

import numpy as np

STATUS = {"completed": 0, "watching": 1, "dropped": 2, "on_hold": 3}  # 상태 → 번호
WATCHED = (STATUS["completed"], STATUS["watching"])  # "봤다"고 칠 상태
DROPPED = STATUS["dropped"]
History = namedtuple("History", "t anime status score fav")
DAY = 86400                     # 하루 (초)
EPOCH = datetime(1970, 1, 1)    # 시각(초)의 기준

K = 10                          # 추천 개수
ONE_PER_SERIES = True           # 추천 10개에 같은 시리즈는 1개만
N_CANDIDATES = 2000             # 장르 취향이 고르는 후보: 인기 상위 몇 개 안에서
ADULT_GENRES = {"Hentai", "Erotica"}  # 성인 작품은 추천하지 않는다 (AniList isAdult 도 같이 봄)

# 취향을 잴 때 작품을 무엇으로 표현할지 (EXP-06: MAL 장르가 가장 좋았음)
# "mal": MAL 장르 44개 / "anilist_genre": AniList 장르 / "anilist_tag": AniList 장르 + 태그(관련도 %)
# "anilist_tag_idf": anilist_tag 에서 흔한 태그(예: 남자 주인공)는 덜 친다
FEATURES = "mal"
CHIP_WEIGHT = 1.0               # 온보딩에서 고른 장르 하나 = 그 장르 작품 하나를 본 만큼

# 다음 편 보너스. 최근에 본 시리즈일수록 크고, NEXT_TAU 일마다 약 1/e 로 줄어든다
# 값은 EXP-04 에서 연습 3,000명으로 고름 (보너스 2~5, 기간 1~3년이면 비슷)
NEXT_WEIGHT = 5.0
NEXT_TAU = 365

# 다음 편 자리 제한. 추천 10개 중 보던 시리즈 다음 편은 최대 NEXT_SLOTS 개
# EXP-05: 새 시리즈 점수가 장르 취향보다 10% 넘게 안 떨어지는 선에서 전체 최대 → 2개
NEXT_SLOTS = 2

# 인기도 섞기 (EXP-09). 점수 = 장르 유사도(0~1) + POP_BETA × 인기도(0~1)
# "all": 전체 기간 인기 / "recent": 지금 시각 직전 RECENT_DAYS 일 동안 많이 본 작품 (요즘 뜨는 작품)
POP_BETA = 0.0
POP_KIND = "all"
RECENT_DAYS = 30
N_RECENT_CANDIDATES = 300       # 최근 인기 섞기에서 후보에 더 넣는 개수 (새로 뜨는 작품이 후보 밖일 수 있어서)

# 같이 본 작품 (EXP-10). "이거 본 사람이 저것도 봄"을 센다 (연관 규칙)
# "confidence": A 본 사람 중 B도 본 비율 / "lift": 그 비율이 B의 원래 인기보다 몇 배인지 / "cosine": 둘의 중간
COWATCH_ITEMS = 3000            # 같이 본 횟수를 셀 작품: 인기 상위 몇 개
COWATCH_KIND = "confidence"
LIFT_MIN_COUNT = 5              # lift 는 같이 본 사람이 이보다 적으면 0 (우연한 조합 걸러내기)


def to_seconds(dt):
    return int((dt - EPOCH).total_seconds())


def to_yyyymmdd(seconds):
    d = (EPOCH + timedelta(seconds=int(seconds))).date()
    return d.year * 10000 + d.month * 100 + d.day


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


def build_context(anime, series, popularity, anilist=None, features=FEATURES, events=None):
    """anime: 작품 id → {title, genres, start_date}, series: 작품 → 시리즈 대표,
    popularity: 작품 → 본 유저 수, anilist: MAL id → {genres, tags, adult},
    events: (작품 id 배열, 시각 배열) 모든 기록. 주면 '최근 인기'를 계산할 수 있다"""
    ids = np.array(sorted(anime), dtype=np.int64)          # 작품 줄 번호 → 작품 id
    row = {a: i for i, a in enumerate(ids.tolist())}        # 작품 id → 줄 번호
    n = len(ids)
    by_popularity = [a for a, _ in sorted(popularity.items(), key=lambda kv: (-kv[1], kv[0])) if a in anime]
    pop_rank = np.full(n, len(by_popularity), dtype=np.int64)  # 인기 순위 (인기 기록이 없으면 맨 뒤)
    pop_count = np.zeros(n)
    for i, a in enumerate(by_popularity):
        pop_rank[row[a]] = i
        pop_count[row[a]] = popularity[a]

    series_items = defaultdict(list)  # 시리즈 → 그 시리즈 작품들
    for a, s in series.items():
        if a in anime:
            series_items[s].append(a)

    # 작품 × 특징 표. 장르 취향은 이 표로 한 번에 계산한다
    anilist = anilist or {}
    vec = build_vectors(anime, anilist, features)
    feats = sorted({f for v in vec.values() for f in v})
    col = {f: j for j, f in enumerate(feats)}
    X = np.zeros((n, len(feats)))
    for a, v in vec.items():
        if a in row:
            for f, w in v.items():
                X[row[a], col[f]] = w
    vnorm = np.sqrt((X * X).sum(axis=1))
    vnorm[vnorm == 0] = 1.0

    adult = {a for a, v in anime.items() if v["genres"] & ADULT_GENRES}
    adult |= {a for a, m in anilist.items() if m["adult"]}
    start = {a: int(v["start_date"].replace("-", "")) for a, v in anime.items() if v["start_date"]}

    ctx = {
        "anime": anime, "ids": ids, "row": row, "series": series, "series_items": series_items,
        "X": X, "vnorm": vnorm, "feature_col": col, "adult": adult, "start": start,
        "by_popularity": by_popularity, "pop_rank": pop_rank,
        "pop_norm": np.log1p(pop_count) / math.log1p(max(pop_count.max(), 1)),
        "cand_rows": np.array([row[a] for a in by_popularity[:N_CANDIDATES]], dtype=np.int64),
    }
    if events is not None:
        build_recent(ctx, *events)
    return ctx


def build_recent(ctx, anime_ids, seconds):
    # 날짜별로 작품마다 몇 번 기록됐는지 누적해 둔다 → 아무 날짜나 "직전 30일 동안 많이 본 작품"을 바로 꺼낼 수 있다
    days = np.asarray(seconds, dtype=np.int64) // DAY
    a = np.asarray(anime_ids, dtype=np.int64)
    d0 = int(days.max()) - 3 * 365                       # 최근 3년만
    ids = ctx["ids"]
    r = np.searchsorted(ids, a)
    ok = (days >= d0) & (r < len(ids))
    ok[ok] = ids[r[ok]] == a[ok]
    nd = int(days.max()) - d0 + 1
    flat = (days[ok] - d0) * len(ids) + r[ok]
    counts = np.bincount(flat, minlength=nd * len(ids)).reshape(nd, len(ids))
    cum = np.zeros((nd + 1, len(ids)), dtype=np.int32)    # cum[k] = k번째 날 전까지의 누적
    cum[1:] = np.cumsum(counts, axis=0)
    ctx["recent_cum"], ctx["recent_d0"] = cum, d0


def build_cowatch(ctx, histories, n_items=COWATCH_ITEMS):
    """histories: 여러 유저의 History. 인기 상위 n_items 편끼리 '같이 본' 사람 수를 센다"""
    uni = np.array([ctx["row"][a] for a in ctx["by_popularity"][:n_items]], dtype=np.int64)
    pos = np.full(len(ctx["ids"]), -1, dtype=np.int64)
    pos[uni] = np.arange(len(uni))
    C = np.zeros((len(uni), len(uni)), dtype=np.float32)
    for i in range(0, len(histories), 4000):
        chunk = histories[i:i + 4000]
        B = np.zeros((len(chunk), len(uni)), dtype=np.float32)  # 유저 × 작품, 봤으면 1
        for j, h in enumerate(chunk):
            p = pos[rows_of(ctx, h.anime[np.isin(h.status, WATCHED)])]
            B[j, p[p >= 0]] = 1
        C += B.T @ B
    d = np.diag(C).astype(np.float64)                    # 작품마다 본 사람 수
    Cd = C.astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        sims = {
            "confidence": np.nan_to_num(Cd / d[:, None]),
            "cosine": np.nan_to_num(Cd / np.sqrt(np.outer(d, d))),
            "lift": np.nan_to_num(np.where(Cd >= LIFT_MIN_COUNT, Cd * len(histories) / np.outer(d, d), 0)),
        }
    for S in sims.values():
        np.fill_diagonal(S, 0)
    ctx.update(cw_rows=uni, cw_pos=pos, cw_sim=sims)


def rows_of(ctx, anime_ids):
    # 작품 id 배열 → 줄 번호 배열 (작품 표에 없는 작품은 뺀다)
    ids = ctx["ids"]
    a = np.asarray(anime_ids, dtype=np.int64)
    r = np.searchsorted(ids, a)
    ok = r < len(ids)
    ok[ok] = ids[r[ok]] == a[ok]
    return r[ok]


def recent_counts(user, ctx):
    # 지금 시각 직전 RECENT_DAYS 일 동안 작품마다 몇 번 기록됐는지
    cum, d0 = ctx["recent_cum"], ctx["recent_d0"]
    k = min(max(user["t"] // DAY - d0, 0), len(cum) - 1)
    return (cum[k] - cum[max(k - RECENT_DAYS, 0)]).astype(np.float64)


# ## 2. 추천 방법
def popular(user, ctx):
    """인기순. 누구에게나 같은 인기작을 준다. 이것보다 못하면 의미가 없는 바닥 점수."""
    return ctx["by_popularity"]


def genre_scores(user, ctx):
    # 본 작품들의 장르·태그를 더해 취향을 만들고, 모든 작품의 유사도(0~1)를 한 번에 계산한다
    h = user["history"]
    p = ctx["X"][rows_of(ctx, h.anime[np.isin(h.status, WATCHED)])].sum(axis=0)
    for g in user.get("genres", ()):  # 온보딩에서 고른 장르
        j = ctx["feature_col"].get(g, ctx["feature_col"].get("g:" + g))
        if j is not None:
            p[j] += CHIP_WEIGHT
    norm = math.sqrt(p @ p) or 1.0
    return (ctx["X"] @ p) / (norm * ctx["vnorm"])  # 코사인 유사도


def rank_rows(rows, ctx, *keys):
    # 줄 번호들을 keys 순서대로 줄 세운다 (앞 key 가 우선, 작은 값이 앞). 마지막엔 인기 순위·작품 id
    order = np.lexsort((ctx["ids"][rows], ctx["pop_rank"][rows]) + tuple(reversed(keys)))
    return ctx["ids"][rows[order]].tolist()


def genre(user, ctx):
    """장르 취향 (v0 방식). 본 작품들의 장르 비율과 비슷한 작품을 준다. 같은 점수면 인기순."""
    s = genre_scores(user, ctx)
    rows = ctx["cand_rows"]
    return rank_rows(rows, ctx, -s[rows])


def series_bonus(user, ctx, weight=NEXT_WEIGHT, tau=NEXT_TAU):
    # 보던 시리즈 → 보너스. 그 시리즈의 마지막 기록이 하차(dropped)면 보너스 없음
    h = user["history"]
    m = np.flatnonzero(np.isin(h.status, WATCHED + (DROPPED,)))
    if not len(m):
        return {}
    s = np.array([ctx["series"].get(a, a) for a in h.anime[m].tolist()], dtype=np.int64)
    order = np.lexsort((m, h.t[m], s))                   # 시리즈별로 시각 순 (같으면 기록 순)
    last = order[np.r_[s[order][1:] != s[order][:-1], True]]  # 시리즈마다 마지막 기록
    first = {}
    for i, sv in enumerate(s.tolist()):
        first.setdefault(sv, i)
    bonus = {}
    for i in sorted(last.tolist(), key=lambda i: first[int(s[i])]):  # 기록에 처음 나온 시리즈부터
        if h.status[m[i]] in WATCHED:
            days = (user["t"] - int(h.t[m[i]])) // DAY
            bonus[int(s[i])] = weight * math.exp(-days / tau)
    return bonus


def next_in_series(user, ctx, weight=NEXT_WEIGHT, tau=NEXT_TAU):
    """다음 편 우선 (EXP-04, 보류). 장르 취향 + 보던 시리즈의 안 본 작품에 보너스."""
    bonus = series_bonus(user, ctx, weight, tau)
    s = genre_scores(user, ctx)
    extra = [ctx["row"][a] for b in bonus for a in ctx["series_items"].get(b, ())]
    rows = np.unique(np.r_[ctx["cand_rows"], np.array(extra, dtype=np.int64)])  # 보던 시리즈 작품은 인기 밖이어도 후보
    b = np.array([bonus.get(ctx["series"].get(a, a), 0.0) for a in ctx["ids"][rows].tolist()])
    return rank_rows(rows, ctx, -(s[rows] + b))


def next_picks(user, ctx, slots=NEXT_SLOTS):
    # 보던 시리즈 다음 편에서 최대 slots 개 (장르 + 다음 편 보너스 순)
    bonus = series_bonus(user, ctx)
    cont = [a for b in bonus for a in ctx["series_items"].get(b, ())]
    if not cont:
        return []
    s = genre_scores(user, ctx)
    rows = np.array([ctx["row"][a] for a in cont], dtype=np.int64)
    b = np.array([bonus[ctx["series"].get(a, a)] for a in cont])
    order = np.lexsort((ctx["pop_rank"][rows], -(s[rows] + b)))  # 같은 점수면 인기순 (그래도 같으면 목록 순서)
    return pick_top(ctx["ids"][rows[order]].tolist(), user, ctx)[:slots]


def next_limited(user, ctx, slots=NEXT_SLOTS):
    """다음 편 자리 제한 (EXP-05). 보던 시리즈 다음 편 최대 slots 개를 앞에 두고, 나머지는 장르 취향."""
    return next_picks(user, ctx, slots) + genre(user, ctx)


def next_plus_new(user, ctx, slots=NEXT_SLOTS):
    """다음 편 + 새 시리즈 (EXP-07, 미채택). 다음 편 최대 slots 개 + 나머지는 한 번도 안 본 시리즈에서만 장르 취향."""
    touched = {ctx["series"].get(a, a) for a in user["history"].anime.tolist()}  # 본 적 있는 시리즈 (하차·보류 포함)
    return next_picks(user, ctx, slots) + [a for a in genre(user, ctx) if ctx["series"].get(a, a) not in touched]


def pop_scores(user, ctx, kind=POP_KIND):
    # 인기도 0~1. 본 사람 수를 로그로 줄여서 1등 작품이 1이 되게 한다
    if kind == "all":
        return ctx["pop_norm"]
    c = recent_counts(user, ctx)
    return np.log1p(c) / math.log1p(c.max()) if c.max() > 0 else c


def pop_candidates(user, ctx, kind=POP_KIND):
    if kind == "all":
        return ctx["cand_rows"]
    c = recent_counts(user, ctx)
    top = np.argpartition(-c, N_RECENT_CANDIDATES)[:N_RECENT_CANDIDATES]
    return np.unique(np.r_[ctx["cand_rows"], top[c[top] > 0]])


def genre_pop(user, ctx, beta=POP_BETA, kind=POP_KIND):
    """장르 취향 + 인기도 (EXP-09). 점수 = 장르 유사도 + beta × 인기도. beta 가 None 이면 인기도만 (같으면 장르 순)."""
    s, p = genre_scores(user, ctx), pop_scores(user, ctx, kind)
    rows = pop_candidates(user, ctx, kind)
    if beta is None:
        return rank_rows(rows, ctx, -p[rows], -s[rows])
    return rank_rows(rows, ctx, -(s[rows] + beta * p[rows]))


def next_genre_pop(user, ctx, beta=POP_BETA, kind=POP_KIND, slots=NEXT_SLOTS):
    """다음 편 자리 제한 + 인기도 섞기 (EXP-09)."""
    return next_picks(user, ctx, slots) + genre_pop(user, ctx, beta, kind)


def cowatch(user, ctx, kind=COWATCH_KIND):
    """같이 본 작품 (EXP-10). 내가 본 작품 하나하나와 '같이 본' 정도를 더해 줄 세운다. 같으면 인기순."""
    h = user["history"]
    p = ctx["cw_pos"][rows_of(ctx, h.anime[np.isin(h.status, WATCHED)])]
    score = ctx["cw_sim"][kind][p[p >= 0]].sum(axis=0)
    return rank_rows(ctx["cw_rows"], ctx, -score)


def next_cowatch(user, ctx, kind=COWATCH_KIND, slots=NEXT_SLOTS):
    """다음 편 자리 제한 + 같이 본 작품 (EXP-10)."""
    return next_picks(user, ctx, slots) + cowatch(user, ctx, kind)


# ## 3. 최종 K개 고르기
def pick_top(ranked, user, ctx, one_per_series=ONE_PER_SERIES):
    # 줄 세운 작품에서 위부터 K개를 고른다. 본 작품·성인 작품·방영 전 작품은 빼고, 같은 시리즈는 처음 1개만
    seen = set(user["history"].anime.tolist())
    today = to_yyyymmdd(user["t"])
    start, adult, series = ctx["start"], ctx["adult"], ctx["series"]
    out, used = [], set()
    for a in ranked:
        if a in seen or a in adult or start.get(a, 0) > today:
            continue
        if one_per_series:
            s = series.get(a, a)
            if s in used:
                continue
            used.add(s)
        out.append(a)
        if len(out) == K:
            break
    return out
