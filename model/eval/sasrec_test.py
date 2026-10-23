# sasrec_test — SASRec(트랜스포머 순서 모델) vs v0.5, MAL 시험지 (EXPERIMENTS 4장 5-1)
#
# 가설: 시청 순서를 학습하는 SASRec 이 EASE(순서 안 봄)보다 기록 많은 유저의 다음 작품을 더 맞힌다
# 비교: ① v0.5 (지금) ② SASRec 혼자 (추천 규칙은 같음: 본 작품 · 성인 · 방영 전 제외, 시리즈 1개)
#       ③ v0.5 기록 많은 유저 설정에서 EASE 자리만 SASRec 점수로 (기록 적은 유저는 그대로)
# 학습: 통계 기록 (시험 유저는 정답 전 기록만, 나머지는 2022-02 전) · 본 작품(다 봄 · 보는 중)을 시간 순으로 · 최근 200편.
#   기록 시각이 같은 작품끼리는 순서를 무작위로 (MAL 은 한꺼번에 고친 기록이 같은 시각이라 작품 번호 순서가 섞여 들어감)
# 고르기 (연습 5,000명만): 모델 크기 64 / 128 (연습 시리즈 Hit@10 이 3번 재는 동안 안 오르면 멈춤) → ③ 의 비중 γ
# 채택 (미리 정함): 실전 Hit@10 · NDCG 둘 다 ▲ + 우연일 확률 0.05 미만 + 연습도 같은 방향
#
#   python -m model.eval.sasrec_test smoke     # 작게 확인 (통계 유저 5,000명 · 연습 1,000명 · 2번 학습)
#   python -m model.eval.sasrec_test           # 전체 (GPU 서버)
#   python -m model.eval.sasrec_test split     # 시간 분리 확인: 학습 · 통계를 정답 기간 전(2020-01 전) 기록만으로
#   python -m model.eval.sasrec_test rolling   # 분기마다 다시 학습: 정답 기간을 분기로 나누고, 분기마다 그 시작 전 기록으로만 학습 · 통계
#     (미래 기록 없음 + 모델은 최대 3개월 묵음 = 서비스에서 주기적으로 다시 학습하는 상황. 크기는 기본 실험에서 고른 128,
#      학습 멈춤은 그 분기 연습 문제로, ③ 의 γ 는 모든 분기 연습 문제를 합쳐서 고름)
#     (기본은 다른 유저의 2022-02 전 기록까지 써서 정답 시점보다 나중 기록이 섞임. 순서 모델은 이걸로 부풀려질 수 있음, Ji 외 2023)
#   → model/data/logs/sasrec.log (진행 상황) · sasrec_result.json · /data/checkpoints/sasrec_best.pt (서버)

import os
for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):   # 서버는 CPU 80개가 보이지만 4개만 씀
    os.environ.setdefault(_k, "4")                                          # → 계산 일꾼을 4개로 (안 맞추면 서로 기다리느라 수십 배 느림)

import json
import pathlib
import sys
import time
from collections import Counter

import numpy as np
import torch
import torch.nn.functional as F

from model.algo import anime_ranker as ar
from model.algo.sasrec import SASRec
from model.eval import next_anime_test as t
from model.eval.new_anime_test import case_hits, paired

LOGS = pathlib.Path(__file__).resolve().parents[1] / "data" / "logs"   # model/data 아래라 커밋 안 됨
LOG = LOGS / "sasrec.log"
RESULT = LOGS / "sasrec_result.json"
CKPT = pathlib.Path("/data/checkpoints/sasrec_best.pt")
MAX_LEN = 200
MIN_COUNT = 5            # 학습 기록에 이보다 적게 나온 작품은 모름 (순서에서 뺌)
DIMS = (64, 128)
MAX_EPOCHS = 60
EVAL_EVERY = 2
PATIENCE = 3             # 연습 점수를 이 횟수만큼 재는 동안 안 오르면 멈춤
GAMMAS = (0.25, 0.5, 1.0, 2.0, 4.0)
BATCH, LR = 128, 1e-3
TOP = 500                # 추천 규칙으로 거르기 전에 SASRec 에서 꺼낼 작품 수
SEED = 0


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def before(data, cut):
    # 시간 분리 확인용 통계 기록: 모든 유저의 cut 전 기록만 (시험 유저도 정답 기간 전 기록만)
    u = data["user"]
    starts = np.r_[0, np.flatnonzero(np.diff(u)) + 1]
    out = []
    for a, b in zip(starts, np.r_[starts[1:], len(u)]):
        m = data["t"][a:b] < cut
        if m.any():
            out.append(t.History(*(data[k][a:b][m] for k in ("t", "anime", "status", "score", "fav"))))
    return out


PERIODS = ((2020, 1), (2020, 4), (2020, 7), (2020, 10), (2021, 1), (2021, 4), (2021, 7), (2021, 10), (2022, 1))


def rolling():
    global LOG, RESULT
    LOG, RESULT = LOGS / "sasrec_rolling.log", LOGS / "sasrec_rolling_result.json"
    from datetime import datetime
    t0 = time.time()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.set_num_threads(int(os.environ["OMP_NUM_THREADS"]))
    log("=== SASRec 분기마다 다시 학습 (분기 시작 전 기록만으로 학습 · 통계, 크기 128) ===")
    anime, data = t.load_anime(), t.load_interactions()
    cases = t.make_cases(data)
    anilist, series = t.load_anilist(), t.load_series(anime)
    tune_ids = {id(c["history"]) for c in cases[: t.TUNE_USERS]}
    bounds = [ar.to_seconds(datetime(y, m, 1)) for y, m in PERIODS] + [t.TARGET_BEFORE]
    names = ("① v0.5", "② SASRec 혼자")
    pool = {lab: {k: [] for k in names + tuple(f"③ γ {g}" for g in GAMMAS)} for lab in ("연습", "실전")}
    seen_all = {"연습": [], "실전": []}
    for (y, m), lo, hi in zip(PERIODS, bounds, bounds[1:]):
        part = [c for c in cases if lo <= c["t"] < hi]
        tune = [c for c in part if id(c["history"]) in tune_ids]
        test = [c for c in part if id(c["history"]) not in tune_ids]
        stats = before(data, lo)
        ctx = t.build_context(cases, anime, series, anilist, data=data, stats=stats)
        ar.build_cowatch(ctx, stats)
        ar.build_ease(ctx)
        rng = np.random.default_rng(SEED)
        seqs = [watched_seq(h, rng) for h in stats]
        cnt = Counter(a for s in seqs for a in s.tolist())
        vocab = np.array(sorted(a for a, c in cnt.items() if c >= MIN_COUNT), dtype=np.int64)
        train_x = encode(seqs, vocab, MAX_LEN + 1)
        train_x = train_x[(train_x > 0).sum(1) >= 2]
        tune_x = encode([watched_seq(c["history"], rng) for c in tune], vocab, MAX_LEN)
        test_x = encode([watched_seq(c["history"], rng) for c in test], vocab, MAX_LEN)
        vrow = ar.rows_of(ctx, vocab)
        log(f"[{y}-{m:02d} 분기] 연습 {len(tune):,} · 실전 {len(test):,}문제 · 학습 순서 {len(train_x):,} · 아는 작품 {len(vocab):,}")
        model, hit, sec = train(128, train_x, tune_x, tune, vocab, ctx, device, MAX_EPOCHS, f"{y}-{m:02d}")
        for lab, cs, x in (("연습", tune, tune_x), ("실전", test, test_x)):
            _, pr = predict(model, x, device, full=True)
            cache = {}
            pool[lab]["① v0.5"].append(case_hits(cs, ctx, ar.next_auto))
            pool[lab]["② SASRec 혼자"].append(case_hits(cs, ctx, ranker(model, x, cs, vocab, device)))
            for g in GAMMAS:
                pool[lab][f"③ γ {g}"].append(case_hits(cs, ctx, with_sasrec(g, pr, cs, vrow, cache)))
            seen_all[lab].append(kinds(cs, ctx))
        log(f"[{y}-{m:02d} 분기] 끝: 학습 {sec / 60:.1f}분 · 연습 SASRec Hit@10 {hit:.4f} · 누적 {(time.time() - t0) / 60:.0f}분")
    hs = {lab: {k: np.vstack(v) for k, v in d.items()} for lab, d in pool.items()}
    gamma = max(GAMMAS, key=lambda g: (hs["연습"][f"③ γ {g}"][:, 0].mean(), -abs(g - ar.WARM["gamma"])))
    log("③ γ (모든 분기 연습 합쳐서): " + " · ".join(f"{g}: {hs['연습'][f'③ γ {g}'][:, 0].mean():.4f}" for g in GAMMAS) + f" → {gamma}")
    head = "방법\t시리즈 Hit@10\tNDCG@10\t보던 시리즈\t새 시리즈"
    result = {"gamma": gamma}
    for lab in ("연습", "실전"):
        seen = np.concatenate(seen_all[lab])
        show = {"① v0.5": hs[lab]["① v0.5"], "② SASRec 혼자": hs[lab]["② SASRec 혼자"],
                "③ v0.5 + SASRec (EASE 대신)": hs[lab][f"③ γ {gamma}"]}
        log(f"[{lab} {len(seen):,}문제 · 분기마다 다시 학습]\n{head}\n" + "\n".join(line(k, h, seen) for k, h in show.items()))
        for k in list(show)[1:]:
            log(f"  {k} vs ① v0.5: {paired(show['① v0.5'], show[k])}")
        result[lab] = {k: {"hit": float(h[:, 0].mean()), "ndcg": float(h[:, 1].mean()),
                           "seen": float(h[seen, 0].mean()), "new": float(h[~seen, 0].mean())} for k, h in show.items()}
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"전체 {(time.time() - t0) / 60:.0f}분 · 끝")


def watched_seq(h, rng):
    # 본 작품을 시간 순으로. 같은 시각끼리는 무작위 순서
    m = np.flatnonzero(np.isin(h.status, ar.WATCHED))
    m = m[rng.permutation(len(m))]
    return h.anime[m[np.argsort(h.t[m], kind="stable")]]


def encode(seqs, vocab, length):
    # 작품 번호 순서들 → (N, length) 텐서. 모르는 작품은 빼고, 최근 length 편을 오른쪽에 붙임 (왼쪽은 빈칸 0)
    out = np.zeros((len(seqs), length), dtype=np.int64)
    for i, s in enumerate(seqs):
        r = np.searchsorted(vocab, s)
        ok = r < len(vocab)
        ok[ok] = vocab[r[ok]] == s[ok]
        s = r[ok][-length:] + 1
        if len(s):
            out[i, length - len(s):] = s
    return torch.from_numpy(out)


def train(dim, train_x, tune_x, tune_cases, vocab, ctx, device, epochs, label):
    """연습 점수로 멈출 때까지 학습 → (가장 좋았던 모델, 연습 Hit@10, 걸린 초)"""
    torch.manual_seed(SEED)
    model = SASRec(len(vocab), dim=dim, max_len=MAX_LEN).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")
    best, best_state, bad, t0 = -1.0, None, 0, time.time()
    for ep in range(1, epochs + 1):
        model.train()
        t1, total, n = time.time(), 0.0, 0
        for idx in torch.randperm(len(train_x)).split(BATCH):
            s = train_x[idx].to(device, non_blocking=True)
            x, y = s[:, :-1], s[:, 1:]
            with torch.autocast(device.type, dtype=torch.float16, enabled=device.type == "cuda"):
                h = model(x)[y > 0]
                loss = F.cross_entropy(model.logits(h).float(), y[y > 0] - 1)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            total, n = total + loss.item(), n + 1
        msg = f"{label} 크기 {dim} · {ep}번째 학습 · 손실 {total / n:.4f} · {time.time() - t1:.0f}초"
        if ep % EVAL_EVERY == 0 or ep == epochs:
            hit = case_hits(tune_cases, ctx, ranker(model, tune_x, tune_cases, vocab, device))[:, 0].mean()
            if hit > best:
                best, best_state, bad = hit, {k: v.detach().clone() for k, v in model.state_dict().items()}, 0
            else:
                bad += 1
            msg += f" · 연습 Hit@10 {hit:.4f} (최고 {best:.4f})"
            if bad >= PATIENCE:
                log(msg + " → 더 안 올라서 멈춤")
                break
        log(msg)
    model.load_state_dict(best_state)
    return model, best, time.time() - t0


@torch.no_grad()
def predict(model, x, device, full=False):
    # 마지막 자리 점수 → 상위 TOP 작품 (번호 배열) · full 이면 전체 작품 확률도 (float16)
    model.eval()
    tops, probs = [], []
    for b in x.split(512):
        with torch.autocast(device.type, dtype=torch.float16, enabled=device.type == "cuda"):
            lg = model.logits(model(b.to(device))[:, -1]).float()
        tops.append(torch.topk(lg, TOP).indices.cpu())
        if full:
            probs.append(torch.softmax(lg, -1).half().cpu())
    return torch.cat(tops).numpy(), (torch.cat(probs).numpy() if full else None)


def ranker(model, x, cases, vocab, device):
    # ② SASRec 혼자: 상위 TOP 작품 순서 그대로 (추천 규칙은 case_hits 의 pick_top 에서)
    top, _ = predict(model, x, device)
    table = {id(c["history"]): vocab[row].tolist() for c, row in zip(cases, top)}
    return lambda user, ctx: table[id(user["history"])]


def with_sasrec(gamma, probs, cases, vrow, cache=None):
    # ③ v0.5 기록 많은 유저 설정에서 EASE 대신 SASRec 확률 (그 유저 1등이 1). 기록 적은 유저는 v0.5 그대로
    # cache: γ 만 바꿔 여러 번 잴 때 취향 · 인기 점수를 한 번만 계산하려고 (유저별 다음 편 · 후보 · 점수 두 개)
    table = {id(c["history"]): p for c, p in zip(cases, probs)}
    cache = {} if cache is None else cache
    w = ar.WARM

    def run(user, ctx):
        if int(np.isin(user["history"].status, ar.WATCHED).sum()) <= ar.COLD_MAX_WATCHED:
            return ar.next_auto(user, ctx)
        key = id(user["history"])
        if key not in cache:
            p = table[key].astype(np.float64)
            c = np.zeros(len(ctx["ids"]))
            c[vrow] = p / p.max()
            base = ar.genre_scores(user, ctx) + w["beta"] * ar.pop_scores(user, ctx, w["kind"])
            rows = np.unique(np.r_[ar.pop_candidates(user, ctx, w["kind"]), ctx["cw_rows"]])
            cache[key] = (ar.next_picks(user, ctx), rows, base[rows], c[rows])
        picks, rows, base, c = cache[key]
        return picks + ar.rank_rows(rows, ctx, -(base + gamma * c))
    return run


def kinds(cases, ctx):
    # 정답이 보던 시리즈 (이미 본 시리즈의 다른 작품) 인지
    ser = lambda a: ctx["series"].get(a, a)
    return np.array([ser(c["target"]) in {ser(a) for a in c["history"].anime.tolist()} for c in cases])


def line(name, h, seen):
    return (f"{name}\t{h[:, 0].mean():.4f}\t{h[:, 1].mean():.4f}\t{h[seen, 0].mean():.4f}\t{h[~seen, 0].mean():.4f}")


def main(smoke=False, split=False):
    global LOG, RESULT, CKPT
    if split:
        LOG, RESULT, CKPT = LOGS / "sasrec_split.log", LOGS / "sasrec_split_result.json", CKPT.with_name("sasrec_split.pt")
    t0 = time.time()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.set_num_threads(int(os.environ["OMP_NUM_THREADS"]))
    rng = np.random.default_rng(SEED)
    log(f"=== SASRec 실험 {'(작게 확인)' if smoke else ''}{'(시간 분리: 2020-01 전 기록만으로 학습 · 통계)' if split else ''} · 장치 {torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU'} ===")
    anime, data = t.load_anime(), t.load_interactions()
    cases = t.make_cases(data)
    anilist = t.load_anilist()
    stats = before(data, t.TARGET_AFTER) if split else t.stats_histories(data, cases)
    ctx = t.build_context(cases, anime, t.load_series(anime), anilist, data=data, stats=stats)
    ar.build_cowatch(ctx, stats)
    ar.build_ease(ctx)
    tune, test = cases[: t.TUNE_USERS], cases[t.TUNE_USERS:]
    if smoke:
        stats, tune, test = stats[:5000], tune[:1000], test[:1000]
    seqs = [watched_seq(h, rng) for h in stats]
    cnt = Counter(a for s in seqs for a in s.tolist())
    vocab = np.array(sorted(a for a, c in cnt.items() if c >= MIN_COUNT), dtype=np.int64)
    train_x = encode(seqs, vocab, MAX_LEN + 1)
    train_x = train_x[(train_x > 0).sum(1) >= 2]
    tune_x = encode([watched_seq(c["history"], rng) for c in tune], vocab, MAX_LEN)
    test_x = encode([watched_seq(c["history"], rng) for c in test], vocab, MAX_LEN)
    vrow = ar.rows_of(ctx, vocab)
    assert len(vrow) == len(vocab)
    log(f"준비 {time.time() - t0:.0f}초 · 학습 순서 {len(train_x):,}개 · 아는 작품 {len(vocab):,}편 · 연습 {len(tune):,} · 실전 {len(test):,}")

    # 1) 모델 크기 고르기 (연습)
    epochs = 2 if smoke else MAX_EPOCHS
    runs = {}
    for dim in ((32,) if smoke else DIMS):
        model, hit, sec = train(dim, train_x, tune_x, tune, vocab, ctx, device, epochs, "①")
        runs[dim] = (model, hit, sec)
        log(f"크기 {dim} 끝: 연습 Hit@10 {hit:.4f} · 학습 {sec / 60:.1f}분")
    dim = max(runs, key=lambda d: (runs[d][1], -d))
    model = runs[dim][0]
    log(f"→ 고른 크기 {dim}")
    if not smoke:
        CKPT.parent.mkdir(parents=True, exist_ok=True)
        torch.save({"state": model.state_dict(), "vocab": vocab, "dim": dim, "max_len": MAX_LEN}, CKPT)

    # 2) ③ 의 비중 γ 고르기 (연습)
    _, tune_p = predict(model, tune_x, device, full=True)
    scores, tune_cache = {}, {}
    for g in GAMMAS:
        scores[g] = case_hits(tune, ctx, with_sasrec(g, tune_p, tune, vrow, tune_cache))[:, 0].mean()
        log(f"③ γ {g}: 연습 Hit@10 {scores[g]:.4f}")
    gamma = max(GAMMAS, key=lambda g: (scores[g], -abs(g - ar.WARM["gamma"])))
    log(f"→ 고른 γ {gamma}")

    # 3) 연습 · 실전 비교 (짝 비교는 ① 기준)
    t2 = time.time()
    _, test_p = predict(model, test_x, device, full=True)
    per_user = (time.time() - t2) / len(test) * 1000
    result = {"dim": dim, "gamma": gamma, "vocab": len(vocab), "train_minutes": {d: runs[d][2] / 60 for d in runs},
              "infer_ms_per_user": per_user}
    head = "방법\t시리즈 Hit@10\tNDCG@10\t보던 시리즈\t새 시리즈"
    for label, part, x, p, cache in (("연습", tune, tune_x, tune_p, tune_cache), ("실전", test, test_x, test_p, None)):
        seen = kinds(part, ctx)
        hs = {"① v0.5": case_hits(part, ctx, ar.next_auto),
              "② SASRec 혼자": case_hits(part, ctx, ranker(model, x, part, vocab, device)),
              "③ v0.5 + SASRec (EASE 대신)": case_hits(part, ctx, with_sasrec(gamma, p, part, vrow, cache))}
        log(f"[{label} {len(part):,}문제]\n{head}\n" + "\n".join(line(k, h, seen) for k, h in hs.items()))
        for k in list(hs)[1:]:
            log(f"  {k} vs ① v0.5: {paired(hs['① v0.5'], hs[k])}")
        result[label] = {k: {"hit": float(h[:, 0].mean()), "ndcg": float(h[:, 1].mean()),
                             "seen": float(h[seen, 0].mean()), "new": float(h[~seen, 0].mean())} for k, h in hs.items()}
    log(f"추천 1명당 SASRec 계산 {per_user:.2f}ms · 전체 {(time.time() - t0) / 60:.0f}분 · 끝")
    if not smoke:
        RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:2] == ["rolling"]:
        rolling()
    else:
        main(smoke=sys.argv[1:2] == ["smoke"], split=sys.argv[1:2] == ["split"])
