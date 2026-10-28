# sasrec — 시청 순서를 학습하는 트랜스포머 추천 (SASRec, Kang & McAuley 2018. EXPERIMENTS 4장 5-1)
#
# 유저가 본 작품을 시간 순서로 읽고 다음에 볼 작품을 맞히게 학습한다 (문장의 다음 단어 맞히기와 같은 방식).
# 자리마다 앞쪽 작품만 볼 수 있다 (미래를 못 봄). 점수 = 마지막 자리의 표현 · 작품 표현 (작품 표현은 입력과 같이 씀)
# 학습은 자리마다 "전체 작품 중 다음 작품 고르기" (교차 엔트로피. Klenitskiy & Vasilev 2023 에서 음수 몇 개만 쓰는 원래 방식보다 셈)
# torch 가 필요하다 (GPU 권장. 학교 GPU 서버: pytorch/pytorch:2.13.0-cuda12.6-cudnn9-runtime)

import torch
from torch import nn


class Block(nn.Module):
    # 트랜스포머 한 층: 자기 주의(앞쪽 작품 보기) → 작품별 변환. 둘 다 앞에 정규화, 뒤에 더하기
    def __init__(self, dim, heads, dropout):
        super().__init__()
        self.n1, self.n2 = nn.LayerNorm(dim), nn.LayerNorm(dim)
        self.att = nn.MultiheadAttention(dim, heads, dropout=dropout, batch_first=True)
        self.ff = nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Dropout(dropout), nn.Linear(dim, dim))
        self.drop = nn.Dropout(dropout)

    def forward(self, x, mask):
        q = self.n1(x)
        a, _ = self.att(q, q, q, attn_mask=mask, need_weights=False)
        x = x + self.drop(a)
        return x + self.drop(self.ff(self.n2(x)))


class SASRec(nn.Module):
    """작품 번호 1..n_items (0 = 빈칸, 순서는 왼쪽부터 빈칸 → 오래된 작품 → 최근 작품)"""

    def __init__(self, n_items, dim=64, layers=2, heads=1, max_len=200, dropout=0.2):
        super().__init__()
        self.dim, self.heads = dim, heads
        self.item = nn.Embedding(n_items + 1, dim, padding_idx=0)
        self.pos = nn.Embedding(max_len, dim)
        self.drop = nn.Dropout(dropout)
        self.blocks = nn.ModuleList(Block(dim, heads, dropout) for _ in range(layers))
        self.norm = nn.LayerNorm(dim)
        for emb in (self.item, self.pos):                        # 시작 점수가 너무 크지 않게 (처음 손실 ≈ log 작품 수)
            nn.init.normal_(emb.weight, std=dim ** -0.5)
        with torch.no_grad():
            self.item.weight[0].zero_()

    def forward(self, seq):
        # seq (B, L) → 자리마다 표현 (B, L, dim). 미래 자리 · 빈칸은 못 봄 (빈칸 자리도 자기 자신은 봐서 계산이 안 깨짐)
        b, n = seq.shape
        pad = seq == 0
        x = self.item(seq) + self.pos.weight[-n:]
        x = self.drop(x) * ~pad[..., None]
        eye = torch.eye(n, dtype=torch.bool, device=seq.device)
        mask = torch.triu(torch.ones(n, n, dtype=torch.bool, device=seq.device), 1) | (pad[:, None, :] & ~eye)
        mask = mask.repeat_interleave(self.heads, 0)
        for blk in self.blocks:
            x = blk(x, mask) * ~pad[..., None]
        return self.norm(x)

    def logits(self, h):
        # 표현 (..., dim) → 작품마다 점수 (..., n_items). 빈칸(0번)은 뺌
        return h @ self.item.weight[1:].T
