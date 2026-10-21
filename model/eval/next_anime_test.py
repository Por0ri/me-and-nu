# next_anime_test — MAL 시청 기록으로 "다음에 볼 작품 맞히기" 점수를 매긴다 (M3 채점기)
#
# 유저마다 마지막에 다 본 작품 하나를 가리고, 그 전 기록만 보고 추천한 상위 10개 안에
# 가린 작품이 있는지 본다. 모델을 고칠 때마다 이 점수로 나아졌는지 확인한다.
#
# 정답은 "다 본(completed)" 작품만 쓴다 (EXP-08). "보는 중" 작품은 매주 진도를 고치는 방영작(원피스 등)이
# 정답으로 몰려서 시험이 "지금 챙겨 보는 방영작 맞히기"로 치우쳤기 때문.
# 정답 기간은 2020-01 ~ 2022-01 (EXP-12). 데이터 수집 직전(2022-02~03) 정답은 한 작품으로 몰려서 뺀다.
#
# 메인 점수는 시리즈 단위다. 정답이 "원피스 극장판 Z"일 때 "원피스"를 추천해도 맞힌 걸로 친다.
# me;nu 는 작품 글을 보여주는 서비스라 같은 시리즈면 같은 관심사로 보기 때문. 작품 단위 점수도 같이 낸다.
#
# 작품 취향만 잰다. 콘텐츠 유형(소식·가이드 등) 취향은 MAL 에 없어서 못 잰다 → M3-2 시뮬레이터 몫.
# 추천 방법(모델)은 model/algo/anime_ranker.py 에 있다. 여기는 데이터 읽기·정답 가리기·채점만 한다.
#
#   python model/scripts/export_mal_sample.py 50000   # 먼저 표본 뽑기 (한 번만)
#   python -m model.eval.next_anime_test              # 시험 보기
#   python -m model.eval.next_anime_test 3000         # 유저 3000명만 (빠르게 확인)
# 다른 표본 폴더를 쓰려면 환경 변수 MAL_SAMPLE_DIR 에 경로를 넣는다.

import csv
import json
import math
import os
import pathlib
import sys
from array import array
from collections import Counter
from datetime import datetime

import numpy as np

from model.algo import anime_ranker
from model.algo.series import SEASON_RELATIONS, build_series
from model.algo.anime_ranker import (FEATURES, N_CANDIDATES, ONE_PER_SERIES, STATUS, WATCHED, History,
                                    genre, next_auto, next_cowatch, next_limited, pick_top, popular,
                                    to_seconds)

ROOT = pathlib.Path(__file__).resolve().parents[1] / "data"
DATA = pathlib.Path(os.environ.get("MAL_SAMPLE_DIR", ROOT / "mal_sample"))
ANILIST = ROOT / "anilist" / "media.jsonl"  # model/scripts/fetch_anilist.py 로 받음
DETAIL = ROOT / "anilist" / "detail.jsonl"   # 스튜디오·제작진·성우 (fetch_anilist.py detail). 없으면 사람 정보 없이

TARGET_STATUS = ("completed",)  # 정답으로 쓸 상태 (EXP-08 전까지는 completed + watching)
TARGET_AFTER = to_seconds(datetime(2020, 1, 1))  # 정답 시각이 이보다 오래되면 날짜가 부정확해서 뺀다
# 데이터 수집(2022-03) 직전 정답은 뺀다 (EXP-12). 직전에 끝난 대작(귀멸의 칼날 유곽편)으로 정답이 몰려서
# "최근 인기"가 실제보다 강하게 나왔음
TARGET_BEFORE = to_seconds(datetime(2022, 2, 1))
MIN_HISTORY = 5                 # 가리고 남은 기록이 이보다 적으면 뺀다
TUNE_USERS = 5000               # 앞쪽 유저는 설정값 고르기용(연습), 나머지는 채점용(실전)

MODELS = {"인기순": popular, "취향만": genre, "다음 편 + 취향": next_limited,
          "다음 편 + 같이 본 작품": next_cowatch, "v0.5 (조합 자동)": next_auto}


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
    # MAL id → AniList 장르·태그 (+ 상세 정보를 받아 뒀으면 스튜디오·핵심 제작진·주연 성우). 받은 파일이 없으면 빈 dict
    out, mal_of = {}, {}
    if not ANILIST.exists():
        return out
    with open(ANILIST, encoding="utf-8") as f:
        for line in f:
            for m in json.loads(line)["media"]:
                if m["idMal"]:
                    mal_of[m["id"]] = m["idMal"]
                    tags = [t for t in m["tags"] if not t["isAdult"]]
                    out[m["idMal"]] = {
                        "genres": set(m["genres"]),
                        "tags": {t["name"]: t["rank"] / 100 for t in tags},
                        "cats": {t["name"]: t["category"] for t in tags},  # 태그 → 분류 (예: Theme-Drama)
                        "adult": m["isAdult"],
                    }
    if DETAIL.exists():
        with open(DETAIL, encoding="utf-8") as f:
            for line in f:
                for d in json.loads(line)["media"]:
                    if mal_of.get(d["id"]) in out:
                        out[mal_of[d["id"]]].update(anime_ranker.people_of(d))
    return out


def load_series(anime):
    # MAL 작품 관계에서 속편·전편만 골라 시리즈를 묶는다 (규칙은 model/algo/series.py)
    with open(DATA / "anime_anime.csv", encoding="utf-8") as f:
        pairs = [(int(r["anime_a"]), int(r["anime_b"])) for r in csv.DictReader(f)
                 if r["related"] == "1" and r["relation_type"] in SEASON_RELATIONS]
    return build_series({a: v["title"] for a, v in anime.items()}, pairs)


def load_interactions():
    """시청 기록을 칸별 배열로 읽는다. 처음 한 번 CSV 를 읽고 interactions.npz 로 저장해 두면 다음부터 빠르다.
    칸: user(유저 번호) · anime · status(상태 번호) · score · fav · t(시각, 초). user_ids[유저 번호] = 유저 id"""
    src, cache = DATA / "interactions.csv", DATA / "interactions.npz"
    if cache.exists() and cache.stat().st_mtime >= src.stat().st_mtime:
        with np.load(cache) as z:
            return {k: z[k] for k in z.files}
    users = {}
    user, anime, status, score, fav = array("i"), array("q"), array("b"), array("b"), array("b")
    times, buf = [], []
    with open(src, encoding="utf-8", newline="") as f:
        rows = csv.reader(f)
        next(rows)
        for uid, aid, st, sc, fv, t in rows:
            user.append(users.setdefault(uid, len(users)))
            anime.append(int(aid))
            status.append(STATUS.get(st, 9))
            score.append(int(sc or 0))
            fav.append(int(fv or 0))
            buf.append(t)
            if len(buf) == 1_000_000:  # 시각은 100만 줄씩 모아서 한 번에 바꾼다
                times.append(np.array(buf, dtype="datetime64[s]").astype(np.int64))
                buf = []
    if buf:
        times.append(np.array(buf, dtype="datetime64[s]").astype(np.int64))
    data = {"user_ids": np.array(list(users)), "user": np.array(user, dtype=np.int32),
            "anime": np.array(anime, dtype=np.int64), "status": np.array(status, dtype=np.int8),
            "score": np.array(score, dtype=np.int8), "fav": np.array(fav, dtype=np.int8),
            "t": np.concatenate(times)}
    np.savez(cache, **data)
    return data


# ## 2. 마지막 작품 가리기
# 마지막으로 다 본 작품이 딱 하나일 때만 쓴다 (같은 시각에 여러 개면 무엇이 마지막인지 모름).
# 가린 작품보다 나중 기록은 "미래"라서 버린다.
def make_cases(data, target_status=TARGET_STATUS):
    """유저별로 정답을 가린 문제를 만든다. 유저 id 순서로 정렬해서 돌려준다"""
    codes = [STATUS[s] for s in target_status]
    u = data["user"]
    starts = np.r_[0, np.flatnonzero(np.diff(u)) + 1]
    ends = np.r_[starts[1:], len(u)]
    names = data["user_ids"][u[starts]]
    cases = []
    for k in np.argsort(names, kind="stable"):
        a, b = starts[k], ends[k]
        t, st, an = data["t"][a:b], data["status"][a:b], data["anime"][a:b]
        cand = np.isin(st, codes)
        if not cand.any():
            continue
        t_last = t[cand].max()
        last = np.flatnonzero(cand & (t == t_last))
        if len(last) != 1 or not TARGET_AFTER <= t_last < TARGET_BEFORE:
            continue
        h = t < t_last
        if h.sum() < MIN_HISTORY:
            continue
        history = History(t[h], an[h], st[h], data["score"][a:b][h], data["fav"][a:b][h])
        cases.append({"user_id": str(names[k]), "history": history, "target": int(an[last[0]]), "t": int(t_last)})
    return cases


# 짧은 기록 시험 (EXP-08): me;nu 신규 유저는 온보딩에서 좋아하는 작품 몇 개만 고른다.
# 그 상황을 흉내 내려고, 본 작품 중 점수를 높게 준 n개만 남긴다 (같으면 최애 → 최근 순). 하차·보류는 뺀다.
def shorten(case, n):
    h = case["history"]
    w = np.flatnonzero(np.isin(h.status, WATCHED))
    keys = (w, -h.t[w], -h.fav[w].astype(np.int64), -h.score[w].astype(np.int64))
    pick = w[np.lexsort(keys)][:n]
    pick = pick[np.lexsort((h.status[pick], h.anime[pick], h.t[pick]))]  # 다시 시각 순 (같으면 작품 id 순)
    return {**case, "history": History(*(col[pick] for col in h))}


# 온보딩 흉내: 작품 n개 + 장르 칩 n_genres개 + 테마 칩 n_themes개 (그 유저가 본 작품에 가장 많이 나온 것)
# 장르 칩은 온보딩에 실제로 보일 AniList 장르만 쓴다 (성인·선정 장르 제외)
# anilist 를 안 주면 MAL 장르로 센다 (MAL 이름 기준. Thriller 는 MAL 의 Suspense)
CHIP_GENRES = {"Action", "Adventure", "Comedy", "Drama", "Fantasy", "Horror", "Mecha", "Music", "Mystery",
               "Psychological", "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Suspense"}
ANILIST_CHIP_GENRES = {"Action", "Adventure", "Comedy", "Drama", "Fantasy", "Horror", "Mahou Shoujo", "Mecha",
                       "Music", "Mystery", "Psychological", "Romance", "Sci-Fi", "Slice of Life", "Sports",
                       "Supernatural", "Thriller"}
# 테마 칩 후보 (시험용 임시안. 실제 칩은 기획과 함께 확정)
THEME_CHIPS = {"Isekai", "Magic", "Super Power", "Swordplay", "Revenge", "Tragedy", "Gore", "Post-Apocalyptic",
               "Survival", "School", "Time Manipulation", "Coming of Age", "Iyashikei", "War", "Crime",
               "Virtual World"}


def onboarding(case, anime, n=1, n_genres=3, anilist=None, n_themes=0):
    h = case["history"]
    watched = h.anime[np.isin(h.status, WATCHED)].tolist()
    top = lambda c, k: [x for x, _ in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]
    if anilist is None:
        genres = Counter(g for a in watched for g in anime.get(a, {}).get("genres", ()) if g in CHIP_GENRES)
    else:
        genres = Counter(g for a in watched for g in anilist.get(a, {}).get("genres", ()) if g in ANILIST_CHIP_GENRES)
    themes = Counter()
    for a in watched:  # 테마는 관련도 % 만큼 센다
        for tag, w in (anilist or {}).get(a, {}).get("tags", {}).items():
            if tag in THEME_CHIPS:
                themes[tag] += w
    return {**shorten(case, n), "genres": top(genres, n_genres), "themes": top(themes, n_themes)}


def stats_histories(data, cases):
    """인기도·같이 본 작품을 셀 기록 (EXP-14). 시험 유저는 정답 전 기록만, 나머지 유저는 TARGET_BEFORE 전 기록 전부.
    시험 유저만 세면 정답 기간을 좁힐 때 세는 사람도 같이 줄어서 통계가 약해진다"""
    in_test = {c["user_id"]: c["history"] for c in cases}
    u = data["user"]
    starts = np.r_[0, np.flatnonzero(np.diff(u)) + 1]
    ends = np.r_[starts[1:], len(u)]
    out = []
    for a, b in zip(starts, ends):
        uid = str(data["user_ids"][u[a]])
        if uid in in_test:
            out.append(in_test[uid])
            continue
        m = data["t"][a:b] < TARGET_BEFORE
        if m.any():
            out.append(History(data["t"][a:b][m], data["anime"][a:b][m], data["status"][a:b][m],
                               data["score"][a:b][m], data["fav"][a:b][m]))
    return out


def build_context(cases, anime, series, anilist=None, features=FEATURES, data=None, stats=None):
    # 인기도는 가리고 남은 기록만으로 센다 (가린 작품을 미리 보면 반칙)
    # stats 를 주면 그 기록으로 센다 (stats_histories). 안 주면 시험 유저 기록만
    # data 를 주면 "최근 인기"(정답 직전 30일 동안 많이 본 작품)도 계산할 수 있게 한다
    pop = Counter()
    for h in (stats if stats is not None else [c["history"] for c in cases]):
        pop.update(set(h.anime.tolist()))
    events = (data["anime"], data["t"]) if data is not None else None
    return anime_ranker.build_context(anime, series, pop, anilist, features, events)


# ## 3. 채점
# Hit@10: 정답이 상위 10개 안에 있으면 1
# NDCG@10: 위에 있을수록 점수가 크다 (1위 1.0, 2위 0.63, 10위 0.29, 밖이면 0)
# 시리즈 단위는 정답과 같은 시리즈 작품이 처음 나온 순위로 매긴다
# 정답 종류별로도 나눈다: 보던 시리즈(이미 본 시리즈의 다른 작품) / 새 시리즈
def evaluate(cases, ctx, model, one_per_series=ONE_PER_SERIES):
    ser = lambda a: ctx["series"].get(a, a)
    s_hit = s_ndcg = w_hit = 0.0
    by_kind = {"보던": [0, 0], "새": [0, 0]}  # [맞힌 수, 전체 수]
    for case in cases:
        user = {"history": case["history"], "t": case["t"]}  # 모델에는 정답을 넘기지 않는다
        for chips in ("genres", "themes"):  # 온보딩 흉내에서 고른 칩
            if chips in case:
                user[chips] = case[chips]
        recs = pick_top(model(user, ctx), user, ctx, one_per_series)
        rec_series = [ser(a) for a in recs]
        hit = ser(case["target"]) in rec_series
        if hit:
            s_hit += 1
            s_ndcg += 1 / math.log2(rec_series.index(ser(case["target"])) + 2)
        w_hit += case["target"] in recs
        kind = "보던" if ser(case["target"]) in {ser(a) for a in case["history"].anime.tolist()} else "새"
        by_kind[kind][0] += hit
        by_kind[kind][1] += 1
    n = len(cases)
    rate = lambda k: by_kind[k][0] / by_kind[k][1] if by_kind[k][1] else 0.0
    return {"시리즈 Hit@10": s_hit / n, "시리즈 NDCG@10": s_ndcg / n, "작품 Hit@10": w_hit / n,
            "보던 시리즈 Hit@10": rate("보던"), "새 시리즈 Hit@10": rate("새")}


def main():
    max_users = int(sys.argv[1]) if len(sys.argv) > 1 else None
    anime = load_anime()
    data = load_interactions()
    all_cases = make_cases(data)
    cases = all_cases[:max_users] if max_users else all_cases
    anilist = load_anilist()
    stats = stats_histories(data, all_cases)  # 정답은 빼고 센다
    ctx = build_context(cases, anime, load_series(anime), anilist, data=data, stats=stats)
    anime_ranker.build_cowatch(ctx, stats)
    anime_ranker.build_ease(ctx)
    cands = set(ctx["by_popularity"][:N_CANDIDATES])
    in_cands = sum(c["target"] in cands for c in cases) / len(cases)
    print(f"유저 {len(data['user_ids']):,}명 중 채점 가능 {len(cases):,}명 · 작품 {len(anime):,}편 · 정답 {'+'.join(TARGET_STATUS)}")
    print(f"정답이 후보(인기 상위 {N_CANDIDATES}편) 안에 있는 비율 {in_cands:.1%}")
    print(f"작품 표현: {FEATURES} · AniList 정보 있는 후보 {len(cands & set(anilist)) / len(cands):.1%}\n")
    cols = ["시리즈 Hit@10", "보던 시리즈 Hit@10", "새 시리즈 Hit@10", "시리즈 NDCG@10", "작품 Hit@10"]
    print(f"{'방법':<14}" + "".join(f"{c:>16}" for c in cols))
    for name, model in MODELS.items():
        m = evaluate(cases, ctx, model)
        print(f"{name:<14}" + "".join(f"{m[c]:>16.3f}" for c in cols))


if __name__ == "__main__":
    main()
