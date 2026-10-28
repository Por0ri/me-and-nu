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
# chips: 작품을 안 받을 때 테마 칩이 장르 칩 위에 더 도움이 되는지 (장르만 vs 장르 + 테마, 둘 다 작품 없음)
#   기준 (미리 정함): 장르 3 → 장르 3 + 테마 3 이 실전 Hit@10 · NDCG 둘 다 ▲ + 우연일 확률 0.05 미만 + 연습 같은 방향이면 테마 칩을 넣고, 아니면 뺀다
#
# chips15: 칩 목록 줄인 안 (장르 15 · 테마 15: 마법소녀 빼고, 호러 · 스릴러와 이세계 · 가상 세계를 합침)과 테마 개수 (모두 작품 없음)
#   기준 (미리 정함): ① 줄인 목록 장르 3 + 테마 3 이 원래 목록보다 실전 Hit@10 · NDCG 가 확실히 (우연 0.05 미만) 떨어지지 않으면 줄인 목록 확정
#   ② 테마 1개가 3개보다 실전 Hit@10 · NDCG 가 확실히 떨어지지 않으면 "테마 1개 이상"으로 충분, 떨어지면 "3개 정도 권장"
#
# chipcap: 화면 확정 목록 (장르 16 · 소재 19)에서 몇 개까지 고르게 해도 되는지 (모두 작품 없음)
#   기준 (미리 정함): 장르 · 소재 k개씩 (5 · 7 · 10)이 3개씩보다 실전 Hit@10 이나 NDCG 가 확실히 (우연 0.05 미만) 떨어지고
#      연습도 같은 방향이면 "떨어짐". 상한 = 떨어지지 않는 가장 큰 k. 10개까지 안 떨어지면 상한 없이 "1개 이상"
#      시험에선 그 유저가 가장 많이 본 장르 · 소재 k개를 고른다고 흉내 (실제 유저는 덜 좋아하는 것도 고를 수 있음)
#
# chipmax: Q2 상한 ("더 보기"까지 소재 43개). A = 작품 없음, B = 요즘 본 작품 1개 + 칩. 각각 3개씩 대비
#   많이 고르기 (10개씩 · 장르 16 + 소재 20 · 전부). 유저는 선호하는 칩만 고른다고 가정 (사용자 결정, "막 누르기"는 안 잼)
#   기준 (미리 정함): 실전 Hit@10 이나 NDCG 가 3개씩보다 확실히 (우연 0.05 미만) 떨어지고 연습도 같은 방향이면 "떨어짐".
#   떨어지는 개수가 나오면 그 전 개수를 상한으로, 전부 골라도 안 떨어지면 상한 없음
#
# chipb: chipmax 의 B (요즘 본 작품 1개 + 칩)에서 10개씩이 떨어져서, 3 ~ 10개 사이 (5 · 7개)를 잼. 기준은 chipmax 와 같음
#   상한 = 3개씩보다 안 떨어지는 가장 큰 개수 (10개씩은 이미 떨어짐). 작품 1개가 칩에 가장 쉽게 묻히는 경우라 여기서 정함
#
#   python -m model.eval.onboarding_test [nowork | chips | chips15 | chipcap | chipmax | chipb]

import sys
import time

import numpy as np

from model.algo import anime_ranker as ar
from model.eval import next_anime_test as t
from model.eval.new_anime_test import case_hits, paired


def entry(case, anime, anilist, n_fav, n_recent, chips, groups=(None, None), noise=(0, 0)):
    # 좋아하는 작품 n_fav 개 + 요즘 본 작품 n_recent 개 (겹치면 그다음 최근 작품) + 칩 (genres, themes 개수)
    h = case["history"]
    w = np.flatnonzero(np.isin(h.status, ar.WATCHED))
    fav = w[np.lexsort((w, -h.t[w], -h.fav[w].astype(np.int64), -h.score[w].astype(np.int64)))][:n_fav]
    rest = [i for i in w[np.argsort(-h.t[w], kind="stable")].tolist() if i not in set(fav.tolist())]
    pick = np.r_[fav, np.array(rest[:n_recent], dtype=np.int64)].astype(np.int64)
    pick = pick[np.lexsort((h.anime[pick], h.t[pick]))]
    onb = t.onboarding(case, anime, 1, chips[0], anilist, chips[1], *groups, noise)   # 칩만 씀 (작품은 아래에서 바꿈)
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
CHIP_VARIANTS = [
    ("작품 · 칩 없음 (인기순만)", 0, 0, (0, 0)),
    ("장르 3 (작품 없음)", 0, 0, (3, 0)),
    ("장르 3 + 테마 3 (작품 없음)", 0, 0, (3, 3)),
]
CHIP_PAIRS = [
    ("작품 · 칩 없음 (인기순만)", "장르 3 (작품 없음)", "작품 없이 장르 칩만"),
    ("장르 3 (작품 없음)", "장르 3 + 테마 3 (작품 없음)", "작품 없을 때 테마 칩 효과"),
]
SHORT = (t.GENRE_CHIPS_15, t.THEME_CHIPS_15)
CHIP15_VARIANTS = [
    ("장르 3 + 테마 3 (작품 없음)", 0, 0, (3, 3)),
    ("줄인 목록 장르 3 + 테마 3 (작품 없음)", 0, 0, (3, 3), SHORT),
    ("줄인 목록 장르 3 + 테마 1 (작품 없음)", 0, 0, (3, 1), SHORT),
]
CHIP15_PAIRS = [
    ("장르 3 + 테마 3 (작품 없음)", "줄인 목록 장르 3 + 테마 3 (작품 없음)", "목록 줄이기 (17 · 16 → 15 · 15)"),
    ("줄인 목록 장르 3 + 테마 3 (작품 없음)", "줄인 목록 장르 3 + 테마 1 (작품 없음)", "테마 3 → 1개"),
]
SCREEN = (t.GENRE_CHIPS_16, t.THEME_CHIPS_19)
CAP_VARIANTS = [(f"화면 목록 장르 {k} + 소재 {k} (작품 없음)", 0, 0, (k, k), SCREEN) for k in (3, 5, 7, 10)]
CAP_PAIRS = [(CAP_VARIANTS[0][0], v[0], f"3 → {v[3][0]}개씩") for v in CAP_VARIANTS[1:]]
FULL = (t.GENRE_CHIPS_16, t.THEME_CHIPS_ALL)
MAX_VARIANTS = []
for scene, n_recent in (("A 작품 없음", 0), ("B 요즘 본 1 +", 1)):
    MAX_VARIANTS += [(f"{scene} 3개씩", 0, n_recent, (3, 3), FULL), (f"{scene} 10개씩", 0, n_recent, (10, 10), FULL)]
    if n_recent == 0:
        MAX_VARIANTS += [(f"{scene} 장르 16 + 소재 20", 0, n_recent, (16, 20), FULL)]
    MAX_VARIANTS += [(f"{scene} 전부 (16 + 43)", 0, n_recent, (-1, -1), FULL)]
MAX_PAIRS = [(v0[0], v[0], v[0] + " (3개씩 대비)") for v0 in MAX_VARIANTS if v0[0].endswith("3개씩")
             for v in MAX_VARIANTS if v[0] != v0[0] and v[0].split(" ")[0] == v0[0].split(" ")[0]]
MODES = {"nowork": (NO_WORK_VARIANTS, NO_WORK_PAIRS), "chips": (CHIP_VARIANTS, CHIP_PAIRS),
         "chips15": (CHIP15_VARIANTS, CHIP15_PAIRS), "chipcap": (CAP_VARIANTS, CAP_PAIRS),
         "chipmax": (MAX_VARIANTS, MAX_PAIRS)}
B_VARIANTS = [(f"B 요즘 본 1 + {k}개씩", 0, 1, (k, k), FULL) for k in (3, 5, 7)]
B_PAIRS = [(B_VARIANTS[0][0], v[0], v[0] + " (3개씩 대비)") for v in B_VARIANTS[1:]]
MODES["chipb"] = (B_VARIANTS, B_PAIRS)


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
    for name, n_fav, n_recent, chips, *groups in variants:
        cs = [entry(c, anime, anilist, n_fav, n_recent, chips, *groups) for c in cases]
        hits[name] = case_hits(cs, ctx, ar.next_auto)
        h = hits[name]
        print(f"{name}\t연습 {h[:t.TUNE_USERS, 0].mean():.4f}\t실전 Hit@10 {h[t.TUNE_USERS:, 0].mean():.4f}\t"
              f"NDCG {h[t.TUNE_USERS:, 1].mean():.4f}\t({time.time() - t0:.0f}초)", flush=True)
    print()
    for a, b, what in pairs:
        for label, sl in (("연습", slice(0, t.TUNE_USERS)), ("실전", slice(t.TUNE_USERS, None))):
            print(f"{what} [{label}]: {paired(hits[a][sl], hits[b][sl])}", flush=True)


if __name__ == "__main__":
    main(*MODES.get(sys.argv[1] if sys.argv[1:] else "", ()))
