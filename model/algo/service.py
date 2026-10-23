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
# - 같이 본 작품 · EASE: MAL 시청 기록을 AniList 번호로 바꿔서 센다. MAL 표본은 2022-03 에 모아서
#   2022년 이후 작품은 기록이 없거나 일부뿐 → 기록을 지우고 비슷한 작품에서 빌려 온다 (EXP-23 · 28과 같은 방식)
# - 커뮤니티 추천: AniList recommendations (links.jsonl)

import csv
import json
import pathlib
from datetime import timedelta

import numpy as np

from model.algo import anime_ranker as ar
from model.algo import content_v1 as v1

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
ANILIST = DATA / "anilist"
ENTRIES = DATA / "menu_work_entries.csv"
WORKS = DATA / "menu_works.csv"
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
def build():
    """서비스 문맥 = 기본 문맥 + MAL 기록으로 센 같이 본 작품 (2022년 이후 · MAL 번호 없는 작품은 기록을 지우고 빌려 옴)"""
    ctx = base()
    entries = ctx["entries"]
    no_record = {e["anilist_id"] for e in entries if e["year"] >= NO_COWATCH_FROM or not e["mal_id"]}
    add_cowatch(ctx, mal_histories(entries), no_record)
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


def add_cowatch(ctx, histories, no_record=None, lam=None):
    """같이 본 작품 · EASE · 빌려 오기를 붙인다 (목록 전체 작품). no_record: 기록을 지우고 빌려 올 작품.
    안 주면 기록이 하나도 없는 작품. ctx["no_record"] 에 남김. lam: EASE 규제 세기 (기본 anime_ranker.EASE_LAMBDA)"""
    ar.build_cowatch(ctx, histories, n_items=len(ctx["ids"]))
    C = ctx["cw_count"]
    if no_record is None:
        no_record = set(ctx["ids"][ctx["cw_rows"][np.diag(C) == 0]].tolist())
    pos = ctx["cw_pos"][ar.rows_of(ctx, sorted(no_record))]
    C[pos, :] = 0                                                # 일부만 있는 기록은 지운다 (new_anime_test.masked 와 같음)
    C[:, pos] = 0
    ctx["cw_sim"] = ar.cowatch_sims(C, ctx["cw_users"])
    ar.build_ease(ctx, ar.EASE_LAMBDA if lam is None else lam)
    ar.build_cf_borrow(ctx, no_record)
    ctx["no_record"] = no_record


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
    작품이 없거나 목록 밖인 글은 pos = -1 (작품 취향 0)"""
    day = lambda x: (x.toordinal() - 719163)                     # date → 1970-01-01 부터 날 수 (anime_ranker.DAY 와 같은 기준)
    pos = [int(ctx["cw_pos"][ctx["row"][ctx["rep_of"][p["menu_work_id"]]]]) if p["menu_work_id"] in ctx["rep_of"] else -1
           for p in posts]
    return {"pos": np.array(pos, dtype=np.int64),
            "type": np.array([v1.TYPE_INDEX[p["type"]] for p in posts], dtype=np.int64),
            "day": np.array([day(p["published"]) for p in posts], dtype=np.int64),
            "until": np.array([day(p["until"]) for p in posts], dtype=np.int64)}


def work_affinity(state, ctx, cfg):
    """v1 작품 취향을 시리즈 단위로: 글은 시리즈 대표 자리를 보므로, 대표 자리에 그 시리즈 작품 중 가장 높은 취향을 둔다"""
    v1.refresh_work_affinity(state, ctx, cfg)
    aff, pos, row = state.work_aff, ctx["cw_pos"], ctx["row"]
    for s, items in ctx["series_items"].items():
        if len(items) > 1:
            p = pos[[row[a] for a in items]]
            aff[pos[row[s]]] = aff[p[p >= 0]].max()
