# anime_ranker — 유저에게 추천할 애니 순서를 정한다 (v0.5 = next_auto, 나머지는 비교용)
#
# 콘텐츠 추천(content_v0.py)이 "어떤 작품 글을 올릴지" 정할 때 쓸 작품 취향 부분이다.
# 채점기(eval/next_anime_test.py)가 이 방법들을 불러서 점수를 매긴다.
#
# 방법은 모두 (user, ctx) → 작품 id 를 좋은 순서로 줄 세운 목록 을 돌려준다.
# - user: {"history": History, "t": 지금 시각(초)}. 정답은 보지 않는다
#   History 는 유저 기록을 칸별 배열로 담은 것 (t 시각(초) · anime 작품 id · status 상태 번호 · score 점수 · fav 최애)
#   온보딩에서 고른 장르·테마가 있으면 user["genres"], user["themes"] 에 넣는다
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

# 취향을 잴 때 작품을 무엇으로 표현할지. EXP-13 에서 AniList 태그 전체가 가장 좋아서 기본값으로 씀
# (취향 점수만 기준 연습 9.14%, MAL 장르 3.98%, 테마 태그만 5.48%)
# "mal": MAL 장르 44개 / "anilist_genre": AniList 장르 / "anilist_tag": AniList 장르 + 태그(관련도 %)
# "anilist_tag_idf": anilist_tag 에서 흔한 태그(예: 남자 주인공)는 덜 친다
# "anilist_theme": anilist_tag 에서 등장인물·제작 방식 태그를 뺀 것 (테마·배경·대상층만)
FEATURES = "anilist_tag"
NON_THEME_TAGS = {"Cast-Traits", "Cast-Main Cast", "Technical"}  # anilist_theme 에서 빼는 태그 분류
CHIP_WEIGHT = 1.0               # 온보딩에서 고른 장르·테마 하나 = 그런 작품 하나를 본 만큼
GENRE_ALIAS = {"Suspense": "Thriller"}  # MAL 장르 이름 → AniList 장르 이름

# 다음 편 보너스. 최근에 본 시리즈일수록 크고, NEXT_TAU 일마다 약 1/e 로 줄어든다
# 값은 EXP-04 에서 연습 3,000명으로 고름 (보너스 2~5, 기간 1~3년이면 비슷)
NEXT_WEIGHT = 5.0
NEXT_TAU = 365

# 다음 편 자리 제한. 추천 10개 중 보던 시리즈 다음 편은 최대 NEXT_SLOTS 개
# EXP-05: 새 시리즈 점수가 장르 취향보다 10% 넘게 안 떨어지는 선에서 전체 최대 → 2개
NEXT_SLOTS = 2

# 인기도 섞기 (EXP-10). 점수 = 장르 유사도(0~1) + POP_BETA × 인기도(0~1)
# "all": 전체 기간 인기 / "recent": 지금 시각 직전 RECENT_DAYS 일 동안 많이 본 작품 (요즘 뜨는 작품)
POP_BETA = 0.0
POP_KIND = "all"
RECENT_DAYS = 30
N_RECENT_CANDIDATES = 300       # 최근 인기 섞기에서 후보에 더 넣는 개수 (새로 뜨는 작품이 후보 밖일 수 있어서)

# 같이 본 작품 (EXP-11). "이거 본 사람이 저것도 봄"을 센다 (연관 규칙)
# "confidence": A 본 사람 중 B도 본 비율 / "lift": 그 비율이 B의 원래 인기보다 몇 배인지 / "cosine": 둘의 중간
# 본 작품마다 점수를 더한다. "confidence_max" 는 더하지 않고 가장 센 것 하나만 쓴다 (Apriori 방식, EXP-17·18)
COWATCH_ITEMS = 3000            # 같이 본 횟수를 셀 작품: 인기 상위 몇 개
COWATCH_KIND = "confidence"
LIFT_MIN_COUNT = 5              # lift 는 같이 본 사람이 이보다 적으면 0 (우연한 조합 걸러내기)

# 학습하는 행동 기반 모델 (EXP-15). "같이 본 작품" 자리에 바꿔 끼울 수 있다 (kind="ease" / "als")
# EASE 는 v0.5 기존 유저 설정에 쓴다 (EXP-16·18). ALS 는 비교용
# EASE (Steck 2019): 같이 본 횟수 표로 작품끼리 관계를 한 번에 계산하는 선형 모델. λ 가 클수록 단순해진다
# ALS (Hu·Koren·Volinsky 2008): 유저·작품을 숫자 벡터로 바꾸는 행렬 분해. 본 작품은 (1 + α) 만큼 중요하게 친다
EASE_LAMBDA = 500.0
ALS_FACTORS = 64
ALS_REG = 1.0
ALS_ALPHA = 10.0
ALS_ITERS = 10


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
        if features == "anilist_theme":
            v.update({"t:" + t: r for t, r in m["tags"].items() if m["cats"].get(t) not in NON_THEME_TAGS})
        elif features != "anilist_genre":
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
    ctx.update(cw_rows=uni, cw_pos=pos, cw_sim=cowatch_sims(C, len(histories)), cw_count=C, cw_users=len(histories))


def cowatch_sims(C, n_users):
    # 같이 본 사람 수 표 → 유사도 표들 (confidence · cosine · lift). 대각은 0
    d = np.diag(C).astype(np.float64)                    # 작품마다 본 사람 수
    Cd = C.astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        sims = {
            "confidence": np.nan_to_num(Cd / d[:, None]),
            "cosine": np.nan_to_num(Cd / np.sqrt(np.outer(d, d))),
            "lift": np.nan_to_num(np.where(Cd >= LIFT_MIN_COUNT, Cd * n_users / np.outer(d, d), 0)),
        }
    for S in sims.values():
        np.fill_diagonal(S, 0)
    return sims


def build_ease(ctx, lam=EASE_LAMBDA):
    # EASE: B = −P / P의 대각 (열마다), P = (같이 본 횟수 표 + λI)의 역행렬. 대각은 0. build_cowatch 다음에 부른다
    P = np.linalg.inv(ctx["cw_count"].astype(np.float64) + lam * np.eye(len(ctx["cw_count"])))
    B = -P / np.diag(P)[None, :]
    np.fill_diagonal(B, 0)
    ctx["cw_sim"]["ease"] = B


def build_als(ctx, histories, factors=ALS_FACTORS, reg=ALS_REG, alpha=ALS_ALPHA, iters=ALS_ITERS, seed=0):
    # ALS: 유저 벡터와 작품 벡터를 번갈아 고친다. 같이 본 횟수 표와 같은 작품(인기 상위 COWATCH_ITEMS 편)만 쓴다
    pos, m = ctx["cw_pos"], len(ctx["cw_rows"])
    users = []
    for h in histories:
        p = pos[rows_of(ctx, h.anime[np.isin(h.status, WATCHED)])]
        p = np.unique(p[p >= 0])
        if len(p):
            users.append(p)
    item_users = [[] for _ in range(m)]
    for u, p in enumerate(users):
        for i in p.tolist():
            item_users[i].append(u)
    item_users = [np.array(x, dtype=np.int64) for x in item_users]
    eye = np.eye(factors)

    def solve(F, lists):  # 상대편 벡터 F 로 이쪽 벡터를 한 번에 푼다
        FtF, out = F.T @ F, np.zeros((len(lists), factors))
        for j, idx in enumerate(lists):
            if len(idx):
                Fi = F[idx]
                out[j] = np.linalg.solve(FtF + alpha * (Fi.T @ Fi) + reg * eye, (1 + alpha) * Fi.sum(axis=0))
        return out

    Y = np.random.default_rng(seed).normal(0, 0.01, (m, factors))
    for _ in range(iters):
        X = solve(Y, users)
        Y = solve(X, item_users)
    ctx.update(als_Y=Y, als_YtY=Y.T @ Y, als_reg=reg, als_alpha=alpha)


def build_rec(ctx, edges):
    """AniList 커뮤니티 추천 ("이거 좋아하면 이것도", 유저 투표). edges: (작품 a, 추천 작품 b, 추천 수) 목록.
    a → b 와 b → a 를 둘 다 잇고, 세기는 log(1 + 추천 수). 작품 줄마다 (이어진 줄 배열, 세기 배열)로 둔다"""
    out = defaultdict(lambda: ([], []))
    row = ctx["row"]
    for a, b, n in edges:
        if a in row and b in row and a != b and n > 0:
            for x, y in ((a, b), (b, a)):
                out[row[x]][0].append(row[y])
                out[row[x]][1].append(math.log1p(n))
    ctx["rec"] = {r: (np.array(ys, dtype=np.int64), np.array(ws)) for r, (ys, ws) in out.items()}


def rec_scores(user, ctx):
    # 본 작품들이 커뮤니티 추천으로 이어진 작품마다 세기를 더한다. 0~1 (그 유저의 1등이 1)
    h = user["history"]
    links = [ctx["rec"][r] for r in rows_of(ctx, h.anime[np.isin(h.status, WATCHED)]).tolist() if r in ctx["rec"]]
    if not links:
        return np.zeros(len(ctx["ids"]))
    out = np.bincount(np.concatenate([y for y, _ in links]), weights=np.concatenate([w for _, w in links]),
                      minlength=len(ctx["ids"]))
    return out / out.max()


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
    col = ctx["feature_col"]
    for g in user.get("genres", ()):  # 온보딩에서 고른 장르 (MAL 표현이면 이름 그대로, AniList 표현이면 "g:" 이름)
        j = col.get(g, col.get("g:" + GENRE_ALIAS.get(g, g)))
        if j is not None:
            p[j] += CHIP_WEIGHT
    for th in user.get("themes", ()):  # 온보딩에서 고른 테마 (AniList 태그)
        j = col.get("t:" + th)
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
    """다음 편 자리 제한 + 인기도 섞기 (EXP-10)."""
    return next_picks(user, ctx, slots) + genre_pop(user, ctx, beta, kind)


def cf_raw(user, ctx, kind=COWATCH_KIND):
    # 행동 기반 점수 (같이 본 횟수 표의 작품마다). kind: confidence · cosine · lift · ease → 본 작품 줄을 더함
    # confidence_max → 본 작품 줄 중 가장 큰 값 / als → 유저 벡터
    # CF_INPUT 에 든 종류면 본 작품 중 같이 본 기록이 없는 작품을 "비슷한 작품들의 줄"(무게 평균)로 바꿔 넣는다 (EXP-24)
    h = user["history"]
    rows = rows_of(ctx, h.anime[np.isin(h.status, WATCHED)])
    p = ctx["cw_pos"][rows]
    p = p[p >= 0]
    extra = _borrowed_rows(ctx, rows, kind) if CF_INPUT and kind in CF_INPUT and "cf_borrow" in ctx else None
    if kind == "confidence_max":
        out = ctx["cw_sim"]["confidence"][p].max(axis=0) if len(p) else np.zeros(len(ctx["cw_rows"]))
        return out if extra is None else np.maximum(out, extra.max(axis=0))
    if kind != "als":
        out = ctx["cw_sim"][kind][p].sum(axis=0)
        return out if extra is None else out + extra.sum(axis=0)
    if not len(p):
        return np.zeros(len(ctx["cw_rows"]))
    Y, Yi = ctx["als_Y"], ctx["als_Y"][np.unique(p)]
    A = ctx["als_YtY"] + ctx["als_alpha"] * (Yi.T @ Yi) + ctx["als_reg"] * np.eye(Y.shape[1])
    return Y @ np.linalg.solve(A, (1 + ctx["als_alpha"]) * Yi.sum(axis=0))


def cowatch(user, ctx, kind=COWATCH_KIND):
    """같이 본 작품 (EXP-11). 내가 본 작품 하나하나와 '같이 본' 정도를 더해 줄 세운다. 같으면 인기순.
    kind 로 EASE·ALS 를 고르면 같은 자리에서 비교할 수 있다 (EXP-15)."""
    return rank_rows(ctx["cw_rows"], ctx, -cf_raw(user, ctx, kind))


def next_cowatch(user, ctx, kind=COWATCH_KIND, slots=NEXT_SLOTS):
    """다음 편 자리 제한 + 같이 본 작품 (EXP-11)."""
    return next_picks(user, ctx, slots) + cowatch(user, ctx, kind)


CF_FILL = "borrow"   # 같이 본 기록이 없는 작품(ctx["cf_missing"])의 행동 기반 점수: None(0점) · "median" · "borrow". EXP-23에서 borrow 채택
CF_INPUT = ("ease",)  # 유저가 본 작품 중 기록 없는 작품도 비슷한 작품으로 이어서 계산할 행동 기반 종류들. EXP-24에서 기록 많은 유저 설정(EASE)만 채택
# 둘 다 build_cf_borrow 를 부른 문맥에서만 작동한다 (MAL 채점기는 기록 없는 작품을 정하지 않아서 결과 그대로).
# 서비스에선 MAL 기록이 없는 작품(2022년 이후 작품) = 기록 없는 작품


def _borrowed_rows(ctx, rows, kind):
    # 본 작품 줄 번호 중 기록 없는 작품마다 "비슷한 작품 줄의 무게 평균" (build_cf_borrow 의 무게). 없으면 None
    idx = ctx.get("cf_borrow_idx")
    if idx is None:
        idx = ctx["cf_borrow_idx"] = {r: i for i, r in enumerate(ctx["cf_borrow"][0].tolist())}
    q = [idx[r] for r in rows.tolist() if r in idx]
    if not q:
        return None
    cache = ctx.setdefault("cf_pseudo", {})
    if kind not in cache:                       # 유사도 종류마다 한 번만 계산 (기록 없는 작품 × 같이 본 표 작품)
        _, donors, W = ctx["cf_borrow"]
        S = ctx["cw_sim"]["confidence" if kind == "confidence_max" else kind]
        cache[kind] = W @ S[ctx["cw_pos"][donors]]
    return cache[kind][q]


def build_cf_borrow(ctx, missing_ids, k=10, use=("tag", "rec")):
    """기록 없는 작품마다 점수를 빌려 올 "기록 있는 비슷한 작품" k개와 무게를 정해 둔다.
    tag = AniList 태그 코사인, rec = 커뮤니티 추천 세기 (build_rec 먼저). 둘 다 쓰면 무게를 더함"""
    row, cw = ctx["row"], ctx["cw_rows"]
    missing = np.zeros(len(ctx["ids"]), dtype=bool)
    missing[[row[a] for a in missing_ids if a in row]] = True
    donors = cw[~missing[cw]]                                  # 같이 본 표 안에서 기록이 있는 작품
    col = {r: j for j, r in enumerate(donors.tolist())}
    rows = np.flatnonzero(missing)
    Xn = ctx["X"] / ctx["vnorm"][:, None]
    W = np.zeros((len(rows), len(donors)))                     # 기록 없는 작품 × 빌려 올 작품 무게
    if "tag" in use:
        S = Xn[rows] @ Xn[donors].T
        top = np.argpartition(-S, min(k, len(donors) - 1), axis=1)[:, :k]
        for i in range(len(rows)):
            W[i, top[i]] += np.clip(S[i, top[i]], 0, None)
    if "rec" in use:
        for i, r in enumerate(rows.tolist()):
            ys, ws = ctx.get("rec", {}).get(r, ((), ()))
            for y, wv in zip(np.asarray(ys).tolist(), np.asarray(ws).tolist()):
                if y in col:
                    W[i, col[y]] += wv / max(ws)
    keep = W.sum(axis=1) > 0
    ctx["cf_missing"] = missing
    ctx["cf_borrow"] = (rows[keep], donors, W[keep] / W[keep].sum(axis=1, keepdims=True))
    ctx.pop("cf_borrow_idx", None)              # 다시 만들면 미리 계산한 것도 버림
    ctx.pop("cf_pseudo", None)


def cowatch_scores(user, ctx, kind=COWATCH_KIND):
    # 행동 기반 점수를 모든 작품에 대해 0~1 로 (그 유저의 1등 작품이 1). 같이 본 횟수 표 밖 작품은 0
    # CF_FILL 이 있으면 기록 없는 작품도 0점으로 두지 않는다 (기록 있는 옛날 작품만 점수를 받아 신작이 밀리는 문제, EXP-21·23)
    raw = cf_raw(user, ctx, kind)
    out = np.zeros(len(ctx["ids"]))
    if raw.max() > 0:
        out[ctx["cw_rows"]] = raw / raw.max()
    if CF_FILL and "cf_missing" in ctx:
        miss = ctx["cf_missing"]
        if CF_FILL == "median":
            have = ctx["cw_rows"][~miss[ctx["cw_rows"]]]
            out[miss] = np.median(out[have])
        elif CF_FILL == "borrow":
            rows, donors, W = ctx["cf_borrow"]
            out[rows] = W @ out[donors]
    return out


# 조합 (EXP-14). 새 작품 자리 점수 = 취향 유사도 + β × 인기도 + γ × 행동 기반 점수 (셋 다 0~1)
# 기록이 적은 유저(신규)와 많은 유저는 강한 재료가 달라서 따로 고름 (연습 5,000명 기준, 값은 EXP-18 에서 확정)
# - 기존 유저: 최근 30일 인기 β=0.35 + EASE γ=0.5 → 실전 33.01% (아이템 기반 CF 로는 27.53%)
# - 신규 유저: 전체 기간 인기 β=1 + 같이 본 작품(가장 센 규칙 하나) γ=2 → 실전 18.55% (작품 1개 + 칩)
#   + 최근 30일 인기 0.5 (trend, EXP-25) → 18.54% → 19.62%. 신작 기록이 없는 상황 15.22% → 17.84%
# - 본 작품 7개 이하면 신규 유저 설정. 연습에서 7개까지는 신규 설정, 10개부터 EASE 가 이김 (EXP-16·18)
# cf: 행동 기반 부분에 쓸 방법 (confidence · confidence_max = 아이템 기반 CF / ease / als)
WARM = {"beta": 0.35, "gamma": 0.5, "kind": "recent", "cf": "ease"}            # 기록 많은 유저
COLD = {"beta": 1.0, "gamma": 2.0, "kind": "all", "cf": "confidence_max", "trend": 0.5}  # 기록 적은 유저 (온보딩 직후)
COLD_MAX_WATCHED = 7                                     # 본 작품이 이 개수 이하면 신규 유저 설정


def hybrid(user, ctx, beta, gamma, kind="recent", cf_kind=COWATCH_KIND, delta=0.0, trend=0.0):
    """취향 + 인기도 + 같이 본 작품 섞기 (EXP-14). cf_kind 로 같이 본 작품 대신 EASE·ALS 를 쓸 수 있다.
    delta > 0 이면 AniList 커뮤니티 추천도 섞는다 (build_rec 먼저. 추천으로 이어진 작품은 후보에 넣음)
    trend > 0 이면 최근 30일 인기를 더 섞는다 (전체 기간 인기를 쓰는 신규 유저 설정용, EXP-25. 최근 인기 300편은 후보에 넣음)"""
    s, p, c = genre_scores(user, ctx), pop_scores(user, ctx, kind), cowatch_scores(user, ctx, cf_kind)
    total = s + beta * p + gamma * c
    rows = np.unique(np.r_[pop_candidates(user, ctx, kind), ctx["cw_rows"]])
    if trend:
        total = total + trend * pop_scores(user, ctx, "recent")
        rows = np.unique(np.r_[rows, pop_candidates(user, ctx, "recent")])
    if delta and "rec" in ctx:
        r = rec_scores(user, ctx)
        total = total + delta * r
        rows = np.unique(np.r_[rows, np.flatnonzero(r > 0)])
    return rank_rows(rows, ctx, -total[rows])


def next_hybrid(user, ctx, beta=None, gamma=None, kind=None, slots=NEXT_SLOTS, cf_kind=None, delta=None, trend=None):
    """다음 편 자리 제한 + 조합 (EXP-14). 값을 안 주면 기존 유저 설정(WARM)."""
    beta = WARM["beta"] if beta is None else beta
    gamma = WARM["gamma"] if gamma is None else gamma
    kind = WARM["kind"] if kind is None else kind
    cf_kind = WARM["cf"] if cf_kind is None else cf_kind
    delta = WARM.get("rec", 0.0) if delta is None else delta
    trend = WARM.get("trend", 0.0) if trend is None else trend
    return next_picks(user, ctx, slots) + hybrid(user, ctx, beta, gamma, kind, cf_kind, delta, trend)


def next_auto(user, ctx, slots=NEXT_SLOTS):
    """v0.5. 조합 자동 설정 (EXP-14, 값은 EXP-18). 본 작품 수로 신규 유저 / 기존 유저 설정을 고른다.
    기존 유저 설정은 EASE 를 쓰므로 build_cowatch → build_ease 를 먼저 불러 둔다."""
    n = int(np.isin(user["history"].status, WATCHED).sum())
    cfg = COLD if n <= COLD_MAX_WATCHED else WARM
    return next_hybrid(user, ctx, cfg["beta"], cfg["gamma"], cfg["kind"], slots, cfg.get("cf", COWATCH_KIND),
                       cfg.get("rec", 0.0), cfg.get("trend", 0.0))


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
