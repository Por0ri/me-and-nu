# embed_descriptions — AniList 줄거리를 문장 임베딩(숫자 벡터)으로 바꿔 저장한다 (1-4b)
#
# 모델: BAAI/bge-small-en-v1.5 (영어 · 384차원 · 약 130MB · MIT). CPU 로 돌린다
# 줄거리에서 스포일러 표시(~!…!~)는 내용째 빼고, HTML 태그 · "(Source: …)" · "[Written by …]" 같은 출처 꼬리도 뺀다
# (출처 꼬리가 남으면 같은 출처라서 비슷하다고 나올 수 있음)
# 무거운 라이브러리(PyTorch · sentence-transformers)는 저장소 밖 별도 환경에만 설치한다 (EXPERIMENTS 1-4b 환경)
#
#   <임베딩 환경 python> model/scripts/embed_descriptions.py
#   → model/data/anilist/desc_emb.npy (작품 수 × 384, 길이 1로 맞춤) · desc_emb_ids.json (AniList 번호 순서) (커밋 안 함)

import json
import pathlib
import re
import sys
import time

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL = "BAAI/bge-small-en-v1.5"
SRC = pathlib.Path(__file__).resolve().parents[1] / "data" / "anilist"
SPOILER = re.compile(r"~!.*?!~", re.S)
TAG = re.compile(r"<[^>]+>")
SOURCE = re.compile(r"[\(\[]\s*(source|written by)[^\)\]]*[\)\]]", re.I)


def clean(text):
    text = SPOILER.sub(" ", text or "")
    text = TAG.sub(" ", text)
    text = SOURCE.sub(" ", text)
    return " ".join(text.split())


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ids, texts = [], []
    with open(SRC / "detail.jsonl", encoding="utf-8") as f:
        for line in f:
            for d in json.loads(line)["media"]:
                t = clean(d.get("description"))
                if t:
                    ids.append(d["id"])
                    texts.append(t)
    print(f"줄거리 있는 작품 {len(ids):,}편 · 평균 {np.mean([len(t) for t in texts]):.0f}자", flush=True)
    t0 = time.time()
    model = SentenceTransformer(MODEL, device="cpu")
    print(f"모델 불러옴 ({time.time() - t0:.0f}초) · 최대 길이 {model.max_seq_length} 토큰", flush=True)
    t0 = time.time()
    emb = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
    print(f"임베딩 {emb.shape} · {time.time() - t0:.0f}초", flush=True)
    np.save(SRC / "desc_emb.npy", emb.astype(np.float32))
    (SRC / "desc_emb_ids.json").write_text(json.dumps({"model": MODEL, "ids": ids}), encoding="utf-8")
    print(f"저장 → {SRC / 'desc_emb.npy'}")


if __name__ == "__main__":
    main()
