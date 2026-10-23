# model_bench — 추천 모델 비교표 (EXPERIMENTS EXP-32). 같은 MAL 시험지 · 같은 후보 작품 · 같은 입력 · 같은 추천 규칙
#
# 모델 (계열별 대표): 인기순 · 태그 취향 · 아이템 CF · RP3β · ALS · EASE · Mult-VAE · LightGCN · GRU4Rec · SASRec
# 재는 법: ① 혼자 = 그 모델 점수만으로 10개 (본 작품 · 성인 · 방영 전 제외, 시리즈 1개)
#          ② v0.5 안에 넣기 = v0.5 의 같이 본 작품 자리(기록 많은 유저 EASE, 기록 적은 유저 아이템 CF)를 그 모델 점수로.
#             점수는 유저마다 0~1 (최소 0 · 최대 1)로 맞추고 비중 γ 는 연습으로 고름
# 장면: 기록 전체 · 신규 유저 (작품 1개 + 장르 3 + 테마 3)
# 공통: 후보 작품 = 인기 상위 3,000편 (v0.5 EASE 범위) · 입력 = 본 작품(다 봄 · 보는 중) · 학습 = v0.5 통계 기록
#       (시험 유저는 정답 전 기록만) · 조정값은 연습 유저 "기록 전체 · 혼자" 점수로만 고름
# 채택 기준 (미리 정함): ② 가 v0.5 보다 실전 Hit@10 · NDCG 둘 다 ▲ + 우연일 확률 0.05 미만 + 연습도 같은 방향.
#   순서 모델(GRU4Rec · SASRec)은 서비스에 시청 순서가 없어 채택 대상 아님 (기록만, EXP-31)
#
#   python -m model.eval.model_bench smoke     # 작게 확인
#   python -m model.eval.model_bench           # 전체 (GPU 서버) → model/data/logs/bench.log · bench_result.json
#   python -m model.eval.model_bench gcnseeds  # EXP-34: LightGCN 2층을 씨앗만 바꿔 다시 학습 → gcn_seeds.log (smoke 같이 주면 작게)

import os
for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):   # 서버는 CPU 4개 할당 (EXP-31 환경 참고)
    os.environ.setdefault(_k, "4")

import json
import math
import pathlib
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn

from model.algo import anime_ranker as ar
from model.algo.sasrec import SASRec
from model.eval import next_anime_test as t
from model.eval.new_anime_test import case_hits

LOGS = pathlib.Path(__file__).resolve().parents[1] / "data" / "logs"
LOG, RESULT = LOGS / "bench.log", LOGS / "bench_result.json"
SEED = 0
TOP = 300                    # 추천 규칙으로 거르기 전에 꺼낼 작품 수
GAMMAS = (0.25, 0.5, 1.0, 2.0, 4.0)
EASE_LAMBDAS = (100, 250, 500, 1000, 2000)
RP3_GRID = [(a, b) for a in (0.5, 1.0) for b in (0.0, 0.3, 0.6)]
ALS_ALPHAS = (5.0, 10.0, 20.0)
SEQ_DIMS = (64, 128)
GCN_LAYERS = (2, 3)
GCN_SEEDS = (1, 2, 3)        # EXP-34 다시 학습 씨앗 (EXP-32 는 SEED 0)
MAX_LEN = 200
EVAL_EVERY, PATIENCE = 2, 3  # 딥러닝: 2번 학습마다 연습 점수, 3번 재는 동안 안 오르면 멈춤
MAX_EPOCHS = {"vae": 100, "gcn": 150, "seq": 60}
GCN_EVAL_EVERY = 5           # LightGCN 은 원래 수백 번 학습하는 모델이라 5번마다 재고 15번 동안 안 오르면 멈춤
VAE_ANNEAL_EPOCHS = 20       # Mult-VAE KL 비중을 0.2까지 올리는 기간 (논문은 20만 단계. 우리 데이터 크기에 맞춰 20번 학습 동안)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ## 1. 준비: 시험지 · 입력
class Bench:
    def __init__(self, smoke=False):
        self.dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        torch.set_num_threads(int(os.environ["OMP_NUM_THREADS"]))
        anime, data = t.load_anime(), t.load_interactions()
        cases = t.make_cases(data)
        anilist = t.load_anilist()
        stats = t.stats_histories(data, cases)
        ctx = t.build_context(cases, anime, t.load_series(anime), anilist, data=data, stats=stats)
        ar.build_cowatch(ctx, stats)
        ar.build_ease(ctx)
        if smoke:
            cases = cases[:600] + cases[t.TUNE_USERS:t.TUNE_USERS + 600]
            keep = {id(c["history"]) for c in cases}
            stats = [h for h in stats if id(h) in keep] + [h for h in stats if id(h) not in keep][:4000]
            self.n_tune = 600
        else:
            self.n_tune = t.TUNE_USERS
        self.ctx, self.anime, self.stats = ctx, anime, stats
        self.U = ctx["cw_rows"]                                   # 후보 작품 줄 (인기 상위 3,000편)
        self.n = len(self.U)
        self.scenes = {"기록 전체": cases, "신규 유저": [t.onboarding(c, anime, 1, 3, anilist, 3) for c in cases]}
        rng = np.random.default_rng(SEED)
        self.train_pos = [self.positions(h) for h in stats]       # 학습 유저마다 후보 작품 위치 (본 작품)
        self.train_seq = [self.sequence(h, rng) for h in stats]
        self.inputs = {k: [self.positions(c["history"]) for c in cs] for k, cs in self.scenes.items()}
        self.seqs = {k: [self.sequence(c["history"], rng) for c in cs] for k, cs in self.scenes.items()}
        self.user_of = {id(h): i for i, h in enumerate(stats)}    # 시험 유저 → 학습 유저 번호 (LightGCN 학습된 유저 벡터용)
        self.seen = {k: self.kinds(cs) for k, cs in self.scenes.items()}
        self.base = {k: self.dropin_base(cs) for k, cs in self.scenes.items()}
        self.v05 = {k: case_hits(cs, ctx, ar.next_auto) for k, cs in self.scenes.items()}

    def positions(self, h):
        p = self.ctx["cw_pos"][ar.rows_of(self.ctx, h.anime[np.isin(h.status, ar.WATCHED)])]
        return np.unique(p[p >= 0])

    def sequence(self, h, rng):
        # 본 작품을 시간 순으로 (같은 시각끼리는 무작위) → 후보 작품 위치 + 1 (0 = 빈칸)
        m = np.flatnonzero(np.isin(h.status, ar.WATCHED))
        m = m[rng.permutation(len(m))]
        m = m[np.argsort(h.t[m], kind="stable")]
        p = self.ctx["cw_pos"][ar.rows_of(self.ctx, h.anime[m])]
        return p[p >= 0] + 1

    def kinds(self, cs):
        ser = lambda a: self.ctx["series"].get(a, a)
        return np.array([ser(c["target"]) in {ser(a) for a in c["history"].anime.tolist()} for c in cs])

    def matrix(self, lists):
        # 위치 목록들 → (N, 3,000) 0/1 행렬 (GPU)
        x = torch.zeros(len(lists), self.n, device=self.dev)
        rows = np.repeat(np.arange(len(lists)), [len(p) for p in lists])
        if len(rows):
            x[torch.from_numpy(rows).to(self.dev), torch.from_numpy(np.concatenate(lists)).to(self.dev)] = 1
        return x

    def seq_tensor(self, seqs, length):
        out = np.zeros((len(seqs), length), dtype=np.int64)
        for i, s in enumerate(seqs):
            s = s[-length:]
            if len(s):
                out[i, length - len(s):] = s
        return torch.from_numpy(out)

    # ## 2. 채점
    def hits(self, scene, scores, part):
        # scores (사례 수, 3,000) → 사례마다 점수 순 작품 → 추천 규칙 → (맞힘, NDCG). part: 연습 / 실전 / 전체
        cs = self.scenes[scene]
        idx = self.part(scene, part)
        top = torch.topk(torch.as_tensor(scores[idx], device=self.dev, dtype=torch.float32), TOP, dim=1).indices.cpu().numpy()
        ids = self.ctx["ids"][self.U]
        table = {id(cs[i]["history"]): ids[row].tolist() for i, row in zip(idx, top)}
        return case_hits([cs[i] for i in idx], self.ctx, lambda user, ctx: table[id(user["history"])])

    def part(self, scene, part):
        n = len(self.scenes[scene])
        return {"연습": np.arange(self.n_tune), "실전": np.arange(self.n_tune, n), "전체": np.arange(n)}[part]

    def dropin_base(self, cs):
        # v0.5 에서 같이 본 작품 부분만 뺀 점수 (유저마다 설정 자동: 기록 적으면 신규, 많으면 기존). 다음 편 · 후보 줄과 같이 저장
        out = []
        for c in cs:
            user = {"history": c["history"], "t": c["t"], **{k: c[k] for k in ("genres", "themes") if k in c}}
            n = int(np.isin(c["history"].status, ar.WATCHED).sum())
            cfg = ar.COLD if n <= ar.COLD_MAX_WATCHED else ar.WARM
            base = ar.genre_scores(user, self.ctx) + cfg["beta"] * ar.pop_scores(user, self.ctx, cfg["kind"])
            rows = np.unique(np.r_[ar.pop_candidates(user, self.ctx, cfg["kind"]), self.U])
            if cfg.get("trend"):
                base = base + cfg["trend"] * ar.pop_scores(user, self.ctx, "recent")
                rows = np.unique(np.r_[rows, ar.pop_candidates(user, self.ctx, "recent")])
            out.append((ar.next_picks(user, self.ctx), rows, base[rows].astype(np.float32), self.ctx["cw_pos"][rows]))
        return out

    def dropin(self, scene, scores, gamma, part):
        # v0.5 의 같이 본 작품 자리를 scores (유저마다 0~1)로
        cs, base = self.scenes[scene], self.base[scene]
        idx = self.part(scene, part)
        table = {}
        for i in idx:
            picks, rows, b, upos = base[i]
            c = np.where(upos >= 0, scores[i][np.maximum(upos, 0)], 0.0)
            table[id(cs[i]["history"])] = picks + ar.rank_rows(rows, self.ctx, -(b + gamma * c))
        return case_hits([cs[i] for i in idx], self.ctx, lambda user, ctx: table[id(user["history"])])


def unit(s):
    # 유저마다 0~1 (최소 0 · 최대 1). 모든 모델에 같은 방식
    s = np.asarray(s, dtype=np.float32)
    lo, hi = s.min(axis=1, keepdims=True), s.max(axis=1, keepdims=True)
    return (s - lo) / np.maximum(hi - lo, 1e-12)


def pstats(h0, h1):
    # 짝 비교 (new_anime_test.paired 와 같은 계산) → (%p 차이, 우연일 확률, NDCG 차이, 우연일 확률)
    a, b = h0[:, 0] > 0, h1[:, 0] > 0
    gain, loss, n = int((b & ~a).sum()), int((a & ~b).sum()), len(a)
    z = (abs(gain - loss) - 1) / math.sqrt(gain + loss) if gain + loss else 0.0
    d = h1[:, 1] - h0[:, 1]
    zn = d.mean() / (d.std(ddof=1) / math.sqrt(n)) if d.std() > 0 else 0.0
    return (gain - loss) / n * 100, math.erfc(max(z, 0) / math.sqrt(2)), float(d.mean()), math.erfc(abs(zn) / math.sqrt(2))


# ## 3. 모델 (점수 함수: 장면 → (사례 수, 3,000) 점수)
def item_item(bench, W, scene, how="sum"):
    x = bench.matrix(bench.inputs[scene])
    W = torch.as_tensor(W, device=bench.dev, dtype=torch.float32)
    if how == "max":                                             # 본 작품 줄 중 가장 큰 값 (v0.5 신규 유저 방식)
        out = torch.zeros(len(x), bench.n, device=bench.dev)
        for i, p in enumerate(bench.inputs[scene]):
            if len(p):
                out[i] = W[torch.from_numpy(p).to(bench.dev)].max(0).values
        return out.cpu().numpy()
    return (x @ W).cpu().numpy()


def ease_weights(bench, lam):
    P = np.linalg.inv(bench.ctx["cw_count"].astype(np.float64) + lam * np.eye(bench.n))
    B = -P / np.diag(P)[None, :]
    np.fill_diagonal(B, 0)
    return B


def rp3_weights(bench, alpha, beta, topk=200):
    # RP3β (Paudel 외 2017): 유저 → 작품 → 유저 → 작품 걸음 확률 (α 제곱) ÷ 도착 작품 인기^β. 작품마다 상위 topk 이웃만
    X = bench.matrix(bench.train_pos)
    Pui = (X / X.sum(1, keepdim=True).clamp(min=1)) ** alpha
    Piu = (X.T / X.T.sum(1, keepdim=True).clamp(min=1)) ** alpha
    W = Piu @ Pui
    W = W / X.sum(0).clamp(min=1)[None, :] ** beta
    W.fill_diagonal_(0)
    v, i = torch.topk(W, topk, dim=1)
    out = torch.zeros_like(W).scatter_(1, i, v)
    return out.cpu().numpy()


def als_scores(bench, alpha, scene):
    ctx = dict(bench.ctx)
    ar.build_als(ctx, bench.stats, alpha=alpha)
    out = np.zeros((len(bench.scenes[scene]), bench.n), dtype=np.float32)
    Y, YtY = ctx["als_Y"], ctx["als_YtY"]
    eye = np.eye(Y.shape[1])
    for i, p in enumerate(bench.inputs[scene]):
        if len(p):
            Yi = Y[p]
            out[i] = Y @ np.linalg.solve(YtY + alpha * (Yi.T @ Yi) + ctx["als_reg"] * eye, (1 + alpha) * Yi.sum(axis=0))
    return out, ctx


class MultVAE(nn.Module):
    # Mult-VAE (Liang 외 2018): 본 작품 → 600 → 잠재 200 → 600 → 작품마다 점수. 입력에 드롭아웃 0.5
    def __init__(self, n, hidden=600, latent=200, dropout=0.5):
        super().__init__()
        self.enc, self.mu, self.lv = nn.Linear(n, hidden), nn.Linear(hidden, latent), nn.Linear(hidden, latent)
        self.dec1, self.dec2 = nn.Linear(latent, hidden), nn.Linear(hidden, n)
        self.drop = nn.Dropout(dropout)

    def forward(self, x):
        h = torch.tanh(self.enc(self.drop(F.normalize(x, dim=1))))
        mu, lv = self.mu(h), self.lv(h)
        z = mu + torch.randn_like(mu) * torch.exp(0.5 * lv) if self.training else mu
        return self.dec2(torch.tanh(self.dec1(z))), mu, lv


class LightGCN(nn.Module):
    # LightGCN (He 외 2020): 유저 · 작품 그래프에서 이웃 평균을 층마다 전달, 층 평균이 최종 벡터
    def __init__(self, n_users, n_items, adj, layers, dim=64):
        super().__init__()
        self.nu, self.ni, self.adj, self.layers = n_users, n_items, adj, layers
        self.emb = nn.Embedding(n_users + n_items, dim)
        nn.init.normal_(self.emb.weight, std=0.1)

    def propagate(self):
        e, out = self.emb.weight, [self.emb.weight]
        for _ in range(self.layers):
            e = torch.sparse.mm(self.adj, e)
            out.append(e)
        layers = torch.stack(out)                               # (층 + 1, 노드, dim)
        return layers.mean(0), layers


class GRU4Rec(nn.Module):
    # GRU4Rec (Hidasi 외 2016) 방식: 본 작품 순서를 GRU 로 읽고 자리마다 다음 작품 점수 (SASRec 과 같은 학습)
    def __init__(self, n_items, dim):
        super().__init__()
        self.item = nn.Embedding(n_items + 1, dim, padding_idx=0)
        nn.init.normal_(self.item.weight, std=dim ** -0.5)
        with torch.no_grad():
            self.item.weight[0].zero_()
        self.gru = nn.GRU(dim, dim, batch_first=True)
        self.norm = nn.LayerNorm(dim)

    def forward(self, seq):
        return self.norm(self.gru(self.item(seq))[0])

    def logits(self, h):
        return h @ self.item.weight[1:].T


def early_stop(step, evaluate, max_epochs, label, every=EVAL_EVERY):
    # step(epoch) → 손실, evaluate() → 연습 Hit@10. 가장 좋았던 상태를 돌려줌
    best, best_state, bad, t0 = -1.0, None, 0, time.time()
    for ep in range(1, max_epochs + 1):
        t1 = time.time()
        loss = step(ep)
        msg = f"{label} · {ep}번째 학습 · 손실 {loss:.4f} · {time.time() - t1:.0f}초"
        if ep % every == 0 or ep == max_epochs:
            hit, state = evaluate()
            if hit > best:
                best, best_state, bad = hit, state, 0
            else:
                bad += 1
            msg += f" · 연습 Hit@10 {hit:.4f} (최고 {best:.4f})"
            if bad >= PATIENCE:
                log(msg + " → 더 안 올라서 멈춤")
                break
        log(msg)
    return best, best_state, time.time() - t0


def snapshot(model):
    return {k: v.detach().clone() for k, v in model.state_dict().items()}


def train_vae(bench, epochs):
    torch.manual_seed(SEED)
    X = bench.matrix(bench.train_pos)
    model = MultVAE(bench.n).to(bench.dev)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    steps = [0]
    per_epoch = math.ceil(len(X) / 500)

    def step(ep):
        model.train()
        total, n = 0.0, 0
        for idx in torch.randperm(len(X), device=bench.dev).split(500):
            x = X[idx]
            logits, mu, lv = model(x)
            anneal = min(0.2, 0.2 * steps[0] / (VAE_ANNEAL_EPOCHS * per_epoch))   # KL 비중을 0.2까지 천천히
            kl = -0.5 * torch.mean(torch.sum(1 + lv - mu ** 2 - lv.exp(), dim=1))
            loss = -(F.log_softmax(logits, 1) * x).sum(1).mean() + anneal * kl
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            steps[0] += 1
            total, n = total + loss.item(), n + 1
        return total / n

    def evaluate():
        s = vae_scores(bench, model, "기록 전체", tune_only=True)
        return bench.hits("기록 전체", s, "연습")[:, 0].mean(), snapshot(model)

    best, state, sec = early_stop(step, evaluate, epochs, "Mult-VAE")
    model.load_state_dict(state)
    return model, best, sec


@torch.no_grad()
def vae_scores(bench, model, scene, tune_only=False):
    model.eval()
    lists = bench.inputs[scene][: bench.n_tune] if tune_only else bench.inputs[scene]
    out = [model(bench.matrix(lists[i:i + 2000]))[0].cpu() for i in range(0, len(lists), 2000)]
    s = torch.cat(out).numpy()
    if tune_only:                                                 # 연습 줄만 계산했어도 hits 는 사례 번호로 찾으니 크기를 맞춤
        full = np.zeros((len(bench.inputs[scene]), bench.n), dtype=np.float32)
        full[: len(s)] = s
        return full
    return s


def train_gcn(bench, layers, epochs, seed=SEED):
    torch.manual_seed(seed)
    nu, ni = len(bench.train_pos), bench.n
    u = np.repeat(np.arange(nu), [len(p) for p in bench.train_pos])
    i = np.concatenate(bench.train_pos)
    deg_u = np.bincount(u, minlength=nu).astype(np.float64)
    deg_i = np.bincount(i, minlength=ni).astype(np.float64)
    w = 1 / np.sqrt(np.maximum(deg_u[u], 1) * np.maximum(deg_i[i], 1))
    idx = np.r_[np.c_[u, nu + i], np.c_[nu + i, u]].T
    adj = torch.sparse_coo_tensor(torch.from_numpy(idx), torch.from_numpy(np.r_[w, w]).float(),
                                  (nu + ni, nu + ni)).coalesce().to(bench.dev)
    model = LightGCN(nu, ni, adj, layers).to(bench.dev)
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    U_t, I_t = torch.from_numpy(u).to(bench.dev), torch.from_numpy(i).to(bench.dev)
    X = bench.matrix(bench.train_pos).bool()

    def step(ep):
        model.train()
        total, n = 0.0, 0
        for b in torch.randperm(len(U_t), device=bench.dev).split(16384):
            uu, ii = U_t[b], I_t[b]
            jj = torch.randint(0, ni, (len(b),), device=bench.dev)  # 안 본 작품 하나 (본 작품이면 다시 뽑기 한 번)
            redo = X[uu, jj]
            jj[redo] = torch.randint(0, ni, (int(redo.sum()),), device=bench.dev)
            final, _ = model.propagate()
            eu, ei, ej = final[uu], final[nu + ii], final[nu + jj]
            loss = -F.logsigmoid((eu * ei).sum(1) - (eu * ej).sum(1)).mean()
            e0 = model.emb.weight
            loss = loss + 1e-4 * (e0[uu].pow(2).sum() + e0[nu + ii].pow(2).sum() + e0[nu + jj].pow(2).sum()) / len(b)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            total, n = total + loss.item(), n + 1
        return total / n

    def evaluate():
        s = gcn_scores(bench, model, "기록 전체")
        return bench.hits("기록 전체", s, "연습")[:, 0].mean(), snapshot(model)

    label = f"LightGCN {layers}층" + (f" · 씨앗 {seed}" if seed != SEED else "")
    best, state, sec = early_stop(step, evaluate, epochs, label, GCN_EVAL_EVERY)
    model.load_state_dict(state)
    return model, best, sec, deg_i


@torch.no_grad()
def gcn_scores(bench, model, scene, deg_i=None):
    # 기록 전체: 학습된 유저 벡터 (시험 유저도 정답 전 기록으로 그래프에 있음). 신규 유저: 고른 작품에서 층마다 전달해 유저 벡터를 만듦
    model.eval()
    final, layers = model.propagate()
    nu = model.nu
    items = final[nu:]
    cs = bench.scenes[scene]
    if scene == "기록 전체":
        uid = torch.tensor([bench.user_of[id(c["history"])] for c in cs], device=bench.dev)
        return (final[uid] @ items.T).cpu().numpy()
    deg = torch.as_tensor(np.maximum(deg_i, 1), device=bench.dev, dtype=torch.float32)
    out = np.zeros((len(cs), bench.n), dtype=np.float32)
    for k, p in enumerate(bench.inputs[scene]):
        if not len(p):
            continue
        pt = torch.from_numpy(p).to(bench.dev)
        coef = 1 / torch.sqrt(len(p) * deg[pt])
        eu = sum((coef[:, None] * layers[l, nu + pt]).sum(0) for l in range(model.layers)) / (model.layers + 1)
        out[k] = (eu @ items.T).cpu().numpy()
    return out


def train_seq(bench, kind, dim, epochs):
    torch.manual_seed(SEED)
    model = (SASRec(bench.n, dim=dim, max_len=MAX_LEN) if kind == "SASRec" else GRU4Rec(bench.n, dim)).to(bench.dev)
    train_x = bench.seq_tensor(bench.train_seq, MAX_LEN + 1)
    train_x = train_x[(train_x > 0).sum(1) >= 2]
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    scaler = torch.amp.GradScaler(enabled=bench.dev.type == "cuda")

    def step(ep):
        model.train()
        total, n = 0.0, 0
        for idx in torch.randperm(len(train_x)).split(128):
            s = train_x[idx].to(bench.dev, non_blocking=True)
            x, y = s[:, :-1], s[:, 1:]
            with torch.autocast(bench.dev.type, dtype=torch.float16, enabled=bench.dev.type == "cuda"):
                loss = F.cross_entropy(model.logits(model(x)[y > 0]).float(), y[y > 0] - 1)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            total, n = total + loss.item(), n + 1
        return total / n

    def evaluate():
        s = seq_scores(bench, model, "기록 전체", tune_only=True)
        return bench.hits("기록 전체", s, "연습")[:, 0].mean(), snapshot(model)

    best, state, sec = early_stop(step, evaluate, epochs, f"{kind} 크기 {dim}")
    model.load_state_dict(state)
    return model, best, sec


@torch.no_grad()
def seq_scores(bench, model, scene, tune_only=False):
    model.eval()
    seqs = bench.seqs[scene][: bench.n_tune] if tune_only else bench.seqs[scene]
    x = bench.seq_tensor(seqs, MAX_LEN)
    out = []
    for b in x.split(512):
        with torch.autocast(bench.dev.type, dtype=torch.float16, enabled=bench.dev.type == "cuda"):
            out.append(torch.softmax(model.logits(model(b.to(bench.dev))[:, -1]).float(), -1).cpu())
    s = torch.cat(out).numpy()
    if tune_only:
        full = np.zeros((len(bench.seqs[scene]), bench.n), dtype=np.float32)
        full[: len(s)] = s
        return full
    return s


# ## 4. 실행
def main(smoke=False, mode="all"):
    global LOG, RESULT
    if mode == "gcnseeds":
        name = "gcn_seeds_smoke" if smoke else "gcn_seeds"
        LOG, RESULT = LOGS / f"{name}.log", LOGS / f"{name}_result.json"
    t0 = time.time()
    log(f"=== {'LightGCN 다시 학습 (EXP-34)' if mode == 'gcnseeds' else '모델 비교'} {'(작게 확인)' if smoke else ''} · 장치 {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'} ===")
    bench = Bench(smoke)
    log(f"준비 {(time.time() - t0) / 60:.1f}분 · 학습 유저 {len(bench.stats):,} · 후보 작품 {bench.n:,} · "
        f"문제 {len(bench.scenes['기록 전체']):,} (연습 {bench.n_tune:,})")
    S1, S2 = "기록 전체", "신규 유저"
    tune = lambda s: bench.hits(S1, s, "연습")[:, 0].mean()
    results = {}

    def record(name, family, sc, chosen, sec, note=""):
        # 두 장면 점수가 정해진 모델: 혼자 · v0.5 안에 넣기 (γ 연습으로) 를 재고 기록
        r = {"계열": family, "고른 값": chosen, "학습 초": round(sec, 1), "비고": note}
        for scene in (S1, S2):
            s = sc[scene]
            u = unit(s)
            alone = {p: bench.hits(scene, s, p) for p in ("연습", "실전")}
            g = max(GAMMAS, key=lambda g: (bench.dropin(scene, u, g, "연습")[:, 0].mean(), -g))
            dropin = {p: bench.dropin(scene, u, g, p) for p in ("연습", "실전")}
            seen = bench.seen[scene][bench.part(scene, "실전")]
            ref = {p: bench.v05[scene][bench.part(scene, p)] for p in ("연습", "실전")}
            d = pstats(ref["실전"], dropin["실전"])
            dt = pstats(ref["연습"], dropin["연습"])
            h = alone["실전"]
            r[scene] = {"혼자": {"hit": float(h[:, 0].mean()), "ndcg": float(h[:, 1].mean()),
                                 "seen": float(h[seen, 0].mean()), "new": float(h[~seen, 0].mean()),
                                 "연습 hit": float(alone["연습"][:, 0].mean())},
                        "넣기": {"gamma": g, "hit": float(dropin["실전"][:, 0].mean()), "ndcg": float(dropin["실전"][:, 1].mean()),
                                 "d_pp": d[0], "p_hit": d[1], "d_ndcg": d[2], "p_ndcg": d[3], "연습 d_pp": dt[0], "연습 d_ndcg": dt[2]}}
            log(f"{name} · {scene}: 혼자 실전 Hit@10 {h[:, 0].mean():.4f} NDCG {h[:, 1].mean():.4f} · "
                f"v0.5 에 넣기 (γ {g}) {dropin['실전'][:, 0].mean():.4f} ({d[0]:+.2f}%p, 우연 {d[1]:.3f} · NDCG {d[2]:+.4f}, 우연 {d[3]:.3f} · 연습 {dt[0]:+.2f}%p)")
        results[name] = r
        RESULT.write_text(json.dumps({"v0.5": {sc: {"hit": float(bench.v05[sc][bench.part(sc, '실전')][:, 0].mean()),
                                                     "ndcg": float(bench.v05[sc][bench.part(sc, '실전')][:, 1].mean())}
                                                for sc in (S1, S2)}, "모델": results}, ensure_ascii=False, indent=1),
                          encoding="utf-8")

    for sc in (S1, S2):
        h = bench.v05[sc][bench.part(sc, "실전")]
        log(f"기준 v0.5 (지금) · {sc}: 실전 Hit@10 {h[:, 0].mean():.4f} NDCG {h[:, 1].mean():.4f}")

    if mode == "gcnseeds":
        # EXP-34: 신규 유저에서 v0.5 에 넣기가 기준을 넘은 LightGCN 2층 (EXP-32, 씨앗 0)이 학습 운인지. 설정은 같고 씨앗만 바꿈
        for s in ((1,) if smoke else GCN_SEEDS):
            model, _, sec, deg_i = train_gcn(bench, 2, 2 if smoke else MAX_EPOCHS["gcn"], seed=s)
            record(f"LightGCN 2층 · 씨앗 {s}", "그래프 신경망 (He 외 2020)",
                   {S1: gcn_scores(bench, model, S1), S2: gcn_scores(bench, model, S2, deg_i)}, "2층", sec)
        passed = 0
        for name, r in results.items():
            b = r[S2]["넣기"]
            ok = b["d_pp"] > 0 and b["d_ndcg"] > 0 and b["p_hit"] < 0.05 and b["p_ndcg"] < 0.05 and b["연습 d_pp"] > 0
            passed += ok
            log(f"{name} · 신규 유저 v0.5 에 넣기: {b['d_pp']:+.2f}%p (우연 {b['p_hit']:.3f}) · NDCG {b['d_ndcg']:+.4f} "
                f"(우연 {b['p_ndcg']:.3f}) · 연습 {b['연습 d_pp']:+.2f}%p · NDCG {b['연습 d_ndcg']:+.4f} → {'통과' if ok else '못 넘음'}")
        log(f"기준 통과 {passed}/{len(results)} · 전체 {(time.time() - t0) / 60:.0f}분 · 끝")
        return

    # 학습 없는 모델
    pop = np.tile(bench.ctx["pop_norm"][bench.U].astype(np.float32), (1, 1))
    record("인기순", "기준", {sc: np.repeat(pop, len(bench.scenes[sc]), 0) for sc in (S1, S2)}, "-", 0)
    tag = {}
    for sc in (S1, S2):
        tag[sc] = np.stack([ar.genre_scores({"history": c["history"], "t": c["t"],
                                             **{k: c[k] for k in ("genres", "themes") if k in c}}, bench.ctx)[bench.U]
                            for c in bench.scenes[sc]]).astype(np.float32)
    record("태그 취향", "내용 기반", tag, "-", 0)
    conf = bench.ctx["cw_sim"]["confidence"]
    record("아이템 CF", "이웃 기반 (같이 본 작품 신뢰도)",
           {S1: item_item(bench, conf, S1, "sum"), S2: item_item(bench, conf, S2, "max")}, "기록 전체 합 · 신규 유저 최대", 0)

    # EASE: λ 고르기
    t1 = time.time()
    best = max(EASE_LAMBDAS, key=lambda lam: tune(item_item(bench, ease_weights(bench, lam), S1)))
    W = ease_weights(bench, best)
    record("EASE", "선형 오토인코더 (Steck 2019)", {sc: item_item(bench, W, sc) for sc in (S1, S2)}, f"λ {best}", time.time() - t1)

    # RP3β: α · β 고르기
    t1 = time.time()
    best = max(RP3_GRID, key=lambda ab: tune(item_item(bench, rp3_weights(bench, *ab), S1)))
    W = rp3_weights(bench, *best)
    record("RP3β", "그래프 랜덤 워크 (Paudel 외 2017)", {sc: item_item(bench, W, sc) for sc in (S1, S2)},
           f"α {best[0]} · β {best[1]}", time.time() - t1)

    # ALS: α 고르기 (CPU)
    t1 = time.time()
    tries = {a: als_scores(bench, a, S1)[0] for a in ((5.0,) if smoke else ALS_ALPHAS)}
    best = max(tries, key=lambda a: tune(tries[a]))
    record("ALS", "행렬 분해 (Hu 외 2008)", {S1: tries[best], S2: als_scores(bench, best, S2)[0]}, f"α {best}", time.time() - t1)

    # 딥러닝 (GPU)
    ep = (lambda k: 2) if smoke else (lambda k: MAX_EPOCHS[k])
    model, _, sec = train_vae(bench, ep("vae"))
    record("Mult-VAE", "신경망 오토인코더 (Liang 외 2018)", {sc: vae_scores(bench, model, sc) for sc in (S1, S2)}, "기본 구조", sec)
    runs = {L: train_gcn(bench, L, ep("gcn")) for L in ((2,) if smoke else GCN_LAYERS)}
    L = max(runs, key=lambda L: runs[L][1])
    model, _, sec, deg_i = runs[L]
    record("LightGCN", "그래프 신경망 (He 외 2020) · 보고서 후보",
           {S1: gcn_scores(bench, model, S1), S2: gcn_scores(bench, model, S2, deg_i)}, f"{L}층",
           sum(r[2] for r in runs.values()), "신규 유저는 고른 작품에서 유저 벡터를 계산")
    for kind, family in (("GRU4Rec", "순서 · RNN (Hidasi 외 2016)"), ("SASRec", "순서 · 트랜스포머 (Kang & McAuley 2018)")):
        runs = {d: train_seq(bench, kind, d, ep("seq")) for d in ((32,) if smoke else SEQ_DIMS)}
        d = max(runs, key=lambda d: runs[d][1])
        model = runs[d][0]
        record(kind, family, {sc: seq_scores(bench, model, sc) for sc in (S1, S2)}, f"크기 {d}",
               sum(r[2] for r in runs.values()), "순서 모델: 서비스에 시청 순서가 없어 채택 대상 아님 · 시험이 후함 (EXP-31)")

    # 표
    for sc in (S1, S2):
        ref = bench.v05[sc][bench.part(sc, "실전")]
        lines = [f"[{sc} · 실전 {len(ref):,}문제] 기준 v0.5 Hit@10 {ref[:, 0].mean():.4f} NDCG {ref[:, 1].mean():.4f}",
                 "모델\t계열\t고른 값\t혼자 Hit@10\t혼자 NDCG\t보던\t새\tv0.5에 넣기 Hit@10 (차이, 우연)\tNDCG 차이"]
        for name, r in results.items():
            a, b = r[sc]["혼자"], r[sc]["넣기"]
            lines.append(f"{name}\t{r['계열']}\t{r['고른 값']}\t{a['hit']:.4f}\t{a['ndcg']:.4f}\t{a['seen']:.4f}\t{a['new']:.4f}\t"
                         f"{b['hit']:.4f} ({b['d_pp']:+.2f}%p, {b['p_hit']:.3f}, γ {b['gamma']})\t{b['d_ndcg']:+.4f}")
        log("\n".join(lines))
    log(f"전체 {(time.time() - t0) / 60:.0f}분 · 끝")


if __name__ == "__main__":
    main(smoke="smoke" in sys.argv[1:], mode="gcnseeds" if "gcnseeds" in sys.argv[1:] else "all")
