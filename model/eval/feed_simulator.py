# feed_simulator — 가짜 유저 시뮬레이터 (M3-2). 글 추천 v1 의 설계를 실제 유저 없이 비교한다
#
# 가짜 유저 = MAL 실제 유저 + 유형 취향 페르소나 + 행동 성향
#   작품 취향의 정답은 그 유저가 MAL 에서 실제로 본 작품과 매긴 점수다 (가짜가 아님). 추천기는 온보딩에서 고른 몇 개만 안다
#   유형 취향·행동 성향은 MAL 에 없어서 가정으로 붙인다 → 가정을 바꿔도 결론이 같은지 따로 확인한다
# 가짜 글 = (작품, 유형, 게시일, 노출 종료일, 품질). 매일 새로 생긴다
# 하루에 여러 번 접속 → 피드를 위에서부터 보다가 아무 때나 그만둔다 → 노출·열기·읽기·반응 기록이 생긴다
# 시뮬레이터는 유저의 진짜 관심을 알기 때문에, 추천 순서를 행동이 아니라 진짜 관심으로 채점한다
#
# 통계(인기도·같이 본 작품·EASE)는 시뮬레이션 유저를 뺀 나머지 MAL 유저로만 센다 (정답 누출 방지)
#
#   python -m model.eval.feed_simulator          # 연습 유저 1,000명으로 기본 비교
#   python -m model.eval.feed_simulator 200      # 시뮬레이션 유저 200명 (연습 100명, 빠르게 확인)
#   python -m model.eval.feed_simulator pool     # 글이 몇 편 있어야 추천이 최신순보다 나은가 (실전 유저, 첫 7일)
#   python -m model.eval.feed_simulator logs     # 어떤 기록을 남기면 추천이 좋아지나 (연습 → 실전, 첫 7일)
#   python -m model.eval.feed_simulator sens     # 가정을 하나씩 바꿔도 글 개수·남길 기록 결론이 유지되나 (실전, 첫 7일)
#   python -m model.eval.feed_simulator tune     # v1 비중 고르기 (연습으로 고르고 실전으로 확인)
#   python -m model.eval.feed_simulator eval     # 유저 테스트 평가 설계 (4-1): 섞어 보여 주기 vs 반으로 나누기, 몇 명 × 며칠이면 차이를 잡나
#   python -m model.eval.feed_simulator refresh  # 반응을 접속마다 반영 vs 하루 한 번 반영 (서비스 계산 주기, EXP-30)

import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace

import numpy as np

from model.algo import anime_ranker as ar
from model.algo import content_v1 as v1
from model.eval import next_anime_test as t

# ## 1. 가정 (모두 바꿔 가며 결론이 유지되는지 확인할 값)
SEED = 0
N_SIM_USERS = 2000          # 연습 1,000 + 실전 1,000
MIN_WATCHED = 30            # 시뮬레이션 유저: 본 작품(다 봄·보는 중)이 이 이상인 MAL 유저
N_DAYS = 14                 # 시뮬레이션 기간 (유저 테스트 1주의 두 배)
BACKLOG_DAYS = 30           # 시작할 때 이미 쌓여 있는 글 (며칠 치)
NEW_PER_DAY = 80            # 하루에 새로 생기는 글
FEED_LEN = 50               # 한 번 접속에 준비하는 피드 길이
RBP_P = 0.9                 # 채점: 다음 글로 넘어갈 확률 (평균 10개쯤 봄)

# 유형별 글 비율. 1차로 만드는 유형을 조금 더 많게
TYPE_MIX = {"news": .20, "event": .08, "guide": .12, "people": .08, "curation": .08, "goods": .06,
            "behind": .06, "review": .08, "character": .12, "music": .06, "humor": .06}
# 노출 기간 (최소, 최대 일). 에이전트 규칙(행사 종료 다음 날 · 방영일 +30일 · 그 외 90일)을 흉내
LIFETIME = {"news": (30, 30), "event": (7, 30), "goods": (14, 45)}
DEFAULT_LIFETIME = (90, 90)
QUALITY_SD = 0.05           # 글마다 잘 쓴 정도 (모두에게 같은 보너스)

# 작품 관심 (MAL 기록 → 0~1)
LIKE_MAX, LIKE, SEEN, ON_HOLD_I, BAD = 1.0, 0.75, 0.5, 0.3, 0.02   # 최애·9점↑ / 7~8점 / 그 외 본 작품 / 보류 / 하차·4점↓
SERIES_I = 0.6              # 안 봤지만 좋아한 작품(7점↑)과 같은 시리즈
BASE_I = 0.03               # 그 밖의 작품
TIMELY, TIMELY_TAU = (v1.TYPE_INDEX["news"], v1.TYPE_INDEX["event"]), 20.0   # 소식·행사는 오래될수록 관심이 줄어듦
REPEAT_DECAY = 0.6          # 이미 본 글을 다시 보면 관심 × 0.6^본 횟수 (연 글은 0)

# 유형 취향 페르소나: (비율, 좋아하는 유형, 싫어하는 유형)
PERSONAS = {
    "소식러": (0.15, ("news", "event"), ("humor",)),
    "수집러": (0.12, ("goods", "event"), ("review",)),
    "캐릭터·성우 덕후": (0.18, ("character", "people", "music"), ()),
    "분석러": (0.15, ("review", "behind", "guide"), ("humor",)),
    "가볍게 보는 사람": (0.15, ("humor", "curation"), ("review",)),
    "골고루": (0.25, (), ()),
}
TYPE_NOISE = 0.3            # 같은 페르소나 안에서 사람마다 다른 정도
TYPE_STRENGTH = 0.6         # 유형 취향 +1 = 관심 × e^0.6 (약 1.8배), −1 = 약 0.55배

# 행동 성향
ACTIVITY = ((0.4, 1.5), (0.4, 3.5), (0.2, 7.0))   # (비율, 하루 평균 접속 수)
SCROLL = (0.85, 0.95)       # 다음 글로 넘어갈 확률 범위 → 한 번에 평균 7~20개
STYLE = {                   # (비율, (좋아요, 저장, 더볼래요, 공유, 그만볼래요) 최대 확률)
    "적극형": (0.25, (0.5, 0.25, 0.15, 0.06, 0.15)),
    "조용형": (0.75, (0.05, 0.03, 0.01, 0.005, 0.02)),
}
CURIOSITY = (0.02, 0.12)    # 관심 없어도 눌러 보는 확률 범위
OPEN_MAX = 0.8              # 관심 1 인 글을 여는 확률 (호기심 클릭 제외)
READ_K = 6.0                # 읽은 비율 흩어짐 (클수록 관심대로 읽음)
ONBOARD_WORKS = ((1, .2), (2, .2), (3, .25), (4, .15), (5, .2))   # 온보딩에서 고르는 작품 수


@dataclass
class SimUser:
    uid: int                    # MAL 유저 번호
    interest: np.ndarray        # 카탈로그 작품별 진짜 관심 (0~1)
    dislike: np.ndarray         # 카탈로그 작품별 싫어함 (하차·4점 이하)
    type_mult: np.ndarray       # 유형별 관심 배수
    type_dislike: np.ndarray    # 싫어하는 유형
    persona: str
    style_name: str
    style: tuple                # (좋아요, 저장, 더볼래요, 공유, 그만볼래요) 최대 확률
    p_next: float               # 다음 글로 넘어갈 확률
    curiosity: float            # 관심 없어도 눌러 보는 확률
    sessions: np.ndarray        # 날짜별 접속 수 (모든 추천 방식에 같게)
    onboard: dict               # 온보딩 입력: history · genres · themes · types · t
    base_aff: dict = None       # 온보딩만으로 만든 v0.5 작품 취향 (rank_scale → 배열. 추천 방식끼리 같이 씀)


# ## 2. 세계 만들기
def build_world(n_users=N_SIM_USERS, seed=SEED):
    anime, data, anilist = t.load_anime(), t.load_interactions(), t.load_anilist()
    series = t.load_series(anime)
    u = data["user"]
    starts = np.r_[0, np.flatnonzero(np.diff(u)) + 1]
    ends = np.r_[starts[1:], len(u)]
    watched = np.add.reduceat(np.isin(data["status"], ar.WATCHED).astype(np.int64), starts)
    pick = np.random.default_rng(seed).choice(np.flatnonzero(watched >= MIN_WATCHED), size=n_users, replace=False)
    is_sim = np.zeros(len(starts), dtype=bool)
    is_sim[pick] = True

    # 통계(인기도·같이 본 작품·EASE·최근 인기)는 시뮬레이션 유저를 뺀 나머지로만
    keep = np.repeat(~is_sim, ends - starts)
    stats = [ar.History(*(data[c][a:b] for c in ("t", "anime", "status", "score", "fav")))
             for a, b in zip(starts[~is_sim], ends[~is_sim])]
    pop = Counter()
    for h in stats:
        pop.update(set(h.anime.tolist()))
    ctx = ar.build_context(anime, series, pop, anilist, ar.FEATURES, (data["anime"][keep], data["t"][keep]))
    ar.build_cowatch(ctx, stats)
    ar.build_ease(ctx)

    cat_ids = ctx["ids"][ctx["cw_rows"]]
    weight = np.array([0.0 if a in ctx["adult"] else np.sqrt(pop[a]) for a in cat_ids.tolist()])
    users = []
    for k in pick:
        a, b = starts[k], ends[k]
        h = ar.History(*(data[c][a:b] for c in ("t", "anime", "status", "score", "fav")))
        users.append(make_user(int(u[a]), h, ctx, anime, anilist, np.random.default_rng([seed, int(k)])))
    return {"ctx": ctx, "users": users, "catalog_ids": cat_ids, "catalog_weight": weight / weight.sum(),
            "n_stats_users": len(stats)}


def make_user(uid, h, ctx, anime, anilist, rng):
    n = len(ctx["cw_rows"])
    ids = ctx["ids"]
    r = np.searchsorted(ids, h.anime)
    ok = r < len(ids)
    ok[ok] = ids[r[ok]] == h.anime[ok]
    pos = ctx["cw_pos"][r[ok]]
    st, sc, fv = h.status[ok], h.score[ok], h.fav[ok]
    lvl = np.where((fv > 0) | (sc >= 9), LIKE_MAX,
                   np.where(sc >= 7, LIKE, np.where(st == ar.STATUS["on_hold"], ON_HOLD_I, SEEN)))
    bad = (st == ar.DROPPED) | ((sc >= 1) & (sc <= 4))
    lvl[bad] = BAD

    interest = np.full(n, BASE_I, dtype=np.float32)
    for a in h.anime[ok][(lvl >= LIKE) & ~bad].tolist():             # 좋아한 작품의 같은 시리즈
        s = ctx["series"].get(a, a)
        for x in ctx["series_items"].get(s, ()):
            p = ctx["cw_pos"][ctx["row"][x]] if x in ctx["row"] else -1
            if p >= 0:
                interest[p] = max(interest[p], SERIES_I)
    m = pos >= 0
    interest[pos[m]] = lvl[m]
    dislike = np.zeros(n, dtype=bool)
    dislike[pos[m & bad]] = True

    names = list(PERSONAS)
    persona = names[rng.choice(len(names), p=[PERSONAS[x][0] for x in names])]
    _, liked, disliked = PERSONAS[persona]
    pref = rng.normal(0.0, TYPE_NOISE, len(v1.TYPES))
    pref[[v1.TYPE_INDEX[x] for x in liked]] += 1.0
    pref[[v1.TYPE_INDEX[x] for x in disliked]] -= 1.0
    order = np.argsort(-pref)
    chosen = [i for i in order if pref[i] > 0.5][:3] or list(order[:2])  # 온보딩 유형 칩: 좋아하는 유형 (없으면 위 2개)
    if rng.random() < 0.3:                                                 # 가끔 별 관심 없는 유형도 하나 고름
        chosen.append(rng.choice([i for i in range(len(v1.TYPES)) if i not in chosen]))

    styles = list(STYLE)
    style_name = styles[rng.choice(len(styles), p=[STYLE[s][0] for s in styles])]
    activity = [a for _, a in ACTIVITY][rng.choice(len(ACTIVITY), p=[p for p, _ in ACTIVITY])]
    n_works = [k for k, _ in ONBOARD_WORKS][rng.choice(len(ONBOARD_WORKS), p=[p for _, p in ONBOARD_WORKS])]
    now = int(h.t.max())
    ob = t.onboarding({"history": h, "t": now}, anime, int(n_works), 3, anilist, 3)
    return SimUser(uid, interest, dislike, np.exp(TYPE_STRENGTH * pref), pref <= -0.5, persona, style_name,
                   STYLE[style_name][1], rng.uniform(*SCROLL), rng.uniform(*CURIOSITY), rng.poisson(activity, N_DAYS),
                   {"history": ob["history"], "genres": ob["genres"], "themes": ob["themes"],
                    "types": [v1.TYPES[i] for i in chosen], "t": now})


def make_feed(world, seed=SEED, backlog_days=BACKLOG_DAYS, per_day=NEW_PER_DAY):
    rng = np.random.default_rng([seed, 99])
    day = np.repeat(np.arange(-backlog_days, N_DAYS), per_day)
    n = len(day)
    mix = np.array([TYPE_MIX[x] for x in v1.TYPES])
    typ = rng.choice(len(v1.TYPES), size=n, p=mix / mix.sum())
    lo = np.array([LIFETIME.get(x, DEFAULT_LIFETIME)[0] for x in v1.TYPES])
    hi = np.array([LIFETIME.get(x, DEFAULT_LIFETIME)[1] for x in v1.TYPES])
    return {"day": day, "pos": rng.choice(len(world["catalog_weight"]), size=n, p=world["catalog_weight"]),
            "type": typ, "until": day + rng.integers(lo[typ], hi[typ] + 1),
            "q": rng.normal(0.0, QUALITY_SD, n).astype(np.float32)}


# ## 3. 반응 흉내
def utility(user, feed, idx, day, seen, opened):
    # 진짜 관심 (0~1) = 작품 관심 × 유형 배수 + 글 품질. 소식·행사는 오래될수록 줄고, 이미 본 글은 줄고, 연 글은 0
    g = user.interest[feed["pos"][idx]] * user.type_mult[feed["type"][idx]] + feed["q"][idx]
    timely = np.isin(feed["type"][idx], TIMELY)
    g = np.where(timely, g * np.exp(-(day - feed["day"][idx]) / TIMELY_TAU), g)
    g = np.clip(g, 0.0, 1.0) * REPEAT_DECAY ** seen[idx]
    g[opened[idx]] = 0.0
    return g


def browse(user, feed, order, gain, rng, seen, opened):
    # 피드를 위에서부터 본다. 글 하나를 보고 다음으로 넘어갈 확률은 p_next (재미있는 글을 보면 조금 더 봄)
    like, save, more, share, less = user.style
    events = []
    for i, c in enumerate(order.tolist()):
        g = float(gain[i])
        events.append(("impression", c, i))
        d = max(float(user.dislike[feed["pos"][c]]), 0.5 if user.type_dislike[feed["type"][c]] else 0.0)
        p_open = 0.0 if opened[c] else user.curiosity + (1 - user.curiosity) * OPEN_MAX * g ** 1.5
        seen[c] += 1
        if rng.random() < p_open:
            opened[c] = True
            events.append(("open", c, None))
            mean = 0.1 + 0.85 * g
            events.append(("read", c, float(rng.beta(mean * READ_K, (1 - mean) * READ_K))))
            for kind, p in (("like", like * g * g), ("save", save * g * g), ("more", more * g * g),
                            ("share", share * g ** 3), ("less", less * d)):
                if rng.random() < p:
                    events.append((kind, c, None))
        elif rng.random() < less * d * 0.3:                          # 열지 않고 피드에서 바로 그만볼래요
            events.append(("less", c, None))
        if rng.random() >= min(user.p_next + (0.03 if g >= 0.5 else 0.0), 0.98):
            break
    return events


# ## 4. 채점
def rbp(g, p=RBP_P):
    # 위에서부터 보다가 언제든 그만두는 유저가 만나는 관심의 기대값 (Moffat & Zobel 2008)
    return float(np.dot(g, (1 - p) * p ** np.arange(len(g))))


def latest_first(feed, day):
    # 비교 기준: 최신순 (개인화 없음, 아무 기록도 안 씀)
    live = np.flatnonzero((feed["day"] <= day) & (feed["until"] >= day))
    return live[np.lexsort((-live, -feed["day"][live]))][:FEED_LEN]


def simulate(world, feed, users, cfg, seed=SEED, days=N_DAYS, update="visit"):
    """cfg=None 이면 최신순. 날짜별 평균 (정답 대비 RBP, 접속당 본 글 · 좋은 글 열람 · 그만볼래요)
    update: v1 순서를 언제 다시 계산하나 (EXP-30). visit = 접속마다 (반응 · 본 글을 바로 반영) /
    daily = 하루 한 번 (그날은 같은 순서) / daily_filter = 하루 한 번 + 접속 때 이미 연 글 · 그만볼래요 한 글만 뺌 /
    daily_seen = daily_filter + 오늘 이미 보여 준 글은 뒤로"""
    ctx, cat_ids, n_feed = world["ctx"], world["catalog_ids"], len(feed["day"])
    tot = {k: np.zeros(N_DAYS) for k in ("nrbp", "sessions", "views", "good_opens", "less", "bad_views", "repeats")}
    tot["events"] = Counter()                                        # 기록 종류별 개수 (읽기는 끝까지 읽은 것만)
    for ui, user in enumerate(users):
        rng = np.random.default_rng([seed, ui, 7])
        seen = np.zeros(n_feed, dtype=np.int16)                      # 유저가 실제로 본 횟수 (진실)
        opened = np.zeros(n_feed, dtype=bool)
        state = None
        if cfg is not None:
            ob = user.onboard
            state = v1.UserState(ob["history"], ob["genres"], ob["themes"], ob["types"], ob["t"],
                                 len(cat_ids), n_feed)
            key = cfg.rank_scale                                     # 온보딩만으로 만든 작품 취향은 방식끼리 같이 씀
            if user.base_aff is None or key not in user.base_aff:
                v1.refresh_work_affinity(state, ctx, cfg)
                user.base_aff = {**(user.base_aff or {}), key: state.work_aff}
            state.work_aff, state.dirty = user.base_aff[key].copy(), False
        for day in range(days):                                       # 앞쪽 날짜 결과는 뒤 날짜와 상관없음
            if state is not None and state.dirty:
                v1.refresh_work_affinity(state, ctx, cfg)
            live = np.flatnonzero((feed["day"] <= day) & (feed["until"] >= day))
            daily = v1.rank_feed(state, feed, day, cfg, 2 * FEED_LEN) if state is not None and update != "visit" else None
            seen0 = state.seen.copy() if daily is not None else None                # 오늘 접속 전까지 보여 준 횟수
            for _ in range(int(user.sessions[day])):
                if cfg is None:
                    order = latest_first(feed, day)
                else:
                    if daily is None:
                        order = v1.rank_feed(state, feed, day, cfg, FEED_LEN)
                    elif update == "daily":
                        order = daily[:FEED_LEN]
                    else:                                            # 서버가 아는 연 글 · 그만볼래요 글만 빼기
                        order = daily[~(state.opened[daily] | state.blocked[daily])]
                        if update == "daily_seen":                   # + 오늘 이미 보여 준 글은 뒤로 (노출 기록으로 서버가 앎)
                            shown = state.seen[order] > seen0[order]
                            order = np.r_[order[~shown], order[shown]]
                        order = order[:FEED_LEN]
                    v1.mark_served(state, order, cfg)
                gain = utility(user, feed, order, day, seen, opened)
                best = utility(user, feed, live, day, seen, opened)     # 정답: 진짜 관심 순으로 놓았을 때
                k = min(FEED_LEN, len(best))
                ideal = rbp(np.sort(np.partition(best, len(best) - k)[len(best) - k:])[::-1]) if k else 0.0
                gmap = dict(zip(order.tolist(), gain.tolist()))
                events = browse(user, feed, order, gain, rng, seen, opened)
                tot["nrbp"][day] += rbp(gain) / ideal if ideal > 0 else 0.0
                tot["sessions"][day] += 1
                tot["views"][day] += sum(e[0] == "impression" for e in events)
                tot["good_opens"][day] += sum(e[0] == "open" and gmap[e[1]] >= 0.5 for e in events)
                tot["less"][day] += sum(e[0] == "less" for e in events)
                tot["bad_views"][day] += sum(e[0] == "impression" and (user.dislike[feed["pos"][e[1]]]
                                                                      or user.type_dislike[feed["type"][e[1]]])
                                             for e in events)            # 싫어하는 작품·유형 글을 본 횟수
                tot["repeats"][day] += sum(e[0] == "impression" and seen[e[1]] >= 2 for e in events)   # 전에 본 글을 또 봄
                tot["events"].update(e[0] for e in events if e[0] != "read" or e[2] >= v1.READ_FULL)
                if state is not None:
                    v1.learn(state, events, feed, cfg, cat_ids)
    return tot


def summary(tot, days):
    n = tot["sessions"][days].sum()
    return {"정답 대비 RBP": tot["nrbp"][days].sum() / n, "접속당 본 글": tot["views"][days].sum() / n,
            "접속당 좋은 글 열람": tot["good_opens"][days].sum() / n, "100접속당 그만볼래요": 100 * tot["less"][days].sum() / n,
            "접속당 싫은 글 노출": tot["bad_views"][days].sum() / n, "접속당 다시 본 글": tot["repeats"][days].sum() / n}


POOL_SIZES = (10, 20, 30, 50, 80)   # 하루 새 글 수. 시작할 때 10일 치가 쌓여 있음 → 시작 글 100 · 200 · 300 · 500 · 800편


def pool_test():
    # 글이 몇 편 있어야 추천이 최신순보다 나은가. 유저 테스트 1주(첫 7일), 실전 유저 1,000명
    t0 = time.time()
    world = build_world(N_SIM_USERS)
    test = world["users"][N_SIM_USERS // 2:]
    week = np.arange(7)
    print(f"실전 유저 {len(test):,}명 · 첫 7일 · 준비 {time.time() - t0:.0f}초")
    print("시작 글\t하루 새 글\t방법\tRBP\t접속당 좋은 글 열람\t접속당 다시 본 글\t접속당 본 글")
    for per_day in POOL_SIZES:
        feed = make_feed(world, backlog_days=10, per_day=per_day)
        for name, cfg in (("최신순", None), ("v1 + 반응", v1.Config(logs=frozenset({"more", "less", "like", "save"})))):
            s = summary(simulate(world, feed, test, cfg), week)
            print(f"{10 * per_day}\t{per_day}\t{name}\t{s['정답 대비 RBP']:.3f}\t{s['접속당 좋은 글 열람']:.2f}\t"
                  f"{s['접속당 다시 본 글']:.1f}\t{s['접속당 본 글']:.1f}\t({time.time() - t0:.0f}초)", flush=True)


LOG_SETS = [   # 어떤 기록을 남길지 (유저 테스트 앱에서 모을 로그 후보)
    ("최신순", None),
    ("기록 없음 (온보딩만)", frozenset()),
    ("O/X만", frozenset({"more", "less"})),
    ("좋아요·저장만", frozenset({"like", "save"})),
    ("반응 4종 (O/X·좋아요·저장)", frozenset({"more", "less", "like", "save"})),
    ("반응 4종 + 노출", frozenset({"more", "less", "like", "save", "impression"})),
    ("반응 4종 + 노출 + 열람·읽기", frozenset({"more", "less", "like", "save", "impression", "open", "read"})),
    ("모든 기록", frozenset(v1.LOGS)),
]


def log_test():
    # 어떤 기록을 남기면 추천이 얼마나 좋아지나. 글은 유저 테스트 목표치(시작 300편 + 하루 30편), 첫 7일
    # 연습 유저로 고르고 실전 유저로 숫자를 낸다
    t0 = time.time()
    world = build_world(N_SIM_USERS)
    feed = make_feed(world, backlog_days=10, per_day=30)
    week = np.arange(7)
    for label, users in (("연습", world["users"][: N_SIM_USERS // 2]), ("실전", world["users"][N_SIM_USERS // 2:])):
        print(f"\n{label} 유저 {len(users):,}명 · 글 시작 300편 + 하루 30편 · 첫 7일")
        print("기록\tRBP\t접속당 좋은 글 열람\t접속당 다시 본 글\t접속당 싫은 글 노출\t100접속당 그만볼래요")
        for name, logs in LOG_SETS:
            cfg = None if logs is None else v1.Config(logs=logs)
            s = summary(simulate(world, feed, users, cfg), week)
            print(f"{name}\t{s['정답 대비 RBP']:.3f}\t{s['접속당 좋은 글 열람']:.2f}\t{s['접속당 다시 본 글']:.1f}\t"
                  f"{s['접속당 싫은 글 노출']:.2f}\t{s['100접속당 그만볼래요']:.2f}\t({time.time() - t0:.0f}초)", flush=True)


SENS_LOGS = [(name, logs) for name, logs in LOG_SETS
             if name in ("최신순", "기록 없음 (온보딩만)", "반응 4종 (O/X·좋아요·저장)", "반응 4종 + 노출", "반응 4종 + 노출 + 열람·읽기")]
BEST_LOGS = frozenset({"more", "less", "like", "save", "impression", "open", "read"})


def _prime_base_aff(world, users):
    # 온보딩만으로 만든 v0.5 작품 취향을 미리 계산해 둔다 (가정만 바꾼 복사본 유저들이 같이 씀)
    cfg = v1.Config()
    for u in users:
        if u.base_aff is None or cfg.rank_scale not in u.base_aff:
            ob = u.onboard
            st = v1.UserState(ob["history"], ob["genres"], ob["themes"], ob["types"], ob["t"], len(world["catalog_ids"]), 1)
            v1.refresh_work_affinity(st, world["ctx"], cfg)
            u.base_aff = {cfg.rank_scale: st.work_aff}


def _sens_users(users, kind):
    # 가정 하나만 바꾼 유저 복사본 (유저마다 정해진 난수라 매번 같게 나옴)
    out = []
    for u in users:
        rng = np.random.default_rng([SEED, u.uid, 11])
        if kind == "active50" and u.style_name == "조용형" and rng.random() < 1 / 3:   # 적극형 25% → 50%
            u = replace(u, style_name="적극형", style=STYLE["적극형"][1])
        elif kind == "type_weak":                                                    # 유형 취향 세기 절반
            u = replace(u, type_mult=np.exp(0.5 * np.log(u.type_mult)))
        elif kind == "half_sessions":                                                # 접속 횟수 절반
            u = replace(u, sessions=rng.binomial(u.sessions, 0.5))
        out.append(u)
    return out


def _top_share(weight, k=50):
    return float(np.sort(weight)[::-1][:k].sum())


def sensitivity_test():
    # 시뮬레이터 가정을 하나씩 바꿔도 EXP-19(글 개수)·EXP-20(남길 기록) 결론이 유지되는지. 실전 유저 1,000명, 첫 7일
    global READ_K
    t0 = time.time()
    world = build_world(N_SIM_USERS)
    test = world["users"][N_SIM_USERS // 2:]
    _prime_base_aff(world, test)
    week, base_w = np.arange(7), world["catalog_weight"]
    print(f"실전 유저 {len(test):,}명 · 첫 7일 · 준비 {time.time() - t0:.0f}초")

    def run(label, users, feed, sets):
        print(f"\n[{label}]")
        print("방법\tRBP\t접속당 좋은 글 열람\t접속당 다시 본 글\t접속당 싫은 글 노출")
        for name, logs in sets:
            s = summary(simulate(world, feed, users, None if logs is None else v1.Config(logs=logs)), week)
            print(f"{name}\t{s['정답 대비 RBP']:.3f}\t{s['접속당 좋은 글 열람']:.2f}\t{s['접속당 다시 본 글']:.1f}\t"
                  f"{s['접속당 싫은 글 노출']:.2f}\t({time.time() - t0:.0f}초)", flush=True)

    feed = make_feed(world, backlog_days=10, per_day=30)
    run("기본 (EXP-20과 같음)", test, feed, SENS_LOGS)
    READ_K = 2.0
    run("읽은 정도 흐리게 (READ_K 6 → 2)", test, feed, SENS_LOGS)
    READ_K = 6.0
    run("적극형 유저 25% → 50%", _sens_users(test, "active50"), feed, SENS_LOGS)
    run("유형 취향 세기 절반", _sens_users(test, "type_weak"), feed, SENS_LOGS)
    run("접속 횟수 절반", _sens_users(test, "half_sessions"), feed, SENS_LOGS)

    # 글이 인기작에 몰릴수록: 작품 고르는 무게 = 인기^0.5(기본) → 인기^1 → 인기^2
    for label, power in (("글 몰림 강하게 (인기^1)", 2), ("글 몰림 아주 강하게 (인기^2)", 4)):
        w = base_w ** power
        world["catalog_weight"] = w / w.sum()
        print(f"\n== {label}: 상위 50개 작품이 글의 {_top_share(world['catalog_weight']):.0%} "
              f"(기본 {_top_share(base_w):.0%})")
        for per_day in (20, 30, 50):
            run(f"{label} · 시작 {10 * per_day}편 + 하루 {per_day}편", test, make_feed(world, backlog_days=10, per_day=per_day),
                [("최신순", None), ("반응 4종 + 노출 + 열람·읽기", BEST_LOGS)])
    world["catalog_weight"] = base_w


TUNE_GRID = [   # v1 비중 하나씩 바꿔 보기 (앞에서 고른 값을 들고 다음 비중으로)
    ("w_type", (0.0, 0.25, 0.5, 1.0, 2.0)),        # 유형 취향
    ("w_seen", (0.0, 0.15, 0.3, 0.6, 1.2)),        # 이미 보여준 횟수 감점
    ("w_fresh", (0.0, 0.15, 0.3, 0.6, 1.2)),       # 새 글 우대
    ("work_feedback", (0.0, 0.25, 0.5, 1.0, 2.0)),  # 작품별 반응
    ("rank_scale", (20.0, 50.0, 100.0, 200.0)),     # v0.5 순위 → 작품 취향이 줄어드는 속도
    ("type_shrink", (1.0, 5.0, 20.0)),              # 반응이 적을 때 유형 취향을 0 쪽으로 당기는 정도
]


def tune_test():
    # v1 비중 고르기: 연습 유저로 비중을 하나씩 고르고, 실전 유저로 출발값과 비교. EXP-20 기록(반응 4종 + 노출 + 열람·읽기),
    # 글 시작 300편 + 하루 30편, 첫 7일
    t0 = time.time()
    world = build_world(N_SIM_USERS)
    feed = make_feed(world, backlog_days=10, per_day=30)
    tune, test = world["users"][: N_SIM_USERS // 2], world["users"][N_SIM_USERS // 2:]
    week = np.arange(7)
    run = lambda users, kw: summary(simulate(world, feed, users, v1.Config(logs=BEST_LOGS, **kw), days=7), week)
    line = lambda s: (f"{s['정답 대비 RBP']:.4f}\t{s['접속당 좋은 글 열람']:.2f}\t{s['접속당 다시 본 글']:.1f}\t"
                      f"{s['접속당 싫은 글 노출']:.2f}\t({time.time() - t0:.0f}초)")
    print(f"연습 {len(tune):,} · 실전 {len(test):,} · 글 시작 300편 + 하루 30편 · 첫 7일 · 준비 {time.time() - t0:.0f}초")
    print("비중\t값\tRBP\t접속당 좋은 글 열람\t접속당 다시 본 글\t접속당 싫은 글 노출")
    best = {}
    for name, values in TUNE_GRID:
        scores = {}
        for v in values:
            s = run(tune, {**best, name: v})
            scores[v] = s["정답 대비 RBP"]
            print(f"{name}\t{v}\t{line(s)}", flush=True)
        best[name] = max(values, key=lambda v: (scores[v], -abs(v - getattr(v1.Config(), name))))   # 같으면 출발값에 가까운 쪽
        print(f"→ {name} = {best[name]}", flush=True)
    print(f"\n고른 비중: {best}\n\n[실전]\n방법\tRBP\t접속당 좋은 글 열람\t접속당 다시 본 글\t접속당 싫은 글 노출")
    print(f"출발값\t{line(run(test, {}))}", flush=True)
    print(f"고른 비중\t{line(run(test, best))}", flush=True)


TUNED = {"w_type": 0.25, "w_seen": 0.3, "w_fresh": 0.0, "work_feedback": 2.0, "rank_scale": 200.0, "type_shrink": 20.0}


def tune_check():
    # 비중 고르기 결과가 가정에 기대는지: 유저가 소식의 새로움을 훨씬 중요하게 여기면(새로움이 줄어드는 날 20 → 5일)
    # w_fresh 는? 유형 취향이 두 배 세면 w_type 은? (연습 유저, 나머지 비중은 고른 값)
    global TIMELY_TAU
    t0 = time.time()
    world = build_world(N_SIM_USERS)
    feed = make_feed(world, backlog_days=10, per_day=30)
    tune = world["users"][: N_SIM_USERS // 2]
    week = np.arange(7)

    def show(label, users, name, values):
        print(f"\n[{label}]\n{name}\tRBP\t접속당 좋은 글 열람\t접속당 다시 본 글\t접속당 싫은 글 노출")
        for v in values:
            s = summary(simulate(world, feed, users, v1.Config(logs=BEST_LOGS, **{**TUNED, name: v}), days=7), week)
            print(f"{v}\t{s['정답 대비 RBP']:.4f}\t{s['접속당 좋은 글 열람']:.2f}\t{s['접속당 다시 본 글']:.1f}\t"
                  f"{s['접속당 싫은 글 노출']:.2f}\t({time.time() - t0:.0f}초)", flush=True)

    old = TIMELY_TAU
    TIMELY_TAU = 5.0
    show("소식·행사 관심이 5일 만에 줄어듦 (기본 20일)", tune, "w_fresh", (0.0, 0.15, 0.3, 0.6))
    TIMELY_TAU = old
    strong = [replace(u, type_mult=u.type_mult ** 2) for u in tune]
    show("유형 취향 두 배 (좋아하는 유형 3.3배 · 싫어하는 유형 0.3배)", strong, "w_type", (0.25, 0.5, 1.0))


REFRESH = [("접속마다 다시 계산", "visit"), ("하루 한 번 + 연 글 · 그만볼래요 빼기", "daily_filter"), ("하루 한 번 그대로", "daily"),
           ("(결과 보고 추가) 하루 한 번 + 빼기 + 오늘 보여 준 글 뒤로", "daily_seen")]


def refresh_test():
    # EXP-30: 반응을 언제 반영하나. 서비스가 v1 순서를 하루 한 번(배치)만 계산하면 접속마다 계산할 때보다 얼마나 나빠지나
    # 접속마다 = 지금 시뮬레이터 (작품 취향은 온보딩으로 한 번, 반응 · 본 글은 접속마다). 실전 유저, EXP-22와 같은 조건
    # 기준 (미리 정함): "하루 한 번 + 빼기" RBP가 접속마다의 95% 이상이고 접속당 다시 본 글이 1장 넘게 늘지 않으면 하루 한 번으로 충분
    t0 = time.time()
    world = build_world(N_SIM_USERS)
    feed = make_feed(world, backlog_days=10, per_day=30)
    test = world["users"][N_SIM_USERS // 2:]
    week = np.arange(7)
    print(f"실전 유저 {len(test):,}명 · 글 시작 300편 + 하루 30편 · 첫 7일 · v1 출발값 · 기록 = 반응 4종 + 노출 + 열람·읽기")
    print("방법	RBP	접속당 좋은 글 열람	접속당 다시 본 글	접속당 싫은 글 노출	100접속당 그만볼래요")
    rows = [("(참고) 최신순", None, "visit")] + [(name, v1.Config(logs=BEST_LOGS), mode) for name, mode in REFRESH]
    for name, cfg, mode in rows:
        s = summary(simulate(world, feed, test, cfg, days=7, update=mode), week)
        print(f"{name}	{s['정답 대비 RBP']:.4f}	{s['접속당 좋은 글 열람']:.2f}	{s['접속당 다시 본 글']:.1f}	"
              f"{s['접속당 싫은 글 노출']:.2f}	{s['100접속당 그만볼래요']:.2f}	({time.time() - t0:.0f}초)", flush=True)


# ## 5. 유저 테스트 평가 설계 (4-1)
# v1 과 최신순을 한 피드에 섞어 보여 주고(팀 드래프트 인터리빙, Radlinski 외 2008) 접속마다 어느 쪽 글에 더 반응했는지 센다
# 몇 명 × 며칠이면 "v1 이 낫다"를 잡는지(검정력)를 반으로 나눠 보여 주기(A/B)와 비교한다
EVAL_USERS = (10, 20, 30, 50, 100)
EVAL_DAYS = (3, 7, 14)
EVAL_DRAWS = 500            # 인원·기간 칸마다 실전 유저 1,000명에서 뽑아 보는 횟수
WIN_RULES = ("열람", "좋아요·저장", "반응 점수 합")   # 접속마다 이긴 팀을 정하는 기준


def team_draft(a, b, k, rng):
    # 두 팀이 번갈아 자기 순서에서 아직 안 뽑힌 가장 위 글을 넣는다. 뽑은 수가 같으면 동전으로 먼저 뽑을 팀을 정함
    # 돌려줌: 섞은 순서, 글마다 넣은 팀 (0 = a, 1 = b)
    lists, idx, picks = (a.tolist(), b.tolist()), [0, 0], [0, 0]
    out, team, used = [], [], set()
    while len(out) < k:
        side = (0 if picks[0] < picks[1] else 1) if picks[0] != picks[1] else int(rng.random() < 0.5)
        for s in (side, 1 - side):                     # 이 팀 글이 다 떨어지면 다른 팀이 마저 채움
            while idx[s] < len(lists[s]) and lists[s][idx[s]] in used:
                idx[s] += 1
            if idx[s] < len(lists[s]):
                side = s
                break
        else:
            break
        c = lists[side][idx[side]]
        out.append(c)
        team.append(side)
        used.add(c)
        picks[side] += 1
    return np.array(out, dtype=np.int64), np.array(team, dtype=np.int8)


def rule_scores(events, team, pos_of):
    # 접속 하나에서 기준마다 팀별 점수 (team[pos_of[글]] = 그 글을 넣은 팀). 반응 점수 합은 v1 반응 점수 (읽은 정도가 열람을 대신함)
    s = np.zeros((len(WIN_RULES), 2))
    for kind, c, val in events:
        side = team[pos_of[c]]
        if kind == "open":
            s[0, side] += 1
        elif kind == "read":
            key = "read_full" if val >= v1.READ_FULL else ("read_bounce" if val < v1.READ_BOUNCE else "read_some")
            s[2, side] += v1.SIGNAL[key]
        elif kind in ("like", "save", "more", "share", "less"):
            s[2, side] += v1.SIGNAL[kind]
            if kind in ("like", "save"):
                s[1, side] += 1
    return s


def simulate_eval(world, feed, users, cfg, mode, seed=SEED, days=N_DAYS):
    """mode: "mix" = v1(cfg) 과 최신순을 섞어 보여 줌 / "v1" · "latest" = 한쪽만 (A/B 의 한 그룹).
    돌려줌: 이긴 접속 수 (유저 × 날 × 기준 × (v1 이김, 최신순 이김, 비김)), 점수 합 (유저 × 날 × 기준), 접속 수 (유저 × 날)"""
    ctx, cat_ids, n_feed = world["ctx"], world["catalog_ids"], len(feed["day"])
    wins = np.zeros((len(users), days, len(WIN_RULES), 3), dtype=np.int32)
    score = np.zeros((len(users), days, len(WIN_RULES)))
    n_sess = np.zeros((len(users), days), dtype=np.int32)
    for ui, user in enumerate(users):
        rng, mix_rng = np.random.default_rng([seed, ui, 7]), np.random.default_rng([seed, ui, 13])
        seen = np.zeros(n_feed, dtype=np.int16)
        opened = np.zeros(n_feed, dtype=bool)
        state = None
        if mode != "latest":
            ob = user.onboard
            state = v1.UserState(ob["history"], ob["genres"], ob["themes"], ob["types"], ob["t"], len(cat_ids), n_feed)
            if user.base_aff is None or cfg.rank_scale not in user.base_aff:
                v1.refresh_work_affinity(state, ctx, cfg)
                user.base_aff = {**(user.base_aff or {}), cfg.rank_scale: state.work_aff}
            state.work_aff, state.dirty = user.base_aff[cfg.rank_scale].copy(), False
        for day in range(days):
            if state is not None and state.dirty:
                v1.refresh_work_affinity(state, ctx, cfg)
            for _ in range(int(user.sessions[day])):
                team = None
                if mode == "latest":
                    order = latest_first(feed, day)
                else:
                    order = v1.rank_feed(state, feed, day, cfg, FEED_LEN)
                    if mode == "mix":
                        order, team = team_draft(order, latest_first(feed, day), FEED_LEN, mix_rng)
                    v1.mark_served(state, order, cfg)
                gain = utility(user, feed, order, day, seen, opened)
                events = browse(user, feed, order, gain, rng, seen, opened)
                s = rule_scores(events, np.zeros(len(order), dtype=np.int8) if team is None else team,
                                {c: i for i, c in enumerate(order.tolist())})
                n_sess[ui, day] += 1
                if mode == "mix":
                    wins[ui, day, :, 0] += s[:, 0] > s[:, 1]
                    wins[ui, day, :, 1] += s[:, 1] > s[:, 0]
                    wins[ui, day, :, 2] += s[:, 0] == s[:, 1]
                else:
                    score[ui, day] += s[:, 0]
                if state is not None:
                    v1.learn(state, events, feed, cfg, cat_ids)
    return wins, score, n_sess


def sign_p(n_pos, n_neg):
    # 부호 검정 (양쪽): v1 이 이긴 유저 수 vs 최신순이 이긴 유저 수가 반반에서 이만큼 벗어날 확률
    n = n_pos + n_neg
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(max(n_pos, n_neg), n + 1)) / 2 ** n)


def power_mix(wins, n, days, r, rng, draws=EVAL_DRAWS):
    # 섞어 보여 주기: 뽑은 n명마다 앞 days 일 동안 v1 이 이긴 접속이 더 많은지 → 유저 단위 부호 검정 (p < 0.05 이고 v1 쪽)
    ok = 0
    for _ in range(draws):
        w = wins[rng.choice(len(wins), n, replace=False), :days, r].sum(axis=1)
        pos, neg = int((w[:, 0] > w[:, 1]).sum()), int((w[:, 1] > w[:, 0]).sum())
        ok += pos > neg and sign_p(pos, neg) < 0.05
    return ok / draws


def power_ab(score_a, sess_a, score_b, sess_b, n, days, r, rng, draws=EVAL_DRAWS, perms=1000):
    # 반으로 나눠 보여 주기: n명을 반씩 v1 · 최신순 그룹으로 → 유저별 접속당 점수 평균을 비교 (순열 검정, p < 0.05 이고 v1 쪽)
    ok = 0
    for _ in range(draws):
        pick = rng.choice(len(score_a), n, replace=False)
        ga, gb = pick[: n // 2], pick[n // 2:]
        sa, sb = sess_a[ga, :days].sum(axis=1), sess_b[gb, :days].sum(axis=1)
        xa = score_a[ga, :days, r].sum(axis=1)[sa > 0] / sa[sa > 0]
        xb = score_b[gb, :days, r].sum(axis=1)[sb > 0] / sb[sb > 0]
        if len(xa) < 2 or len(xb) < 2:
            continue
        x, na = np.r_[xa, xb], len(xa)
        lab = np.argsort(rng.random((perms, len(x))), axis=1) < na
        diffs = (lab * x).sum(axis=1) / na - (~lab * x).sum(axis=1) / (len(x) - na)
        obs = xa.mean() - xb.mean()
        ok += obs > 0 and (np.abs(diffs) >= abs(obs)).mean() < 0.05
    return ok / draws


def eval_test():
    # 4-1: 유저 테스트에서 v1 이 최신순보다 나은지 판단하는 법. 글 시작 300편 + 하루 30편, 실전 유저 1,000명, 14일
    # 효과 2가지: v1 (반응 4종 + 노출 + 열람·읽기 학습) vs 최신순 / 더 약한 경우: v1 온보딩만 (기록 학습 없음) vs 최신순
    # 기준 (미리 정함): 검정력 0.8 이상이 되는 가장 작은 인원 · 기간을 추천. 이김 기준은 그 인원에서 검정력이 높은 것
    t0 = time.time()
    world = build_world(N_SIM_USERS)
    feed = make_feed(world, backlog_days=10, per_day=30)
    test = world["users"][N_SIM_USERS // 2:]
    print(f"실전 유저 {len(test):,}명 · 글 시작 300편 + 하루 30편 · {N_DAYS}일 · 준비 {time.time() - t0:.0f}초", flush=True)
    _, lat_score, lat_sess = simulate_eval(world, feed, test, None, "latest")
    print(f"최신순만 ({time.time() - t0:.0f}초)", flush=True)
    for label, cfg in (("v1 (기록 학습) vs 최신순", v1.Config(logs=BEST_LOGS)), ("더 약한 경우: v1 온보딩만 vs 최신순", v1.Config())):
        wins, _, _ = simulate_eval(world, feed, test, cfg, "mix")
        _, v_score, v_sess = simulate_eval(world, feed, test, cfg, "v1")
        print(f"\n== {label} ({time.time() - t0:.0f}초)")
        tot = wins.sum(axis=(0, 1))
        for r, name in enumerate(WIN_RULES):
            a, b, tie = tot[r]
            print(f"[{name}] 섞어 보여 준 접속 {a + b + tie:,}번 중 v1 이김 {a / (a + b + tie):.1%} · 최신순 이김 {b / (a + b + tie):.1%} "
                  f"· 비김 {tie / (a + b + tie):.1%} (승부 난 접속 중 v1 {a / max(a + b, 1):.1%})")
        rng = np.random.default_rng([SEED, 41])
        for r, name in enumerate(WIN_RULES):
            print(f"\n[{name}] 검정력 = \"v1 이 낫다\"를 잡는 비율 (섞기 / 반으로 나누기)\n인원\t" + "\t".join(f"{d}일" for d in EVAL_DAYS))
            for n in EVAL_USERS:
                cells = [f"{power_mix(wins, n, d, r, rng):.2f} / {power_ab(v_score, v_sess, lat_score, lat_sess, n, d, r, rng):.2f}"
                         for d in EVAL_DAYS]
                print(f"{n}명\t" + "\t".join(cells), flush=True)
        print(f"({time.time() - t0:.0f}초)", flush=True)


def main():
    if sys.argv[1:2] == ["eval"]:
        return eval_test()
    if sys.argv[1:2] == ["refresh"]:
        return refresh_test()
    if sys.argv[1:2] == ["pool"]:
        return pool_test()
    if sys.argv[1:2] == ["tune"]:
        return tune_test()
    if sys.argv[1:2] == ["tune_check"]:
        return tune_check()
    if sys.argv[1:2] == ["logs"]:
        return log_test()
    if sys.argv[1:2] == ["sens"]:
        return sensitivity_test()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else N_SIM_USERS
    t0 = time.time()
    world = build_world(n)
    feed = make_feed(world)
    tune = world["users"][: n // 2]
    print(f"시뮬레이션 유저 {n:,}명 (연습 {len(tune):,}) · 통계 유저 {world['n_stats_users']:,}명 · "
          f"글 {len(feed['day']):,}편 · 준비 {time.time() - t0:.0f}초")
    print(f"페르소나 {dict(Counter(u.persona for u in tune))}")
    print(f"반응 성향 {dict(Counter(u.style_name for u in tune))} · 평균 접속 {np.mean([u.sessions.mean() for u in tune]):.1f}회/일\n")
    variants = [("최신순", None),
                ("v1 온보딩만", v1.Config()),
                ("v1 + 반응 (O/X·좋아요·저장)", v1.Config(logs=frozenset({"more", "less", "like", "save"}))),
                ("v1 + 모든 기록", v1.Config(logs=frozenset(v1.LOGS)))]
    first, second = np.array([0]), np.arange(7, N_DAYS)
    print("방법\t첫날 RBP\t둘째 주 RBP\t접속당 본 글\t접속당 좋은 글 열람\t100접속당 그만볼래요")
    for name, cfg in variants:
        tot = simulate(world, feed, tune, cfg)
        a, b = summary(tot, first), summary(tot, second)
        print(f"{name}\t{a['정답 대비 RBP']:.3f}\t{b['정답 대비 RBP']:.3f}\t{b['접속당 본 글']:.1f}\t"
              f"{b['접속당 좋은 글 열람']:.2f}\t{b['100접속당 그만볼래요']:.2f}\t({time.time() - t0:.0f}초)", flush=True)


if __name__ == "__main__":
    main()
