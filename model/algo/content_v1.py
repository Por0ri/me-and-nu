# content_v1 — 글 추천 v1 시제품. 글 점수 = 작품 취향 + 유형 취향 + 최신성 + 반응
#
# 작품 취향은 v0.5(anime_ranker.next_auto) 순위로 만들고, 유형 취향과 반응은 유저 상태(UserState)에 쌓는다.
# 아직 서비스 DB 에는 안 붙었다. 가짜 유저 시뮬레이터(model/eval/feed_simulator.py)로 설계를 비교하는 단계라
# 아래 숫자는 모두 출발값이다. 값은 시뮬레이터 연습 유저로 고른다.
#
# 글 표(feed)는 칸별 배열 dict 로 받는다: pos(작품의 카탈로그 위치. 작품이 없는 글은 -1) · type(유형 번호) · day(게시일) · until(노출 종료일)
# 카탈로그 = v0.5 의 같이 본 횟수 표 작품 (ctx["cw_rows"]. 실험 문맥은 인기 상위 3,000편, 서비스 문맥(service.py)은 me;nu 작품 목록 전체)

from dataclasses import dataclass, field

import numpy as np

from model.algo import anime_ranker as ar

ALGORITHM_VERSION = "v1-sim"

# 콘텐츠 유형 11개 (콘텐츠 명세서 8개 + 캐릭터·음악·유머)
TYPES = ("news", "event", "guide", "people", "curation", "goods", "behind", "review", "character", "music", "humor")
TYPE_INDEX = {t: i for i, t in enumerate(TYPES)}

# 추천기가 쓸 수 있는 기록. 노출 impression · 열람 open · 읽기 read · 더볼래요 more · 그만볼래요 less ·
# 좋아요 like · 저장 save · 공유 share. more · less · like · save 는 ERD 에 이미 있는 현재 상태 표
# (content_reaction · saved_item)로도 알 수 있다. 어떤 기록을 모을지는 시뮬레이터 실험으로 정한다
LOGS = ("impression", "open", "read", "more", "less", "like", "save", "share")

# 기록별 신호 세기 (출발값). 손이 많이 가는 반응일수록 강하게, 안 누르고 지나친 건 약한 마이너스
SIGNAL = {
    "save": 1.0, "more": 1.0, "like": 0.8, "share": 0.8,
    "less": -2.0,
    "open": 0.3,                                                # 열어 봄 (읽은 정도를 모를 때)
    "read_full": 0.8, "read_some": 0.3, "read_bounce": -0.2,    # 읽은 정도를 알 때는 open 대신 이걸 씀
    "skip": -0.05,                                              # 보여줬는데 안 누름 (노출 기록이 있을 때만)
}
READ_FULL, READ_BOUNCE = 0.8, 0.3   # 읽은 비율이 이 이상이면 끝까지 읽음, 이 미만이면 바로 나감

# 유형별 최신성이 약 1/e 로 줄어드는 날 수. 나머지 유형은 30일
FRESH_TAU = {"news": 3.0, "event": 7.0, "goods": 7.0}
TAU = np.array([FRESH_TAU.get(t, 30.0) for t in TYPES])


@dataclass
class Config:
    logs: frozenset = frozenset()   # 쓸 수 있는 기록 (LOGS 중에서)
    w_work: float = 1.0             # 작품 취향
    w_type: float = 0.5             # 유형 취향
    w_fresh: float = 0.3            # 최신성
    w_seen: float = 0.3             # 이미 보여준 횟수만큼 감점
    served_depth: int = 10          # 노출 기록이 없을 때: 보낸 목록(recommendation_item)의 위쪽 몇 개를 봤다고 칠지
    work_feedback: float = 0.5      # 작품별 반응 합이 작품 취향에 더해지는 세기
    type_prior: float = 1.0         # 온보딩에서 고른 유형
    type_shrink: float = 5.0        # 반응이 적을 때 유형 취향이 흔들리지 않게 0 쪽으로 당기는 정도
    rank_scale: float = 50.0        # v0.5 순위 → 작품 취향: 1 / (1 + 순위 / rank_scale)
    add_liked_works: bool = False   # 반응이 강한 작품을 v0.5 입력(본 작품)에 더할지
    liked_threshold: float = 0.8    # 작품별 반응 합이 이 이상이면 좋아하는 작품으로 침


@dataclass
class UserState:
    """추천기가 아는 유저 정보. 기록을 받을 때마다 learn() 으로 고친다"""
    history: ar.History             # v0.5 입력 (온보딩 작품. add_liked_works 면 반응으로 좋아한 작품도)
    genres: list                    # 온보딩 장르 칩 (AniList 장르)
    themes: list                    # 온보딩 테마 칩 (AniList 태그)
    types: list                     # 온보딩에서 고른 유형 (TYPES 코드)
    t: int                          # v0.5 기준 시각 (초)
    n_catalog: int
    n_feed: int
    work_aff: np.ndarray = None     # 카탈로그 작품별 v0.5 취향 (0~1)
    dirty: bool = True              # 작품 입력이 바뀌어 v0.5 를 다시 돌려야 함
    work_ev: np.ndarray = field(init=False)   # 카탈로그 작품별 반응 점수 합
    type_ev: np.ndarray = field(init=False)   # 유형별 반응 점수 합
    type_n: np.ndarray = field(init=False)    # 유형별 반응 수
    seen: np.ndarray = field(init=False)      # 글별 노출 횟수 (노출 기록이 있을 때만 셈)
    served: np.ndarray = field(init=False)    # 글별로 보낸 목록 위쪽에 들어간 횟수 (기록 없이도 서버가 앎)
    opened: np.ndarray = field(init=False)    # 연 글 (열람·읽기 기록이 있을 때만 앎)
    blocked: np.ndarray = field(init=False)   # 그만볼래요 한 글

    def __post_init__(self):
        self.work_ev = np.zeros(self.n_catalog, dtype=np.float32)
        self.type_ev = np.zeros(len(TYPES))
        self.type_n = np.zeros(len(TYPES))
        self.seen = np.zeros(self.n_feed, dtype=np.int16)
        self.served = np.zeros(self.n_feed, dtype=np.int16)
        self.opened = np.zeros(self.n_feed, dtype=bool)
        self.blocked = np.zeros(self.n_feed, dtype=bool)


def refresh_work_affinity(state, ctx, cfg):
    # v0.5 순위 → 카탈로그 작품별 취향 (1위 1.0, 50위 0.5, 200위 0.2 ...). 고른 작품과 같은 시리즈는 1
    user = {"history": state.history, "t": state.t, "genres": state.genres, "themes": state.themes}
    pos = ctx["cw_pos"][ar.rows_of(ctx, ar.next_auto(user, ctx))]
    rank = np.flatnonzero(pos >= 0)
    first_pos, first = np.unique(pos[rank], return_index=True)   # 같은 작품이 두 번 나오면 앞 순위
    aff = np.zeros(state.n_catalog, dtype=np.float32)
    aff[first_pos] = 1.0 / (1.0 + rank[first] / cfg.rank_scale)
    mine = {ctx["series"].get(a, a) for a in state.history.anime.tolist()}
    own = [ctx["row"][x] for s in mine for x in ctx["series_items"].get(s, (s,)) if x in ctx["row"]]
    p = ctx["cw_pos"][np.array(own, dtype=np.int64)] if own else np.array([], dtype=np.int64)
    aff[p[p >= 0]] = 1.0
    state.work_aff, state.dirty = aff, False


def type_pref(state, cfg):
    # 유형 취향 = 온보딩에서 고른 유형 + 유형별 반응 평균 (반응이 적으면 0 쪽으로)
    prior = np.zeros(len(TYPES))
    prior[[TYPE_INDEX[x] for x in state.types]] = cfg.type_prior
    return prior + state.type_ev / (state.type_n + cfg.type_shrink)


def rank_feed(state, feed, day, cfg, k=50):
    """오늘(day) 보여줄 글 번호 k개를 점수 순으로. 노출 기간이 지난 글, 아는 범위에서 이미 연 글·그만볼래요 한 글은 뺀다"""
    live = np.flatnonzero((feed["day"] <= day) & (feed["until"] >= day))
    pos, typ = feed["pos"][live], feed["type"][live]
    s = np.where(pos >= 0, cfg.w_work * (state.work_aff[pos] + cfg.work_feedback * np.tanh(state.work_ev[pos])), 0.0)
    s = s + cfg.w_type * type_pref(state, cfg)[typ]
    s = s + cfg.w_fresh * np.exp(-(day - feed["day"][live]) / TAU[typ])
    shown = state.seen if "impression" in cfg.logs else state.served   # 노출 기록이 없으면 보낸 목록으로 짐작
    s = s - cfg.w_seen * shown[live]
    s[state.opened[live] | state.blocked[live]] = -np.inf
    if not len(s):
        return live
    top = np.argpartition(-s, min(k, len(s) - 1))[:k]
    top = top[np.lexsort((live[top], -s[top]))]                   # 점수 순 (같으면 글 번호 순)
    return live[top[np.isfinite(s[top])]]


def mark_served(state, order, cfg):
    # 보낸 목록 기록 (recommendation_item). 노출 기록이 없을 때 위쪽 served_depth 개는 봤다고 친다
    state.served[order[:cfg.served_depth]] += 1


def _signal(state, feed, c, w, cfg, catalog_ids):
    p, ty = feed["pos"][c], feed["type"][c]
    if p >= 0:                                                    # 작품이 없는 글은 유형 취향에만
        state.work_ev[p] += w
    state.type_ev[ty] += w
    state.type_n[ty] += 1
    if cfg.add_liked_works and w > 0 and p >= 0 and state.work_ev[p] >= cfg.liked_threshold:
        a = int(catalog_ids[p])
        if a not in set(state.history.anime.tolist()):            # v0.5 입력에 "본 작품"으로 더함
            h = state.history
            state.history = ar.History(np.r_[h.t, state.t], np.r_[h.anime, a], np.r_[h.status, ar.STATUS["completed"]],
                                       np.r_[h.score, 0], np.r_[h.fav, 0])
            state.dirty = True


def learn(state, events, feed, cfg, catalog_ids):
    """한 번 접속에서 생긴 기록으로 유저 상태를 고친다. cfg.logs 에 없는 기록은 안 모은다고 보고 버린다.
    events: (종류, 글 번호, 값) 목록. 종류: impression · open · read(값=읽은 비율) · like · save · more · less · share"""
    logs, opened_now, shown = cfg.logs, set(), []
    for kind, c, val in events:
        if kind == "impression":
            shown.append(c)
            if "impression" in logs:
                state.seen[c] += 1
        elif kind == "open":
            opened_now.add(c)
            if "open" in logs or "read" in logs:
                state.opened[c] = True
            if "open" in logs and "read" not in logs:
                _signal(state, feed, c, SIGNAL["open"], cfg, catalog_ids)
        elif kind == "read":
            if "read" in logs:
                key = "read_full" if val >= READ_FULL else ("read_bounce" if val < READ_BOUNCE else "read_some")
                _signal(state, feed, c, SIGNAL[key], cfg, catalog_ids)
        elif kind in ("more", "less", "like", "save", "share"):
            if kind in logs:
                if kind == "less":
                    state.blocked[c] = True
                _signal(state, feed, c, SIGNAL[kind], cfg, catalog_ids)
    if "impression" in logs:
        for c in shown:
            if c not in opened_now:
                _signal(state, feed, c, SIGNAL["skip"], cfg, catalog_ids)
