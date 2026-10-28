# anilist_bench — AniList 시험지로 LightGCN · SASRec 신규 유저 다시 보기 (EXPERIMENTS EXP-35, 4장 5-5 · 5-6)
#
# EXP-32 (MAL 시험지)에서 신규 유저의 v0.5 에 넣기가 오른 두 모델을 2026년 정답 시험지 (anilist_test)로 다시 잰다.
# 재는 법은 EXP-32 와 같음 (model_bench: 혼자 · v0.5 의 같이 본 작품 자리에 넣기, γ 는 연습으로). 고른 값도 EXP-32 그대로:
#   LightGCN 2층 (EXP-34 에서 씨앗 3개 모두 통과했을 때만) · SASRec 크기 128
# 학습 기록 = MAL (2022년 이후 작품 기록은 뺌) + AniList 표본 (= 1-3 의 "MAL + AniList" 같이 본 작품 재료와 같은 기록)
#   LightGCN 유저 벡터는 모든 장면에서 고른 작품으로 계산 (② 에선 시험 유저가 학습 기록에 없음)
#   학습 기록이 없는 작품 점수는 v0.5 와 같은 이웃 · 무게로 빌려 옴 (EXP-23 · 28)
# 조건 (같은 모델을 조건마다 따로 학습)
#   same  ① 같은 시기 (1-3 과 같음): AniList 는 시험 유저 정답 전 기록 + 나머지 유저 기준일 (2026-09-01) 전 기록
#   split ② 시간 분리: AniList 는 모든 유저의 2026-01-01 전 기록만 (정답 기간 기록을 학습 · 같이 본 작품 · 인기에 안 씀.
#         최근 30일 인기는 서비스처럼 정답 시각 전 기록으로 계속 셈) → MAL 시험이 순서 모델에 후했던 것 (EXP-31)을 빼고 봄
# 장면: 기록 전체 · 신규 유저 (좋아하는 1 + 칩) · 신규 유저 · 요즘 1 (좋아하는 1 + 요즘 1 + 칩, 온보딩 제안 EXP-33)
# 기준 v0.5: 같이 본 작품 재료 MAL (지금 서비스) · MAL + AniList 둘 다 기록. 판단은 1-3 에서 채택된 재료로
# 채택 기준 (미리 정함): 신규 유저 (좋아하는 1 + 칩) 장면에서 v0.5 에 넣기가 ① · ② 모두 실전 Hit@10 · NDCG 둘 다 ▲ +
#   우연일 확률 0.05 미만 + 연습 같은 방향 + 명단 겹침 정답을 뺀 비교도 같은 방향 → 채택 후보.
#   ① 만 통과 → 같은 시기 기록 덕 (보류). 둘 다 못 넘음 → 폐기
#
#   python -m model.eval.anilist_bench same|split [--no-gcn] [--users N]   (GPU 서버)
#   → model/data/logs/anilist_bench_{same|split}.log · _result.json  (--users 를 주면 작게 확인: 학습 2번, 이름에 _smoke)

import os
for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):   # 서버는 CPU 4개 할당 (EXP-31 환경 참고)
    os.environ.setdefault(_k, "4")

import json
import sys
import time

import numpy as np
import torch

from model.algo import anime_ranker as ar
from model.eval import anilist_test as at
from model.eval import model_bench as mb
from model.eval import next_anime_test as t
from model.eval import onboarding_test as ot
from model.eval.new_anime_test import case_hits

SCENES = ("기록 전체", "신규 유저", "신규 유저 · 요즘 1")
DECIDE = "신규 유저"                # 채택 판단 장면


def prepare(cond):
    # ① 통계 = 시험 유저 정답 전 + 나머지 유저 기준일 전 / ② 모든 유저의 2026-01-01 전 기록 (anilist_test.split_stats)
    users, cases, outside, stats, ctx = at.prepare(cond == "split")
    src = at.sources(ctx, stats)
    ctxs = at.variants(ctx, stats, ("mal", "both"), src)
    anime, anilist = ctx["anime"], ctx["anilist"]
    scenes = {"기록 전체": cases,
              "신규 유저": [t.onboarding(c, anime, 1, 3, anilist, 3) for c in cases],
              "신규 유저 · 요즘 1": [ot.entry(c, anime, anilist, 1, 1, (3, 3)) for c in cases]}
    return cases, ctxs, src[0]["both"][0], scenes


def borrow(ctx, s):
    # 학습 기록이 없는 작품: 빌려 올 이웃이 있으면 v0.5 와 같은 이웃 · 무게 평균, 없으면 그 유저의 가장 낮은 점수
    rows, donors, W = ctx["cf_borrow"]
    pos = ctx["cw_pos"]
    s = np.array(s, dtype=np.float32)
    miss = pos[np.flatnonzero(ctx["cf_missing"])]
    s[:, miss[miss >= 0]] = s.min(axis=1, keepdims=True)
    s[:, pos[rows]] = s[:, pos[donors]] @ W.T.astype(np.float32)
    return s


def main():
    args = sys.argv[1:]
    if "--users" in args:
        i = args.index("--users")
        at.LIMIT = int(args[i + 1])
        del args[i:i + 2]
    cond = "split" if "split" in args else "same"
    smoke = at.LIMIT is not None
    name = f"anilist_bench_{cond}" + ("_smoke" if smoke else "")
    mb.LOG, mb.RESULT = mb.LOGS / f"{name}.log", mb.LOGS / f"{name}_result.json"
    t0 = time.time()
    mb.log(f"=== AniList 시험 · {'① 같은 시기' if cond == 'same' else '② 시간 분리 (2026-01-01 전 기록만 학습)'} "
           f"{'(작게 확인)' if smoke else ''} · 장치 {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'} ===")
    cases, ctxs, train, scenes = prepare(cond)
    bench = mb.Bench.from_parts(ctxs["both"], train, scenes, int(len(cases) * at.TUNE_SHARE))
    bench.fold = True
    over = at.kinds(ctxs["both"], cases)[2]
    refs = {"MAL + AniList": bench.v05, "MAL": {sc: case_hits(cs, ctxs["mal"], ar.next_auto) for sc, cs in scenes.items()}}
    mb.log(f"준비 {(time.time() - t0) / 60:.1f}분 · 학습 유저 {len(train):,} · 후보 작품 {bench.n:,} · 문제 {len(cases):,} "
           f"(연습 {bench.n_tune:,}) · 명단 겹침 {over.mean():.1%} · 학습 기록 없어 빌려 온 작품 {len(ctxs['both']['no_record']):,}편")
    for key, ref in refs.items():
        for sc in SCENES:
            h = ref[sc][bench.part(sc, "실전")]
            mb.log(f"기준 v0.5 ({key}) · {sc}: 실전 Hit@10 {h[:, 0].mean():.4f} NDCG {h[:, 1].mean():.4f}")

    epochs = (lambda k: 2) if smoke else (lambda k: mb.MAX_EPOCHS[k])
    scores = {}
    if "--no-gcn" not in args:
        model, _, sec, deg_i = mb.train_gcn(bench, 2, epochs("gcn"))
        scores["LightGCN 2층"] = ({sc: borrow(bench.ctx, mb.gcn_scores(bench, model, sc, deg_i)) for sc in SCENES}, sec)
        del model
        torch.cuda.empty_cache()
    model, _, sec = mb.train_seq(bench, "SASRec", 128, epochs("seq"))
    scores["SASRec 크기 128"] = ({sc: borrow(bench.ctx, mb.seq_scores(bench, model, sc)) for sc in SCENES}, sec)

    results = {}
    for mname, (sc_scores, sec) in scores.items():
        results[mname] = {"학습 초": round(sec, 1)}
        for sc in SCENES:
            alone, dropin, g = mb.measure(bench, sc, sc_scores[sc])
            test, tune = bench.part(sc, "실전"), bench.part(sc, "연습")
            keep = ~over[test]
            h = alone["실전"]
            r = results[mname][sc] = {"혼자 hit": float(h[:, 0].mean()), "혼자 ndcg": float(h[:, 1].mean()), "gamma": g,
                                      "넣기 hit": float(dropin["실전"][:, 0].mean()), "넣기 ndcg": float(dropin["실전"][:, 1].mean())}
            for key, ref in refs.items():
                d = mb.pstats(ref[sc][test], dropin["실전"])
                dt = mb.pstats(ref[sc][tune], dropin["연습"])
                dk = mb.pstats(ref[sc][test][keep], dropin["실전"][keep])
                ok = d[0] > 0 and d[2] > 0 and d[1] < 0.05 and d[3] < 0.05 and dt[0] > 0 and dk[0] > 0 and dk[2] > 0
                r[key] = {"d_pp": d[0], "p_hit": d[1], "d_ndcg": d[2], "p_ndcg": d[3], "연습 d_pp": dt[0], "연습 d_ndcg": dt[2],
                          "겹침 뺀 d_pp": dk[0], "겹침 뺀 d_ndcg": dk[2], "통과": ok}
                mb.log(f"{mname} · {sc} · 기준 v0.5 ({key}): 혼자 {h[:, 0].mean():.4f} · 넣기 (γ {g}) {dropin['실전'][:, 0].mean():.4f} "
                       f"({d[0]:+.2f}%p, 우연 {d[1]:.3f} · NDCG {d[2]:+.4f}, 우연 {d[3]:.3f} · 연습 {dt[0]:+.2f}%p · NDCG {dt[2]:+.4f} · "
                       f"명단 겹침 뺀 {dk[0]:+.2f}%p · NDCG {dk[2]:+.4f})" + (f" → {'통과' if ok else '못 넘음'}" if sc == DECIDE else ""))
        mb.RESULT.write_text(json.dumps({"조건": cond, "모델": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    mb.log(f"전체 {(time.time() - t0) / 60:.0f}분 · 끝")


if __name__ == "__main__":
    main()
