# onboarding_test — 온보딩에서 무엇을 물어볼지: 입력별로 신규 유저 추천이 얼마나 맞나 (EXPERIMENTS EXP-33)
#
# MAL 시험지 (3판)의 신규 유저 장면. 그 유저 기록에서 온보딩 입력을 흉내 내 v0.5(지금)로 추천하고 채점한다
#   좋아하는 작품 = 점수 높게 준 작품 (같으면 최애 → 최근, next_anime_test.shorten 과 같은 순서)
#   요즘 본 작품 = 정답 직전에 가장 최근 기록된 본 작품 ("요즘 보는 애니" 질문 흉내)
#   장르 칩 3 · 테마 칩 3 = 그 유저가 본 작품에 가장 많이 나온 것 (next_anime_test.onboarding 과 같음)
# 기준 (미리 정함): 요즘 본 작품이 (요즘 1 + 칩 vs 좋아하는 1 + 칩) 와 (좋아하는 1 + 요즘 1 vs 좋아하는 2, 개수 같게) 모두
#   실전 Hit@10 · NDCG 둘 다 ▲ + 우연일 확률 0.05 미만 + 연습 같은 방향이면 "요즘 보는 애니" 질문을 제안.
#   칩은 확실히 오를 때만 필수로 제안. MAL 시험은 요즘 작품에 후한 편이라 (EXP-31) 효과 크기는 과장될 수 있음
# nowork: 작품을 안 받는 온보딩 (작품 · 칩 없음 = 인기순만, 칩만) 과 같은 시험으로 비교.
#   기준 (미리 정함): 칩만 → 좋아하는 1 + 칩 이 실전 Hit@10 · NDCG 둘 다 ▲ + 우연일 확률 0.05 미만 + 연습 같은 방향이면 작품 질문을 필수로 제안
#
#   python -m model.eval.onboarding_test [nowork]

import sys
import time

import numpy as np

from model.algo import anime_ranker as ar
from model.eval import next_anime_test as t
from model.eval.new_anime_test import case_hits, paired


def entry(case, anime, anilist, n_fav, n_recent, chips):
    # 좋아하는 작품 n_fav 개 + 요즘 본 작품 n_recent 개 (겹치면 그다음 최근 작품) + 칩 (genres, themes 개수)
    h = case["history"]
    w = np.flatnonzero(np.isin(h.status, ar.WATCHED))
    fav = w[np.lexsort((w, -h.t[w], -h.fav[w].astype(np.int64), -h.score[w].astype(np.int64)))][:n_fav]
    rest = [i for i in w[np.argsort(-h.t[w], kind="stable")].tolist() if i not in set(fav.tolist())]
    pick = np.r_[fav, np.array(rest[:n_recent], dtype=np.int64)].astype(np.int64)
    pick = pick[np.lexsort((h.anime[pick], h.t[pick]))]
    onb = t.onboarding(case, anime, 1, chips[0], anilist, chips[1])     # 칩만 씀 (작품은 아래에서 바꿈)
    return {**onb, "history": t.History(*(col[pick] for col in h))}


VARIANTS = [
    ("좋아하는 1", 1, 0, (0, 0)),
    ("좋아하는 1 + 장르 3", 1, 0, (3, 0)),
    ("좋아하는 1 + 장르 3 + 테마 3 (지금 흉내)", 1, 0, (3, 3)),
    ("좋아하는 3 + 장르 3 + 테마 3", 3, 0, (3, 3)),
    ("요즘 본 1 + 장르 3 + 테마 3", 0, 1, (3, 3)),
    ("좋아하는 2 + 장르 3 + 테마 3", 2, 0, (3, 3)),
    ("좋아하는 1 + 요즘 본 1 + 장르 3 + 테마 3", 1, 1, (3, 3)),
]
PAIRS = [   # (기준, 비교, 무엇을 보나)
    ("좋아하는 1", "좋아하는 1 + 장르 3", "장르 칩 효과"),
    ("좋아하는 1 + 장르 3", "좋아하는 1 + 장르 3 + 테마 3 (지금 흉내)", "테마 칩 효과"),
    ("좋아하는 1 + 장르 3 + 테마 3 (지금 흉내)", "좋아하는 3 + 장르 3 + 테마 3", "작품 1 → 3개"),
    ("좋아하는 1 + 장르 3 + 테마 3 (지금 흉내)", "요즘 본 1 + 장르 3 + 테마 3", "좋아하는 작품 대신 요즘 본 작품"),
    ("좋아하는 2 + 장르 3 + 테마 3", "좋아하는 1 + 요즘 본 1 + 장르 3 + 테마 3", "같은 2개 중 1개를 요즘 본 작품으로"),
]
NO_WORK_VARIANTS = [
    ("작품 · 칩 없음 (인기순만)", 0, 0, (0, 0)),
    ("장르 3 + 테마 3 (작품 없음)", 0, 0, (3, 3)),
    ("좋아하는 1 + 장르 3 + 테마 3 (지금 흉내)", 1, 0, (3, 3)),
    ("좋아하는 1 + 요즘 본 1 + 장르 3 + 테마 3", 1, 1, (3, 3)),
]
NO_WORK_PAIRS = [
    ("작품 · 칩 없음 (인기순만)", "장르 3 + 테마 3 (작품 없음)", "작품 없이 칩만"),
    ("장르 3 + 테마 3 (작품 없음)", "좋아하는 1 + 장르 3 + 테마 3 (지금 흉내)", "칩에 작품 1개 더하기"),
    ("작품 · 칩 없음 (인기순만)", "좋아하는 1 + 요즘 본 1 + 장르 3 + 테마 3", "아무것도 없음 → 좋아하는 1 + 요즘 1 + 칩"),
]


def main(variants=VARIANTS, pairs=PAIRS):
    t0 = time.time()
    anime, data = t.load_anime(), t.load_interactions()
    cases = t.make_cases(data)
    anilist = t.load_anilist()
    stats = t.stats_histories(data, cases)
    ctx = t.build_context(cases, anime, t.load_series(anime), anilist, data=data, stats=stats)
    ar.build_cowatch(ctx, stats)
    ar.build_ease(ctx)
    print(f"준비 {time.time() - t0:.0f}초 · 문제 {len(cases):,} (연습 {t.TUNE_USERS:,})", flush=True)
    hits = {}
    for name, n_fav, n_recent, chips in variants:
        cs = [entry(c, anime, anilist, n_fav, n_recent, chips) for c in cases]
        hits[name] = case_hits(cs, ctx, ar.next_auto)
        h = hits[name]
        print(f"{name}\t연습 {h[:t.TUNE_USERS, 0].mean():.4f}\t실전 Hit@10 {h[t.TUNE_USERS:, 0].mean():.4f}\t"
              f"NDCG {h[t.TUNE_USERS:, 1].mean():.4f}\t({time.time() - t0:.0f}초)", flush=True)
    print()
    for a, b, what in pairs:
        for label, sl in (("연습", slice(0, t.TUNE_USERS)), ("실전", slice(t.TUNE_USERS, None))):
            print(f"{what} [{label}]: {paired(hits[a][sl], hits[b][sl])}", flush=True)


if __name__ == "__main__":
    main(*((NO_WORK_VARIANTS, NO_WORK_PAIRS) if sys.argv[1:] == ["nowork"] else ()))
