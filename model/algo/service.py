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
from datetime import timedelta

import numpy as np

from model.algo import anime_ranker as ar
from model.algo import content_v1 as v1

MODEL = pathlib.Path(__file__).resolve().parents[1]
DATA = MODEL / "data"
ANILIST = DATA / "anilist"
ENTRIES = MODEL / "catalog" / "menu_work_entries.csv"   # me;nu 작품 목록 (레포에 있음. 백엔드 seeds/menu_works.py 와 같은 파일)
WORKS = MODEL / "catalog" / "menu_works.csv"
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


def genre_affinity(state, ctx):
    """작품별 장르 · 소재 유사도 = v0.5 태그 취향과 같은 계산 (고른 작품의 장르 · 태그 + 칩, 코사인 0~1). 글은 시리즈 대표 자리를 봄"""
    user = {"history": state.history, "t": state.t, "genres": state.genres, "themes": state.themes}
    state.genre_sim = ar.genre_scores(user, ctx)[ctx["cw_rows"]].astype(np.float32)


# ## 5. 서비스 연결 (매일 추천 계산 = scripts/daily_recommend.py)
# 유저 테스트 동안 v1 비중은 고정. 서비스에 맞춰 바꾸는 건 아래 입력 대응표 · 장르 · 소재 가산 (W_GENRE) · 보여 줄 순서 규칙 (arrange) ·
# 추천 이유 문구뿐 (W_GENRE · arrange 는 10/31 사용자 결정)

# 글 점수의 작품 몫에 더하는 장르 · 소재 유사도 비중. 로컬 시험 계정 4명으로 고른 출발값 (1.0 부터 고른 유형 글이 줄어듦) → 테스트 뒤 다시 정함
W_GENRE = 0.5

LOGS = frozenset({"impression", "open", "read", "like", "save", "more", "less"})   # v1 이 쓰는 기록 7종 (EXP-22 · 30 조건)

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


def user_types(answers):
    """온보딩 답 목록 → v1 유형 코드 목록 (순서 유지, 겹치면 하나). 답 하나가 유형 여러 개일 수 있음"""
    out = []
    for a in answers:
        for t in ANSWER_TYPES.get(a, (a,)):
            if t in v1.TYPE_INDEX and t not in out:
                out.append(t)
    return out


def arrange(order, feed, picked, ctx):
    """점수 순 글 번호 → 보여 줄 순서 (10/31 결정). Q1 에서 고른 작품 (같은 시리즈 포함)의 글을 맨 위로.
    고른 작품 글 · 나머지 글 각각 안에서 같은 작품 글은 한 바퀴에 하나씩 (작품마다 가장 높은 글 → 두 번째 글 ...).
    한 바퀴 안은 점수 순서 그대로. 작품 없는 글은 글마다 따로 침"""
    mine = {_cw_pos(ctx, w) for w in picked} - {-1}

    def rounds(items):
        count, keyed = {}, []
        for n, i in enumerate(items):
            p = int(feed["pos"][i])
            k = p if p >= 0 else ("post", i)
            keyed.append((count.get(k, 0), n, i))
            count[k] = count.get(k, 0) + 1
        return [i for *_, i in sorted(keyed)]

    return (rounds([i for i in order if int(feed["pos"][i]) in mine])
            + rounds([i for i in order if int(feed["pos"][i]) not in mine]))


def score_parts(state, feed, i, day, cfg):
    """v1.rank_feed 와 같은 계산으로 글 i 의 점수 조각 (작품 취향 (장르 · 소재 유사도 포함), 유형 취향, 새 글, 이미 본 만큼 감점). 합 = 순위 점수"""
    p, ty = int(feed["pos"][i]), int(feed["type"][i])
    work = cfg.w_work * (float(state.work_aff[p]) + cfg.work_feedback * math.tanh(float(state.work_ev[p]))) if p >= 0 else 0.0
    if p >= 0 and cfg.w_genre and state.genre_sim is not None:
        work += cfg.w_genre * float(state.genre_sim[p])
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
