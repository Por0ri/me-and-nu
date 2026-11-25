# service — 모델을 서비스 데이터에 잇는다 (EXPERIMENTS 4장 3-2 연결 연습)
#
# 실험은 MAL 작품 번호로 했지만 서비스는 me;nu 작품 목록(AniList 번호, make_work_catalog.py)을 쓴다.
# 여기서 v0.5(anime_ranker) 문맥을 me;nu 작품 목록 위에 만들고, 글을 v1(content_v1) 글 표로 바꾼다.
# 작품 = 시즌 · 극장판 (AniList 번호), 시리즈 = me;nu 작품 번호. 글은 me;nu 작품 번호(시리즈)에 붙는다
#
# 문맥 재료
# - 장르 · 태그: AniList (media.jsonl · media_recent.jsonl) / 스튜디오 · 제작진 · 성우: detail.jsonl
# - 인기: AniList popularity (그 작품을 목록에 담은 사람 수)
# - 최근 인기: AniList 유저 표본(fetch_anilist_lists.py)에서 최근 30일 안에 목록을 고친 기록 수. 표본이 없으면 0
#   (표본 원본은 12/31까지 지우기로 약속 → 그 뒤엔 AniList trending 이나 me;nu 기록으로 바꿔야 함)
# - 같이 본 작품 · EASE: MAL 시청 기록 (AniList 번호로 바꿈) + AniList 유저 표본 (EXP-36 · 37). MAL 표본은 2022-03 에 모아서
#   2022년 이후 작품은 기록이 없거나 일부뿐 → MAL 에선 그 작품 기록을 빼고 AniList 표본 기록으로 센다.
#   같이 본 유저가 100명 미만인 작품은 비슷한 작품에서 빌려 온다 (EXP-23 · 28과 같은 방식). build(cowatch="mal") = MAL 만
#   (표본 원본은 12/31 삭제 약속 → 그 전에 같이 본 횟수 표만 저장해 두고 그걸로 계산해야 함)
# - 커뮤니티 추천: AniList recommendations (links.jsonl)

import csv
import gzip
import json
import math
import pathlib
import pickle
import re
from collections import deque
from datetime import timedelta

import numpy as np

from model.algo import anime_ranker as ar
from model.algo import content_v1 as v1

MODEL = pathlib.Path(__file__).resolve().parents[1]
DATA = MODEL / "data"
ANILIST = DATA / "anilist"
ENTRIES = MODEL.parent / "catalog" / "menu_work_entries.csv"   # me;nu 작품 목록 (레포 맨 위 공용 폴더. 서버 · 에이전트도 같은 파일)
WORKS = MODEL.parent / "catalog" / "menu_works.csv"
ANILIST_USERS = DATA / "anilist_users" / "entries.csv"
NO_COWATCH_FROM = 2022   # 이 해부터 시작한 작품은 MAL 같이 본 기록을 안 씀 (위 설명)

# 글 노출 규칙: 유형마다 피드에 언제까지 보여줄지
NEWS_DAYS = 30           # 소식: 발표일부터 30일. 행사 · 발매 날짜가 더 늦으면 그날까지
EVENT_OPEN_DAYS = 30     # 행사: 끝 날짜까지. 시작일만 있으면 시작일부터 30일
GOODS_DAYS = 14          # 굿즈: 판매 끝 날짜까지. 발매(판매 시작)일만 있으면 그 뒤 14일
EVERGREEN_DAYS = 90      # 그 밖의 유형 (가이드 · 인물 · 큐레이션 · 비하인드 · 리뷰 · 캐릭터 · 음악 · 유머): 발표일부터 90일


# ## 1. 작품 목록
def load_catalog():
    """me;nu 작품 목록 → (작품 목록, 시리즈 dict). 작품: anilist_id · mal_id · menu_work_id · year · popularity · title"""
    with open(ENTRIES, encoding="utf-8-sig") as f:
        entries = [{"anilist_id": int(r["anilist_id"]), "mal_id": int(r["mal_id"]) if r["mal_id"] else None,
                    "menu_work_id": int(r["menu_work_id"]), "format": r["format"], "year": int(r["year"] or 0),
                    "popularity": int(r["popularity"] or 0), "title": r["title_romaji"] or r["title_english"]}
                   for r in csv.DictReader(f)]
    with open(WORKS, encoding="utf-8-sig") as f:
        works = {int(r["menu_work_id"]): {"title_ko": r["title_ko"], "title": r["title_romaji"] or r["title_english"]}
                 for r in csv.DictReader(f)}
    return entries, works


def load_features(ids):
    # AniList 번호 → 장르 · 태그 · 성인 여부 + 스튜디오 · 핵심 제작진 · 주연 성우 (next_anime_test.load_anilist 와 같은 모양)
    # 시작 연도도 같이 (media_recent 에만 있음)
    out, year = {}, {}
    for name in ("media.jsonl", "media_recent.jsonl"):
        with open(ANILIST / name, encoding="utf-8") as f:
            for line in f:
                for m in json.loads(line)["media"]:
                    if m["id"] not in ids:
                        continue
                    tags = [t for t in m["tags"] if not t["isAdult"]]
                    out[m["id"]] = {"genres": set(m["genres"]), "tags": {t["name"]: t["rank"] / 100 for t in tags},
                                    "cats": {t["name"]: t["category"] for t in tags}, "adult": m["isAdult"]}
                    if (m.get("startDate") or {}).get("year"):
                        year[m["id"]] = m["startDate"]
    with open(ANILIST / "detail.jsonl", encoding="utf-8") as f:
        for line in f:
            for d in json.loads(line)["media"]:
                if d["id"] in out:
                    out[d["id"]].update(ar.people_of(d))
    return out, year


def rec_edges(ids):
    # AniList 커뮤니티 추천 (작품 a, 추천 작품 b, 추천 수). 둘 다 목록 안에 있는 것만
    edges = []
    with open(ANILIST / "links.jsonl", encoding="utf-8") as f:
        for line in f:
            for m in json.loads(line)["media"]:
                for node in (m.get("recommendations") or {}).get("nodes", []):
                    b = (node.get("mediaRecommendation") or {}).get("id")
                    if m["id"] in ids and b in ids:
                        edges.append((m["id"], b, node.get("rating") or 0))
    return edges


# ## 2. 기록
def mal_histories(entries):
    """MAL 시청 기록 → 유저마다 History. 작품 번호를 AniList 번호로 바꾸고, 목록 밖 작품은 뺀다"""
    from model.eval.next_anime_test import load_interactions
    data = load_interactions()
    to_al = {e["mal_id"]: e["anilist_id"] for e in entries if e["mal_id"]}
    mal = np.array(sorted(to_al), dtype=np.int64)
    al = np.array([to_al[m] for m in mal.tolist()], dtype=np.int64)
    r = np.searchsorted(mal, data["anime"])
    ok = r < len(mal)
    ok[ok] = mal[r[ok]] == data["anime"][ok]
    u, anime = data["user"][ok], al[r[ok]]
    t, status, score, fav = (data[k][ok] for k in ("t", "status", "score", "fav"))
    starts = np.r_[0, np.flatnonzero(np.diff(u)) + 1]           # 기록은 유저별로 모여 있음
    ends = np.r_[starts[1:], len(u)]
    return [ar.History(t[a:b], anime[a:b], status[a:b], score[a:b], fav[a:b]) for a, b in zip(starts, ends)]


def anilist_histories(entries):
    """AniList 유저 표본 → 유저마다 History (목록 안 작품, 모든 기록). 같이 본 작품 재료 (EXP-36). 표본이 없으면 빈 목록
    (원본은 12/31 삭제 약속 → 그 전에 같이 본 횟수 표로 바꿔 둬야 함)"""
    if not ANILIST_USERS.exists():
        return []
    from model.eval.anilist_test import history, load_users
    ids = np.array(sorted(e["anilist_id"] for e in entries), dtype=np.int64)
    return [history(u, m) for u in load_users().values() for m in [np.isin(u["anime"], ids)] if m.any()]


def no_mal_ids(entries):
    # MAL 같이 본 기록을 안 쓰는 작품: 2022년 이후 · MAL 번호 없음 (위 설명)
    return {e["anilist_id"] for e in entries if e["year"] >= NO_COWATCH_FROM or not e["mal_id"]}


def without(histories, ids):
    # 기록에서 ids 작품을 뺌. 남는 기록이 없는 유저는 뺌
    drop = np.array(sorted(ids), dtype=np.int64)
    return [ar.History(*(col[keep] for col in h)) for h in histories for keep in [~np.isin(h.anime, drop)] if keep.any()]


def anilist_events():
    """AniList 유저 표본 → (작품 번호 배열, 목록을 마지막으로 고친 시각 배열). 최근 인기용. 표본이 없으면 None"""
    if not ANILIST_USERS.exists():
        return None
    ids, ts = [], []
    with open(ANILIST_USERS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            u = r.get("updated_at") or ""
            if u.isdigit() and int(u) > 0:                       # 받는 중이면 마지막 줄이 덜 써졌을 수 있음
                ids.append(int(r["media_id"]))
                ts.append(int(u))
    return (np.array(ids, dtype=np.int64), np.array(ts, dtype=np.int64)) if ids else None


# ## 3. 문맥
COWATCH = "both"     # 같이 본 작품 재료 기본값 (EXP-37)
MIN_USERS = 100      # 같이 본 유저가 이 수 미만인 작품은 적은 기록 대신 비슷한 작품에서 빌려 옴 (EXP-37, 10 · 30 · 100 · 300 중)


def build(cowatch=COWATCH, min_users=MIN_USERS):
    """서비스 문맥 = 기본 문맥 + 같이 본 작품. cowatch:
    "both" (기본, EXP-37) = MAL (2022년 이후 · MAL 번호 없는 작품 기록은 뺌) + AniList 표본. 같이 본 유저가 min_users 명 미만인
      작품은 빌려 옴 (기록이 묵어도 버팀, EXP-35 ②). EASE 규제는 기록 유저 수에 비례 (anilist_test.variants 와 같음)
    "mal" = MAL 기록만 (2022년 이후 · MAL 번호 없는 작품은 기록을 지우고 빌려 옴). 표본이 없으면 이쪽"""
    ctx = base()
    entries = ctx["entries"]
    mal, no_mal = mal_histories(entries), no_mal_ids(entries)
    al = anilist_histories(entries) if cowatch == "both" else []
    if al:
        hs = without(mal, no_mal) + al
        add_cowatch(ctx, hs, lam=ar.EASE_LAMBDA * len(hs) / len(mal), min_users=min_users)
    else:
        add_cowatch(ctx, mal, no_mal)
    return ctx


def base(popularity=None, events=None):
    """me;nu 작품 목록 위의 v0.5 문맥에서 같이 본 작품만 뺀 것 (add_cowatch 로 붙임). 실험 문맥에 더해
    work_of(작품 → me;nu 작품 번호) · rep_of(me;nu 작품 번호 → 대표 작품) · works(me;nu 작품 정보) · entries(작품 목록)를 넣는다.
    popularity: 작품 → 인기 (기본: AniList popularity). events: 최근 인기용 (작품 배열, 시각 배열) (기본: AniList 유저 표본)"""
    entries, works = load_catalog()
    ids = {e["anilist_id"] for e in entries}
    anilist, year = load_features(ids)
    from model.eval.next_anime_test import load_anime
    mal_start = {a: v["start_date"] for a, v in load_anime().items() if v["start_date"]}
    anime = {}
    for e in entries:
        a, sd = e["anilist_id"], year.get(e["anilist_id"])
        start = (f"{sd['year']:04d}-{sd.get('month') or 1:02d}-{sd.get('day') or 1:02d}" if sd
                 else mal_start.get(e["mal_id"]) or (f"{e['year']:04d}-01-01" if e["year"] else None))
        anime[a] = {"title": e["title"], "genres": anilist.get(a, {}).get("genres", set()), "start_date": start}
    rep = {}                                                     # 시리즈 대표 = 그 시리즈에서 가장 인기 있는 작품
    for e in sorted(entries, key=lambda e: (-e["popularity"], e["anilist_id"])):
        rep.setdefault(e["menu_work_id"], e["anilist_id"])
    series = {e["anilist_id"]: rep[e["menu_work_id"]] for e in entries}
    if popularity is None:
        popularity = {e["anilist_id"]: e["popularity"] for e in entries}
    ctx = ar.build_context(anime, series, popularity, anilist, events=anilist_events() if events is None else events)
    if "recent_cum" not in ctx:                                  # 최근 인기 재료가 없으면 0
        ctx["recent_cum"], ctx["recent_d0"] = np.zeros((2, len(ctx["ids"])), dtype=np.int32), 0
    ar.build_rec(ctx, rec_edges(ids))
    ctx.update(work_of={e["anilist_id"]: e["menu_work_id"] for e in entries}, rep_of=rep, works=works,
               entries=entries, anilist=anilist)
    return ctx


def add_cowatch(ctx, histories, no_record=None, lam=None, min_users=1):
    """같이 본 작품 · EASE · 빌려 오기를 붙인다 (목록 전체 작품). no_record: 기록을 지우고 빌려 올 작품.
    안 주면 기록한 유저가 min_users 명 미만인 작품 (기본 1 = 기록이 하나도 없는 작품. 기록이 적은 작품도 빌려 오기, 1-3b).
    ctx["no_record"] 에 남김. lam: EASE 규제 세기 (기본 anime_ranker.EASE_LAMBDA)"""
    ar.build_cowatch(ctx, histories, n_items=len(ctx["ids"]))
    C = ctx["cw_count"]
    if no_record is None:
        no_record = set(ctx["ids"][ctx["cw_rows"][np.diag(C) < min_users]].tolist())
    pos = ctx["cw_pos"][ar.rows_of(ctx, sorted(no_record))]
    C[pos, :] = 0                                                # 일부만 있는 기록은 지운다 (new_anime_test.masked 와 같음)
    C[:, pos] = 0
    ctx["cw_sim"] = ar.cowatch_sims(C, ctx["cw_users"])
    ar.build_ease(ctx, ar.EASE_LAMBDA if lam is None else lam)
    ar.build_cf_borrow(ctx, no_record)
    ctx["no_record"] = no_record


# 서버용 문맥 파일: 서버에는 AniList · MAL 원본을 두지 않는다 (AniList 유저 표본은 원본을 PC 한 대에만 두고 12/31 삭제 약속).
# 내 PC 에서 build() 한 문맥을 파일 하나로 저장하고 서버는 그 파일만 읽는다 (model/scripts/save_context.py).
# 문맥에는 작품끼리 통계 (같이 본 횟수 · EASE · 작품 특징 · 최근 인기 합계)만 있어야 함 → 저장 전에 유저 단위 자료를 검사
CONTEXT_VERSION = 1


def check_no_user_rows(ctx):
    """유저 기록 (History)이나 유저 수만큼 긴 배열이 문맥에 섞였으면 ValueError"""
    n_users = int(ctx.get("cw_users") or 0)

    def walk(v, path):
        if isinstance(v, ar.History):
            raise ValueError(f"유저 기록이 문맥에 있음: {path}")
        if isinstance(v, np.ndarray) and n_users and n_users in v.shape:
            raise ValueError(f"유저 수 ({n_users:,})만큼 긴 배열: {path} {v.shape}")
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{path}.{k}")
        elif isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]")

    for k, v in ctx.items():
        walk(v, k)


def save_context(ctx, path):
    check_no_user_rows(ctx)
    with gzip.open(path, "wb", compresslevel=6) as f:
        pickle.dump({"version": CONTEXT_VERSION, "ctx": ctx}, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_context(path):
    # pickle 은 아무 파일이나 읽으면 위험 → save_context 로 우리가 만든 파일만 읽는다
    with gzip.open(path, "rb") as f:
        data = pickle.load(f)
    if not isinstance(data, dict) or data.get("version") != CONTEXT_VERSION:
        raise ValueError(f"문맥 파일 버전이 다름 (필요 {CONTEXT_VERSION}): {path}")
    return data["ctx"]


def onboarding_user(ctx, menu_work_ids, genres, themes, t):
    """온보딩 → v0.5 유저. 고른 작품(시리즈)마다 대표 작품 하나를 "본 작품"으로 (시즌을 다 넣으면 본 작품 수가 부풀어
    기존 유저 설정으로 넘어감). t: 기준 시각 (초)"""
    anime = np.array([ctx["rep_of"][w] for w in menu_work_ids if w in ctx["rep_of"]], dtype=np.int64)
    n = len(anime)
    h = ar.History(np.full(n, t, dtype=np.int64), anime, np.full(n, ar.STATUS["completed"], dtype=np.int8),
                   np.zeros(n, dtype=np.int8), np.zeros(n, dtype=np.int8))
    return {"history": h, "t": t, "genres": list(genres), "themes": list(themes)}


# ## 4. 글
def exposure_end(ctype, published, event_start=None, event_end=None):
    """글 유형 · 날짜 → (노출 끝 날짜, 메모). 날짜가 모자라 다른 규칙을 쓴 경우 메모를 남긴다. 날짜는 datetime.date"""
    d = timedelta
    if ctype == "event":
        if event_end:
            return event_end, ""
        if event_start:
            return event_start + d(EVENT_OPEN_DAYS), "행사 끝 날짜 없음 → 시작일 + 30일"
        return published + d(NEWS_DAYS), "행사 날짜 없음 → 소식 규칙"
    if ctype == "goods":
        if event_start or event_end:
            return max(x for x in (event_end, event_start and event_start + d(GOODS_DAYS)) if x), ""
        return published + d(NEWS_DAYS), "발매일 없음 → 소식 규칙"
    if ctype == "news":
        return max(x for x in (published + d(NEWS_DAYS), event_end or event_start) if x), ""
    return published + d(EVERGREEN_DAYS), ""


def make_feed(posts, ctx):
    """글 목록 → v1 글 표. posts: dict 목록 (type · menu_work_id(없으면 None) · published · until, 날짜는 date).
    작품이 없거나 목록 밖인 글은 pos = -1 (작품 취향 0).
    글에 works (연결 작품 번호, 연결 순서) · anchor (기준 작품 자리, 없으면 None) 가 있으면 (11/16 결정):
    pos = 기준 작품 자리 (기준이 없는 모음은 -1), rel_pos · rel_w = 기준이 아닌 연결 작품 자리 · 무게 W_RELATED.
    팬 추천 (kind like) 은 기준 작품만 봄 (11/17: 글 안 추천작을 고른 사람에겐 맞지 않는 글)"""
    day = lambda x: (x.toordinal() - 719163)                     # date → 1970-01-01 부터 날 수 (anime_ranker.DAY 와 같은 기준)
    pos, rel = [], []
    for p in posts:
        works = p.get("works")
        if works is None:
            pos.append(_cw_pos(ctx, p["menu_work_id"]))
            rel.append([])
            continue
        a = p.get("anchor")
        pos.append(_cw_pos(ctx, works[a]) if a is not None else -1)
        others = [] if p.get("kind") == "like" else [_cw_pos(ctx, w) for j, w in enumerate(works) if j != a]
        rel.append([q for q in others if q >= 0])
    feed = {"pos": np.array(pos, dtype=np.int64),
            "type": np.array([v1.TYPE_INDEX[p["type"]] for p in posts], dtype=np.int64),
            "day": np.array([day(p["published"]) for p in posts], dtype=np.int64),
            "until": np.array([day(p["until"]) for p in posts], dtype=np.int64)}
    k = max((len(r) for r in rel), default=0)
    if k:
        rel_pos = np.full((len(posts), k), -1, dtype=np.int64)
        for n, r in enumerate(rel):
            rel_pos[n, :len(r)] = r
        feed.update(rel_pos=rel_pos, rel_w=np.where(rel_pos >= 0, W_RELATED, 0.0))
    return feed


def chip_weights(state, ctx):
    """고른 장르 · 소재 칩 → (작품 표에서 찾은 칩 이름 목록, 카탈로그 작품 × 칩 무게). 장르는 있으면 1, 소재는 그 소재가
    작품에서 얼마나 중심인지 (AniList 태그 순위 / 100). 표에 없는 칩은 뺌. 찾은 칩이 없으면 ([], None)"""
    col = ctx["feature_col"]
    found = [(g, col.get(g, col.get("g:" + ar.GENRE_ALIAS.get(g, g)))) for g in state.genres]
    found += [(th, col.get("t:" + th)) for th in state.themes]
    found = [(name, j) for name, j in found if j is not None]
    if not found:
        return [], None
    w = np.asarray(ctx["X"][np.ix_(ctx["cw_rows"], [j for _, j in found])], dtype=np.float32)
    return [name for name, _ in found], w


def _picked_positions(state, ctx):
    # 고른 작품 (같은 시리즈의 시즌 · 극장판 포함) 의 카탈로그 자리 (v1.refresh_work_affinity 가 1 로 두는 자리와 같음)
    mine = {ctx["series"].get(a, a) for a in state.history.anime.tolist()}
    own = [ctx["row"][x] for s in mine for x in ctx["series_items"].get(s, (s,)) if x in ctx["row"]]
    p = ctx["cw_pos"][np.array(own, dtype=np.int64)] if own else np.array([], dtype=np.int64)
    return p[p >= 0]


def work_affinity(state, ctx, cfg):
    """작품 취향 (카탈로그 자리별 0~1). 고른 작품 (같은 시리즈 포함) = 1.
    고른 작품 밖 (11/17 사용자 결정 "인기작이 아니라 고른 장르 · 소재에 맞는 작품"):
      (칩 맞춤 ÷ 카탈로그 최댓값 + W_POP_TIE × 인기) ÷ (1 + W_POP_TIE). 칩 맞춤 = 고른 칩 무게 평균.
      인기는 칩이 거의 같게 맞는 작품끼리 순서를 정하는 정도 (11/17: 동점이면 같은 장르 · 소재 안에서 인기작 먼저).
    찾은 칩이 없으면 예전 v0.5 순위 취향 (인기 · 같이 본 기록 중심) 그대로.
    글은 시리즈 대표 자리를 보므로, 대표 자리에 그 시리즈 작품 중 가장 높은 취향을 둔다"""
    v1.refresh_work_affinity(state, ctx, cfg)
    state.chip_names, state.chip_w = chip_weights(state, ctx)
    if state.chip_w is not None:
        fit = state.chip_w.mean(axis=1)
        top = float(fit.max())
        other = ((fit / top if top > 0 else fit) + W_POP_TIE * ctx["pop_norm"][ctx["cw_rows"]]) / (1 + W_POP_TIE)
        state.work_aff[:] = other.astype(state.work_aff.dtype)
        state.work_aff[_picked_positions(state, ctx)] = 1.0
    aff, pos, row = state.work_aff, ctx["cw_pos"], ctx["row"]
    for s, items in ctx["series_items"].items():
        if len(items) > 1:
            p = pos[[row[a] for a in items]]
            aff[pos[row[s]]] = aff[p[p >= 0]].max()


def genre_affinity(state, ctx):
    """작품별 장르 · 소재 유사도 = v0.5 태그 취향과 같은 계산 (고른 작품의 장르 · 태그 + 칩, 코사인 0~1). 글은 시리즈 대표 자리를 봄"""
    user = {"history": state.history, "t": state.t, "genres": state.genres, "themes": state.themes}
    state.genre_sim = ar.genre_scores(user, ctx)[ctx["cw_rows"]].astype(np.float32)


# ## 5. 서비스 연결 (매일 추천 계산 = scripts/daily_recommend.py)
# 유저 테스트 동안 v1 비중은 고정. 서비스에 맞춰 바꾸는 건 아래 입력 대응표 · 장르 · 소재 가산 (W_GENRE) · 보여 줄 순서 규칙 (arrange) ·
# 추천 이유 문구뿐 (W_GENRE · arrange 는 10/31 사용자 결정). 11/17 베타 전: 고른 작품 밖 작품 취향을 칩 맞춤으로 (work_affinity),
# 여러 작품 글 (make_feed · v1.work_parts) · 보여 줄 자리 (compose) · 이유 (explain) 추가. 시뮬레이터 계산은 그대로

# 글 점수의 작품 몫에 더하는 장르 · 소재 유사도 비중. 로컬 시험 계정 4명으로 고른 출발값 (1.0 부터 고른 유형 글이 줄어듦) → 테스트 뒤 다시 정함
W_GENRE = 0.5
# 이미 본 글 감점 (v1 출발값 0.3). 보고 안 연 감점이 작품 취향을 빠르게 덮어서 줄임. 같은 날 같은 글 노출은 한 번만,
# SEEN_MS 이상 본 노출만 셈 (load_visits)
W_SEEN = 0.1
# 끝까지 본 글 (카드 마지막 장 · 본문 80% 이상)만 본 날 포함 이 날 수 동안 후보에서 뺌. 덜 본 글은 바로 다시 후보
# (11/3 사용자 결정. 그 전엔 열기만 해도 계속 뺐음). 백엔드 피드 (feed_service.REREAD_DAYS) 와 같은 값
REREAD_DAYS = 14
# 화면에 나왔지만 끝까지 안 본 글이 맨 위 · 곁들임 · 다른 작품 자리에서 쉬는 날 수 (11/19 사용자 결정. 그 전엔 한 번 나오면 영영 뺐음).
# 오늘 전 이 날 수 안에 나온 글만 쉼 → 월요일에 지나친 글은 화요일 쉬고 수요일부터 다시 자리 후보. 오늘 나온 건 안 셈 (slot_ready).
# 화면에 나옴 = SEEN_MS 이상 본 노출 (11/25)
REST_DAYS = 1
# '봤다' 로 치는 노출 (11/25 사용자 결정, 부록 A #48): 카드가 80% 이상 보인 시간 (focus_ms) 이 이 ms 이상. focus_ms 가 없는 기록
# (그 전 기록 · 화면이 아직 안 보냄) 은 반 이상 보인 시간 (visible_ms) 으로 같은 기준. 노출 하나씩 봄 (같은 날 합 아님).
# 못 미친 노출은 load_visits 가 버림 → 본 글 감점 (W_SEEN) · 자리 쉬기 (REST_DAYS) · 보고 지나침 (v1 SIGNAL skip) 에 안 들어감.
# 단 열었거나 카드를 넘긴 글은 노출 시간과 상관없이 본 글 (확실히 봤음, 같은 접속에 노출을 하나 넣음 · 보고 지나침 아님).
# 그전엔 반 이상 1초면 남는 노출을 다 '봤다' 로 쳐서 빨리 넘긴 카드까지 다음 날 감점 · 쉬기를 받았음.
# 백엔드 '오늘 본 카드 맨 뒤로' 는 따로 정한 기준 (반 이상 보인 시간 하루 합 2초, recommendation_repository.SHOWN_VISIBLE_MS)
SEEN_MS = 3000

LOGS = frozenset({"impression", "open", "read", "like", "save", "more", "less"})   # v1 이 쓰는 기록 7종 (EXP-22 · 30 조건)

# 여러 작품 글 · 보여 줄 자리 · 이유 (11/16 · 11/17 사용자와 정함, EXPERIMENTS 부록 A #36 ~ #43). 값은 출발값 → 베타 기록으로 다시 정함
W_RELATED = 0.5                         # 글에 같이 연결된 (기준이 아닌) 작품의 무게. 글의 작품 몫은 무게 × 작품 몫 중 가장 큰 값
W_POP_TIE = 0.3                         # 고른 작품 밖 작품 취향: 칩이 거의 같게 맞을 때 인기작 먼저 (work_affinity)
ANCHOR_FIRST = ("like", "studio")       # 1번 연결 작품이 기준인 RAG 글 종류 (팬 추천 · 제작사). 그 밖은 제목이 집은 작품
SIDE_SLOTS = (4, 8)                     # 곁들임 자리 = 하루 최대 2개
DISCOVER_FROM, DISCOVER_EVERY = 6, 4    # 다른 작품 자리 6 · 10 · 14 ...번째
SIDE_CHIP_MIN = 0.5                     # 이유 문구에 칩 이름을 쓸 만큼 그 칩이 작품에 있는 정도 (소재 순위 50 이상)
_OVERLAP_ID = re.compile(r"dict-curation-overlap-(?:t([a-z0-9]+?)[123]|(w\d+))(?:-v\d+)?")

# 온보딩 답 → v1 유형. Q3(덕질 방식) 6개 = consumption_type · 예전(v1) 답의 q1, 그리고 예전 q2(받고 싶은 소식) 3개.
# Q3 를 유형 10개 문장으로 바꾸면 답이 유형 코드로 오므로 표에 없는 코드는 그대로 씀
# 화면 문장은 6개, 내부 유형은 10개 그대로 → 답 하나에 유형 여러 개 (10/30 결정. 비하인드는 유머에 합쳐서 behind 도 같이)
ANSWER_TYPES = {"world": ("guide", "review"), "relationships": ("character", "people"), "ost": ("music",),
                "goods": ("goods",), "events": ("event",), "news": ("news", "curation", "humor", "behind"),
                "releases": ("news",), "characters": ("character",), "restock": ("goods",)}

TYPE_LABELS = {"news": "소식", "event": "행사", "guide": "작품 가이드", "people": "인물", "curation": "큐레이션",
               "goods": "굿즈", "behind": "비하인드", "review": "리뷰", "character": "캐릭터", "music": "음악", "humor": "유머"}

# 카드에 보이는 추천 이유. 점수 조각 중 가장 큰 것으로 고름. {을} · {와} = 작품 이름 받침에 맞는 조사
REASONS = {
    "picked": "‘{work}’{을} 골라서 추천해요",           # 온보딩에서 고른 작품의 글
    "cowatch": "‘{work}’{와} 함께 많이 본 작품이에요",   # 고른 작품을 본 사람이 많이 본 작품의 글
    "taste": "취향에 맞는 작품이에요",                   # 그 밖에 작품 취향이 큰 글
    "type": "관심 있는 {type} 글이에요",                 # 유형 취향이 큰 글
    "fresh": "새로 올라온 글이에요",                     # 새 글 점수가 큰 글
}


Q3_ANSWERS = ("world", "relationships", "ost", "goods", "events", "news")   # 화면 Q3 답 6개 (좋아하는 순서대로 1 ~ 6개)
# Q3 순위별 무게 (1순위부터) = 선형 (7 − 순위) ÷ 6. 1순위 1 = 예전 답 하나일 때와 같은 점수 (EXP-39, 부록 A #45)
TYPE_RANK_W = (1.0, 0.833, 0.667, 0.5, 0.333, 0.167)
GROUP_OF = {t: a for a in Q3_ANSWERS for t in ANSWER_TYPES[a]}   # 글 유형 → Q3 답 묶음 (같은 유형 연속 금지에 씀)
TYPE_RUN = 1    # 같은 Q3 답 묶음 글은 연속 TYPE_RUN 개까지. 1 = 바로 앞 글과 다른 묶음 먼저 (EXP-39, 부록 A #46)


def q3_answers(first, ranked):
    """Q3 답 (좋아하는 순서). first = onboarding_answers.consumption_type (1순위), ranked = 새 표 tap_consumption_type 의
    [(rank, 답), ...] (rank 순). rank 1 줄이 first 와 같을 때만 새 표 순서: 같은 DB 를 쓰는 운영 옛 코드는 취향 수정 때
    first 만 바꿈 → 그러면 [first] 하나. 백엔드 취향 조회 (onboarding_repository.load_consumption_types) 와 같은 규칙"""
    if ranked and ranked[0] == (1, first):
        return [answer for _, answer in ranked]
    return [first] if first else []


def answer_types(answers, extra=()):
    """회원의 유형 무게 = Q3 답 (좋아하는 순서) 의 순위 무게 + extra (예전 v1 답의 q2 '받고 싶은 소식') 는 무게 1.
    q2 는 Q3 와 다른 질문이라 순위가 아님 → 예전 (v1) 회원 점수도 그대로"""
    types = user_types(answers)
    for t in user_types(extra, (1.0,) * len(extra)):
        types[t] = 1.0
    return types


def user_types(answers, rank_w=TYPE_RANK_W):
    """온보딩 답 (좋아하는 순서) → {v1 유형 코드: 순위 무게}. 답 하나가 유형 여러 개일 수 있고, 겹치면 앞 순위 무게.
    rank_w = 순위별 무게 (시뮬레이터가 후보를 넣어 봄)"""
    out = {}
    for a, w in zip(answers, rank_w):
        for t in ANSWER_TYPES.get(a, (a,)):
            if t in v1.TYPE_INDEX and t not in out:
                out[t] = w
    return out


def post_kind(request_id):
    """RAG 글 번호 (agent_run.input_payload.request_id) → (글 종류, 소재 키, 소재 슬러그).
    형식 dict-{유형}-{종류}-{...}. 소재 모음은 dict-curation-overlap-t{슬러그}{1|2|3}[-vN] (옛 형식 w{번호} 는 그 번호가 소재 키,
    슬러그 없음). RAG 가 아닌 글은 (None, None, None)"""
    rid = request_id or ""
    if not rid.startswith("dict-"):
        return None, None, None
    parts = rid.split("-")
    kind = parts[2] if len(parts) > 2 else None
    m = _OVERLAP_ID.fullmatch(rid) if kind == "overlap" else None
    if m:
        return kind, m.group(1) or m.group(2), m.group(1)
    return kind, None, None


def anchor_of(works, names, title, kind):
    """글의 기준 작품 자리 (11/16 결정 1번). 작품이 하나면 그 작품, 팬 추천 · 제작사는 1번,
    그 밖 (가수 · 감독 · 소재 모음 등)은 제목이 이름을 그대로 쓴 작품 (연결 앞 순서 먼저, 두 글자 이상). 없으면 None = 모음"""
    if not works:
        return None
    if len(works) == 1 or kind in ANCHOR_FIRST:
        return 0
    return next((j for j, n in enumerate(names or ()) if n and len(n) >= 2 and n in (title or "")), None)


def chip_slug(name):
    # AniList 이름 → RAG 소재 슬러그 (소문자 · 영숫자만)
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def chip_labels(user, features):
    """유저가 고른 칩 → {AniList 이름: 화면 이름}. features = preferences.load_chip_features 결과"""
    out = {}
    for tag in user.get("preference_tags", ()):
        f = features.get((tag["kind"], tag["code"]))
        if f is not None and tag.get("label"):
            out.setdefault(f.name, tag["label"])
    return out


def _rounds(items, feed):
    # 같은 작품 글은 한 바퀴에 하나씩 (작품마다 가장 높은 글 → 두 번째 글 ...). 한 바퀴 안은 들어온 순서. 작품 없는 글은 글마다 따로
    count, keyed = {}, []
    for n, i in enumerate(items):
        p = int(feed["pos"][i])
        k = p if p >= 0 else ("post", i)
        keyed.append((count.get(k, 0), n, i))
        count[k] = count.get(k, 0) + 1
    return [i for *_, i in sorted(keyed)]


def slot_ready(state, day, rest_days=None):
    """글별로 자리 (맨 위 · 곁들임 · 다른 작품) 후보가 되는지 (11/19 결정). 오늘 전 rest_days 일 안에 화면에 나온 글만 쉼.
    오늘 나온 건 안 셈 → 하루 중간에 다시 계산해도 (취향 저장) 오늘 지나친 글은 자리를 안 잃음. 한 번도 안 나온 글은 늘 후보.
    끝까지 본 글 (REREAD_DAYS) · 그만볼래요는 v1.rank_feed 가 이미 순위에서 뺌"""
    rest_days = REST_DAYS if rest_days is None else rest_days
    last = np.where(state.seen_last >= day, state.seen_prev, state.seen_last)   # 오늘 기록은 빼고 그 전 마지막 날
    return last < day - rest_days


def _candidate_level(same_group, same_work):
    """같은 유형 연속 금지 (compose 6번) 에서 후보 등급. 작은 쪽 먼저: 묶음도 작품도 다름 → 같은 묶음 · 다른 작품
    (같은 작품은 한 바퀴에 하나씩 #30 이 먼저) → 다른 묶음 · 같은 작품 → 둘 다 같음"""
    return (same_work, same_group)


def compose(order, feed, posts, picked, chip_slugs, ctx, seen=None, top_n=300, ready=None, type_run=None):
    """점수 순 글 번호 → 보여 줄 순서 [(글 번호, 자리), ...] (11/16 · 11/17 결정). 자리 = pinned · side · discover · score
    1. 팬 추천은 기준 작품이 고른 작품 (같은 시리즈 포함) 인 글만 남김
    2. 맨 위 = 기준 작품이 고른 작품인 글 중 자리 후보인 글 (같은 작품은 한 바퀴에 하나씩)
    3. SIDE_SLOTS 번째 = 곁들임 (하루 최대 2개): 팬 추천이 아닌 여러 작품 글 중 고른 작품이 기준이 아닌 자리에 있거나
       소재 모음의 소재가 고른 칩인 글. 점수 순, 자리 후보인 글만, 서로 다른 소재
    4. DISCOVER_FROM 부터 DISCOVER_EVERY 마다 = 다른 작품: 기준 작품이 고른 작품이 아닌 자리 후보 글, 점수 순 (한 바퀴에 하나씩)
    5. 그 자리 글이 없으면 맨 위 → 나머지 점수 순으로 채움. 같은 소재 모음은 하루 하나 (11/17 RAG 요청: 미루지 않고 뺌)
    6. 같은 유형 연속 금지 (EXP-39): 앞 type_run (기본 TYPE_RUN, 0 = 끔) 개 글이 같은 Q3 답 묶음이면 그 자리 후보 중에서
       _candidate_level 이 가장 낮은 가장 앞 글 (묶음도 작품도 다름 → 같은 묶음 · 다른 작품 → 다른 묶음 · 같은 작품 → 그대로).
       다른 자리 후보로 넘어가지는 않음. 같은 소재 모음은 6번 없이 정했을 때 남는 글만 남김 → 보이는 소재 모음은 같고 자리만 바뀜
    chip_slugs: 고른 칩의 소재 슬러그 집합 (chip_slug). ready: 글별 자리 후보 여부 (slot_ready, 11/19).
    ready 가 없으면 seen (글별 노출 횟수) 이 0 인 글만 후보 (11/18 까지 규칙: 한 번 나오면 영영 자리에서 빠짐).
    top_n: 하루 순위 개수 (daily_recommend.TOP_N 과 같게, 11/25 100 → 300). 후보가 모자라면 짧게 끝남"""
    type_run = TYPE_RUN if type_run is None else type_run
    keep_theme = None                                                 # 소재 → 남길 글 (6번 없이 정한 순서에서 먼저 나온 글)
    if type_run:
        base = compose(order, feed, posts, picked, chip_slugs, ctx, seen, top_n, ready, type_run=0)
        keep_theme = {posts[i]["theme"]: i for i, _ in reversed(base) if posts[i].get("theme")}
    mine = {_cw_pos(ctx, w) for w in picked} - {-1}
    if ready is not None:
        free = lambda i: bool(ready[i])
    else:
        free = lambda i: seen is None or not seen[i]
    at = lambda i: int(feed["pos"][i])
    order = [i for i in order if posts[i].get("kind") != "like" or at(i) in mine]
    pinned = [i for i in order if at(i) in mine and free(i)]
    pinned_set = set(pinned)

    def is_side(i):
        p = posts[i]
        works = p.get("works") or ()
        if p.get("kind") == "like" or len(works) < 2 or at(i) in mine:
            return False
        a = p.get("anchor")
        return (any(j != a and _cw_pos(ctx, w) in mine for j, w in enumerate(works))
                or bool(p.get("slug") and p["slug"] in chip_slugs))

    side, side_themes = [], set()
    for i in order:
        if len(side) == len(SIDE_SLOTS):
            break
        key = posts[i].get("theme")
        if i in pinned_set or not free(i) or not is_side(i) or (key and key in side_themes):
            continue
        side.append(i)
        if key:
            side_themes.add(key)
    side_set = set(side)
    rest = _rounds([i for i in order if i not in pinned_set and i not in side_set], feed)
    queues = {"pinned": deque(_rounds(pinned, feed)), "side": deque(side),
              "discover": deque(i for i in rest if free(i) and at(i) >= 0 and at(i) not in mine), "score": deque(rest)}
    out, used, themes = [], set(), set()
    group = lambda i: GROUP_OF.get(posts[i]["type"], posts[i]["type"])

    def take(name):
        # 이 자리 후보 중 가장 앞 글. 단 앞 type_run 개가 같은 묶음이면 (6번) _candidate_level 이 가장 낮은 가장 앞 글
        q = queues[name]
        avoid = None
        if type_run and len(out) >= type_run:
            recent = {group(i) for i, _ in out[-type_run:]}
            avoid = next(iter(recent)) if len(recent) == 1 else None
        last_work = at(out[-1][0]) if out else -1
        best = None
        for k, i in enumerate(q):
            key = posts[i].get("theme")
            if i in used or (key and (key in themes or (keep_theme is not None and keep_theme.get(key) != i))):
                continue
            level = (0, 0) if avoid is None else _candidate_level(group(i) == avoid, 0 <= at(i) == last_work)
            if best is None or level < best[0]:
                best = (level, k)
                if level == (0, 0):
                    break
        if best is None:                                              # 남은 글이 모두 이미 나왔거나 같은 소재 모음
            q.clear()
            return None
        i = q[best[1]]
        del q[best[1]]
        used.add(i)
        if posts[i].get("theme"):
            themes.add(posts[i]["theme"])
        return i

    while len(out) < top_n:
        n = len(out) + 1
        first = ("side" if n in SIDE_SLOTS else
                 "discover" if n >= DISCOVER_FROM and (n - DISCOVER_FROM) % DISCOVER_EVERY == 0 else None)
        for name in ([first] if first else []) + ["pinned", "score"]:
            i = take(name)
            if i is not None:
                out.append((i, name))
                break
        else:
            break
    return out


def arrange(order, feed, picked, ctx, seen=None):
    """점수 순 글 번호 → 보여 줄 순서 (10/31 결정). Q1 에서 고른 작품 (같은 시리즈 포함)의 글 중 아직 안 본 글을 맨 위로.
    seen: 글별 노출 횟수 → 한 번이라도 보고 안 연 글은 다음 계산부터 맨 위 고정에서 빠지고 점수 순서로 (연 글은 순위에서 빠짐).
    맨 위 글 · 나머지 글 각각 안에서 같은 작품 글은 한 바퀴에 하나씩 (작품마다 가장 높은 글 → 두 번째 글 ...).
    한 바퀴 안은 점수 순서 그대로. 작품 없는 글은 글마다 따로 침"""
    mine = {_cw_pos(ctx, w) for w in picked} - {-1}
    pinned = lambda i: int(feed["pos"][i]) in mine and (seen is None or not seen[i])

    def rounds(items):
        count, keyed = {}, []
        for n, i in enumerate(items):
            p = int(feed["pos"][i])
            k = p if p >= 0 else ("post", i)
            keyed.append((count.get(k, 0), n, i))
            count[k] = count.get(k, 0) + 1
        return [i for *_, i in sorted(keyed)]

    return rounds([i for i in order if pinned(i)]) + rounds([i for i in order if not pinned(i)])


def score_parts(state, feed, i, day, cfg):
    """v1.rank_feed 와 같은 계산으로 글 i 의 점수 조각 (작품 취향 (장르 · 소재 유사도 · 같이 연결된 작품 포함), 유형 취향, 새 글,
    이미 본 만큼 감점). 합 = 순위 점수"""
    ty = int(feed["type"][i])
    work = float(v1.work_parts(state, feed, np.array([i]), cfg)[0])
    shown = state.seen if "impression" in cfg.logs else state.served
    return (work, cfg.w_type * float(v1.type_pref(state, cfg)[ty]),
            cfg.w_fresh * math.exp(-(day - int(feed["day"][i])) / float(v1.TAU[ty])), -cfg.w_seen * float(shown[i]))


def _josa(word, pair):
    # 받침에 맞는 조사 (pair = 받침 있을 때, 없을 때). 한글로 안 끝나면 둘 다 적음
    last = [c for c in word if c.isalnum()][-1:]
    if last and "가" <= last[0] <= "힣":
        return pair[0] if (ord(last[0]) - 0xAC00) % 28 else pair[1]
    return f"{pair[0]}({pair[1]})"


def _cw_pos(ctx, w):
    # me;nu 작품 번호 → 같이 본 횟수 표 위치 (시리즈 대표 작품 기준, 없으면 -1)
    return int(ctx["cw_pos"][ctx["row"][ctx["rep_of"][w]]]) if w in ctx["rep_of"] else -1


def reason(parts, work, picked, type_code, ctx):
    """점수 조각 → 추천 이유 (text = 카드 문구). work: 글의 대표 작품 번호(없으면 None), picked: 온보딩에서 고른 작품 번호들.
    작품 취향이 가장 크면: 고른 작품 (같은 시리즈 포함) → picked, 고른 작품과 같이 본 기록이 있으면 그중 가장 가까운 작품 → cowatch, 아니면 taste"""
    kind, about = ("work", "type", "fresh")[int(np.argmax(parts[:3]))], None
    mine = None if work is None else next((m for m in picked if m == work or 0 <= _cw_pos(ctx, m) == _cw_pos(ctx, work)), None)
    if kind == "work" and work is None:
        kind = "fresh"
    elif kind == "work" and mine is not None:
        kind, about = "picked", mine
    elif kind == "work":
        sim, q = ctx["cw_sim"][ar.COWATCH_KIND], _cw_pos(ctx, work)         # v0.5 가 같이 본 작품에 쓰는 유사도
        near = [(float(sim[p, q]), m) for m in picked for p in [_cw_pos(ctx, m)] if p >= 0 and q >= 0]
        top, m = max(near, default=(0.0, None))
        kind, about = ("cowatch", m) if top > 0 else ("taste", None)
    w = ctx["works"].get(about) or {}
    name = w.get("title_ko") or w.get("title") or ""
    if about is not None and not name:
        kind, about = "taste", None
    text = REASONS[kind].format(work=name, 을=_josa(name, ("을", "를")), 와=_josa(name, ("과", "와")),
                                type=TYPE_LABELS.get(type_code, type_code))
    return {"text": text, "kind": kind, "work": about,
            "parts": {k: round(x, 4) for k, x in zip(("work", "type", "fresh", "seen"), parts)}}


# 11/17 결정 6번: 이유 = "왜 이 작품" + "어떤 글". 점수에 실제로 쓴 것만 말함 (같이 본 기록은 점수에서 빠져서 안 씀)
TYPE_STORY = {"character": "캐릭터 이야기", "music": "노래 이야기", "guide": "작품 가이드", "people": "인물 이야기",
              "news": "새 소식", "event": "행사 소식", "goods": "굿즈 소식", "review": "리뷰", "humor": "유머 글",
              "behind": "비하인드 이야기"}


def _ieyo(word):
    return _josa(word, ("이에요", "예요"))


def _title(ctx, w, fallback=""):
    # 작품 이름: 목록의 한글 제목 → 글에 연결된 이름 (모델 파일에 없는 새 작품) → 원어 제목
    info = ctx["works"].get(w) or {}
    return info.get("title_ko") or fallback or info.get("title") or ""


def _chip_fit(state, q):
    return float(state.chip_w[q].mean()) if state.chip_w is not None and q >= 0 else 0.0


def _matched_chips(state, q, labels, k=2):
    # 카탈로그 자리 q 작품에 SIDE_CHIP_MIN 이상 있는 고른 칩의 화면 이름, 진한 순 k 개
    if state.chip_w is None or q < 0:
        return []
    w = state.chip_w[q]
    names = [state.chip_names[n] for n in sorted(range(len(w)), key=lambda n: -float(w[n])) if w[n] >= SIDE_CHIP_MIN]
    return [labels[n] for n in names if n in labels][:k]


def _closest_picked(ctx, w, picked):
    # 고른 작품 중 작품 특징 (장르 · 태그 · 제작사 · 제작진 · 성우) 이 가장 닮은 작품 (코사인 > 0), 없으면 None
    if w not in ctx["rep_of"]:
        return None
    X, vn, row = ctx["X"], ctx["vnorm"], ctx["row"]
    r = row[ctx["rep_of"][w]]
    best, out = 0.0, None
    for m in picked:
        if m in ctx["rep_of"] and m != w:
            s = row[ctx["rep_of"][m]]
            norm = float(vn[r] * vn[s])
            c = float(X[r] @ X[s]) / norm if norm > 0 else 0.0
            if c > best:
                best, out = c, m
    return out


def _picked_text(name, post):
    # 고른 작품 글: 맨 위 글이 다 "골라서 추천해요" 로 같아서 글 종류별 문구 (11/17)
    kind, ty = post.get("kind"), post["type"]
    if kind == "like":
        return f"‘{name}’ 팬들이 추천한 다음 작품이에요"
    if kind in ("bdaymonth", "birthday"):
        return f"고른 ‘{name}’ 캐릭터의 생일 글이에요"
    if kind == "origin":
        return f"고른 ‘{name}’의 원작 이야기예요"
    if ty == "curation":
        return f"고른 ‘{name}’ 관련 모음이에요"
    story = TYPE_STORY.get(ty)
    if story:
        return f"고른 ‘{name}’의 {story}{_ieyo(story)}"
    return REASONS["picked"].format(work=name, 을=_josa(name, ("을", "를")))


def explain(parts, post, slot, picked, state, ctx, labels):
    """점수 조각 · 글 · 자리 → 추천 이유 (text = 카드 문구). labels = chip_labels 결과 {AniList 이름: 화면 이름}.
    1. 기준 작품이 고른 작품 → 글 종류별 문구 ("고른 ‘원피스’의 캐릭터 이야기예요" · "‘원피스’ 팬들이 추천한 다음 작품이에요")
    2. 고른 작품이 같이 연결된 여러 작품 글 → "‘원피스’도 나오는 글이에요"
    3. 곁들임 자리 · 기준 없는 모음의 소재가 고른 칩 → "고른 ‘고어’ 소재 모음이에요"
    4. 그 밖: 점수 조각 중 가장 큰 것 + 작품에 진하게 있는 고른 칩 → "고른 ‘호러’ · ‘고어’ 취향에 맞는 작품이에요" ·
       "고른 ‘힐링’ 작품의 노래 이야기예요" · "새로 올라온 ‘호러’ 작품 글이에요". 칩이 없으면 가장 닮은 고른 작품 · 유형 · 새 글
    자리 (slot) 도 남겨 베타 뒤 자리별 반응을 비교한다"""
    works = post.get("works")
    if works is None:
        works = [post["menu_work_id"]] if post.get("menu_work_id") is not None else []
        a = 0 if works else None
    else:
        a = post.get("anchor")
    names = post.get("names") or ()
    name_at = lambda j: names[j] if j is not None and j < len(names) else ""
    pos = [_cw_pos(ctx, w) for w in works]
    mine = {}
    for m in picked:
        q = _cw_pos(ctx, m)
        if q >= 0:
            mine.setdefault(q, m)
    kind = about = text = None
    if a is not None and pos[a] in mine:
        about = mine[pos[a]]
        kind, text = "picked", _picked_text(_title(ctx, about, name_at(a)), post)
    elif post.get("kind") != "like" and len(works) > 1:
        hits = [j for j, q in enumerate(pos) if j != a and q in mine]     # 같이 나오는 고른 작품 (제목에 이름이 있는 것 먼저)
        j = next((j for j in hits if name_at(j) and name_at(j) in (post.get("title") or "")), hits[0] if hits else None)
        theme = {chip_slug(n): lab for n, lab in labels.items()}.get(post.get("slug") or "")
        if j is not None:
            about = mine[pos[j]]
            kind, text = "with_picked", f"‘{_title(ctx, about, name_at(j))}’도 나오는 글이에요"
        elif theme and (slot == "side" or a is None):
            kind, text = "theme", f"고른 ‘{theme}’ 소재 모음이에요"
    if text is None:
        j = a if a is not None else (max(range(len(pos)), key=lambda n: _chip_fit(state, pos[n])) if pos else None)
        q = pos[j] if j is not None else -1
        chips = _matched_chips(state, q, labels)
        big = ("work", "type", "fresh")[int(np.argmax(parts[:3]))]
        if big == "work" and not works:
            big = "fresh"
        if big == "work" and chips:
            kind, text = "chips", "고른 " + " · ".join(f"‘{c}’" for c in chips) + " 취향에 맞는 작품이에요"
        elif big == "work":
            m = _closest_picked(ctx, works[j], picked)
            if m is not None:
                about, nm = m, _title(ctx, m)
                kind, text = "similar", f"‘{nm}’{_josa(nm, ('과', '와'))} 분위기가 닮은 작품이에요"
            else:
                kind, text = "taste", REASONS["taste"]
        elif big == "type" and chips:
            story = TYPE_STORY.get(post["type"])
            kind = "chips_type"
            text = (f"고른 ‘{chips[0]}’ 작품의 {story}{_ieyo(story)}" if story
                    else f"고른 ‘{chips[0]}’ 작품이 나오는 {TYPE_LABELS.get(post['type'], post['type'])} 글이에요")
        elif big == "type":
            kind, text = "type", REASONS["type"].format(type=TYPE_LABELS.get(post["type"], post["type"]))
        elif chips:
            kind, text = "chips_fresh", f"새로 올라온 ‘{chips[0]}’ 작품 글이에요"
        else:
            kind, text = "fresh", REASONS["fresh"]
        if about is None and j is not None:
            about = works[j]
    return {"text": text, "kind": kind, "work": about, "slot": slot,
            "parts": {k: round(x, 4) for k, x in zip(("work", "type", "fresh", "seen"), parts)}}
