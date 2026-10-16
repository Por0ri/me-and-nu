# next_anime_test — MAL 시청 기록으로 "다음에 볼 작품 맞히기" 점수를 매긴다 (M3 채점기)
#
# 유저마다 마지막에 본 작품 하나를 가리고, 그 전 기록만 보고 추천한 상위 10개 안에
# 가린 작품이 있는지 본다. 모델을 고칠 때마다 이 점수로 나아졌는지 확인한다.
#
# 메인 점수는 시리즈 단위다. 정답이 "원피스 극장판 Z"일 때 "원피스"를 추천해도 맞힌 걸로 친다.
# me;nu 는 작품 글을 보여주는 서비스라 같은 시리즈면 같은 관심사로 보기 때문. 작품 단위 점수도 같이 낸다.
#
# 작품 취향만 잰다. 콘텐츠 유형(소식·가이드 등) 취향은 MAL 에 없어서 못 잰다 → M3-2 시뮬레이터 몫.
# 추천 방법(모델)은 model/algo/anime_ranker.py 에 있다. 여기는 데이터 읽기·정답 가리기·채점만 한다.
#
#   python model/scripts/export_mal_sample.py     # 먼저 표본 뽑기 (한 번만)
#   python -m model.eval.next_anime_test          # 시험 보기
#   python -m model.eval.next_anime_test 3000     # 유저 3000명만 (빠르게 확인)

import csv
import json
import math
import pathlib
import sys
from collections import Counter, defaultdict
from datetime import datetime

from model.algo import anime_ranker
from model.algo.series import SEASON_RELATIONS, build_series
from model.algo.anime_ranker import (FEATURES, N_CANDIDATES, ONE_PER_SERIES, WATCHED,
                                    genre, next_in_series, next_limited, next_plus_new, pick_top, popular)

DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "mal_sample"
ANILIST = DATA.parent / "anilist" / "media.jsonl"  # model/scripts/fetch_anilist.py 로 받음

TARGET_AFTER = datetime(2020, 1, 1)  # 마지막 기록이 이보다 오래되면 날짜가 부정확해서 뺀다
MIN_HISTORY = 5                 # 가리고 남은 기록이 이보다 적으면 뺀다

MODELS = {"인기순": popular, "장르 취향(v0)": genre,
          "다음 편 우선": next_in_series, "다음 편 자리 제한": next_limited,
          "다음 편 + 새 시리즈": next_plus_new}


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


def load_series(anime):
    # MAL 작품 관계에서 속편·전편만 골라 시리즈를 묶는다 (규칙은 model/algo/series.py)
    with open(DATA / "anime_anime.csv", encoding="utf-8") as f:
        pairs = [(int(r["anime_a"]), int(r["anime_b"])) for r in csv.DictReader(f)
                 if r["related"] == "1" and r["relation_type"] in SEASON_RELATIONS]
    return build_series({a: v["title"] for a, v in anime.items()}, pairs)


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


def build_context(cases, anime, series, anilist=None, features=FEATURES):
    # 인기도는 가리고 남은 기록만으로 센다 (가린 작품을 미리 보면 반칙)
    pop = Counter()
    for case in cases:
        pop.update({r[1] for r in case["history"]})
    return anime_ranker.build_context(anime, series, pop, anilist, features)


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
        recs = pick_top(model(user, ctx), user, ctx, one_per_series)
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
