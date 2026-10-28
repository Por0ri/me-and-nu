# llm_card_test — EXP-38 1단계: LLM 취향 카드로 기록 없는 신작의 "비슷한 작품"을 더 잘 찾는가 (유저 없이 작품끼리)
#
# 문맥: 서비스 문맥 (service.build(): 같이 본 기록 = MAL (2022년 이후 작품 뺌) + AniList 표본). 같이 본 유저 100명 미만 작품은 빼고 잼
# 방법: EXP-28 (new_anime_test.learn_test) 1단계와 같음. 특징 · 카드 · 줄거리가 다 있는 작품을 5묶음으로 나눠 한 묶음씩 가리고
#   나머지로 학습 → 가린 작품의 이웃 10개 중 실제로 같이 많이 본 작품 (같이 본 코사인 상위 50) 수
#   카드 · 줄거리 임베딩은 평균을 빼고 길이 1로 (new_anime_test.load_text 와 같음)
# 기준 (미리 정함, EXPERIMENTS EXP-38): 학습 · 특징 + 카드 (세기 0.1 · 1 · 10 중 평균 최고)가 지금 (학습 · 특징, 세기 1)보다
#   0.3개 넘게 오르면 2단계 (유저 시험). 못 넘으면 폐기. 2025년 이후 작품 (Qwen3-8B 공개 뒤)은 따로 셈 (새는 정보 확인)
#
#   python -m model.eval.llm_card_test [desc]
#   desc: 카드 자리에 줄거리 임베딩을 넣어 같은 표를 냄 (카드가 오기 전 기준선 · 코드 확인용)

import json
import sys
import time

import numpy as np

from model.algo import anime_ranker as ar
from model.algo import service
from model.eval import next_anime_test as t

LAMS = (0.1, 1.0, 10.0)
K, G = 10, 50
NEW_FROM = 2025


def load_emb(ctx, name):
    src = t.ROOT / "anilist"
    ids = json.loads((src / f"{name}_emb_ids.json").read_text(encoding="utf-8"))["ids"]
    emb = np.load(src / f"{name}_emb.npy").astype(np.float64)
    T = np.zeros((len(ctx["ids"]), emb.shape[1]))
    for i, a in enumerate(ids):
        if a in ctx["row"]:
            T[ctx["row"][a]] = emb[i]
    has = np.flatnonzero(np.abs(T).sum(axis=1) > 0)
    T[has] -= T[has].mean(axis=0)
    T[has] /= np.linalg.norm(T[has], axis=1, keepdims=True)
    return T


def main(name="card"):
    t0 = time.time()
    ctx = service.build()
    card, desc = load_emb(ctx, name), load_emb(ctx, "desc")
    year = {}
    with open(t.ROOT / "anilist" / "llm_card_inputs.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            year[r["id"]] = r["year"] or 0
    cw = ctx["cw_rows"]
    no = np.zeros(len(ctx["ids"]), dtype=bool)
    no[[ctx["row"][a] for a in ctx["no_record"] if a in ctx["row"]]] = True
    nz = lambda M: np.abs(M[cw]).sum(axis=1) > 0
    rows = cw[nz(ctx["X"]) & nz(card) & nz(desc) & ~no[cw]]
    new = np.array([year.get(ctx["ids"][r], 0) >= NEW_FROM for r in rows])
    fold = np.random.default_rng(0).permutation(len(rows)) % 5
    Xn = lambda r: ctx["X"][r] / ctx["vnorm"][r, None]
    print(f"준비 {time.time() - t0:.0f}초 · 비교 작품 {len(rows):,}편 (그중 {NEW_FROM}년 이후 {new.sum()}편) · "
          f"카드 자리 = {'줄거리 (기준선 확인)' if name == 'desc' else '카드'}", flush=True)

    def learned(te, tr, feats, lam, T):
        ctx["T"] = T
        return ar.learned_sims(ctx, te, tr, feats, lam)

    methods = [("원래 특징 코사인", lambda te, tr: Xn(te) @ Xn(tr).T),
               ("줄거리 코사인", lambda te, tr: desc[te] @ desc[tr].T),
               ("카드 코사인", lambda te, tr: card[te] @ card[tr].T),
               ("학습 · 특징 (지금)", lambda te, tr: learned(te, tr, ("feat",), 1.0, card)),
               ("학습 · 특징 + 줄거리 · 세기 1", lambda te, tr: learned(te, tr, ("feat", "text"), 1.0, desc))]
    for lam in LAMS:
        methods += [(f"학습 · 카드 · 세기 {lam:g}", lambda te, tr, lam=lam: learned(te, tr, ("text",), lam, card)),
                    (f"학습 · 특징 + 카드 · 세기 {lam:g}", lambda te, tr, lam=lam: learned(te, tr, ("feat", "text"), lam, card))]
    score = {m: [] for m, _ in methods}
    newhit = {m: [0, 0] for m, _ in methods}
    for f in range(5):
        tr, te = rows[fold != f], rows[fold == f]
        C = ctx["cw_sim"]["cosine"][np.ix_(ctx["cw_pos"][te], ctx["cw_pos"][tr])].astype(np.float64)
        truth = [set(r) for r in np.argpartition(-C, G, axis=1)[:, :G]]
        is_new = new[fold == f]
        for m, fn in methods:
            top = np.argpartition(-fn(te, tr), K, axis=1)[:, :K]
            hit = np.array([len(set(top[i]) & truth[i]) for i in range(len(te))])
            score[m].append(hit.mean())
            newhit[m][0] += hit[is_new].sum()
            newhit[m][1] += is_new.sum()
        print(f"  {f + 1}/5묶음 ({time.time() - t0:.0f}초)", flush=True)

    print(f"\n[1단계 · 작품 {len(rows):,}편 · 가린 작품의 이웃 {K}개 중 같이 많이 본 작품 수 (평균 ± 묶음 간 표준편차) · "
          f"무작위면 {K * G / (len(rows) * 0.8):.2f}개]")
    print(f"방법\t전체\t{NEW_FROM}년 이후 ({new.sum()}편)")
    for m, _ in methods:
        n_new = newhit[m][1]
        print(f"{m}\t{np.mean(score[m]):.2f} ± {np.std(score[m]):.2f}\t{newhit[m][0] / n_new:.2f}" if n_new else f"{m}\t{np.mean(score[m]):.2f}")
    now = np.mean(score["학습 · 특징 (지금)"])
    best = max((m for m in score if m.startswith("학습 · 특징 + 카드")), key=lambda m: np.mean(score[m]))
    gain = np.mean(score[best]) - now
    print(f"\n→ {best}: {np.mean(score[best]):.2f}개 (지금 {now:.2f}개, {gain:+.2f}개) → "
          f"{'기준 통과 (0.3개 넘게 오름) → 2단계' if gain > 0.3 else '기준 못 넘음 → 폐기'}", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main("desc" if sys.argv[1:] == ["desc"] else "card")
