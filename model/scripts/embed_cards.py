# embed_cards — LLM 취향 카드 (llm_cards.py 결과)를 문장 임베딩으로 바꿔 저장한다 (EXP-38)
#
# 모델은 줄거리 임베딩 (embed_descriptions.py, EXP-27)과 같은 BAAI/bge-small-en-v1.5 → EXP-27 과 다른 건 넣는 글 (줄거리 vs 카드)뿐
# 카드 글 = 칸 이름과 값을 이어 붙인 것. JSON 이 깨진 카드는 LLM 답 그대로
# 임베딩 환경 (sentence-transformers)에서 돌린다 (embed_descriptions.py 와 같은 환경)
#
#   <임베딩 환경 python> model/scripts/embed_cards.py
#   → model/data/anilist/card_emb.npy (작품 수 × 384, 길이 1로 맞춤) · card_emb_ids.json (커밋 안 함)

import json
import pathlib
import sys
import time

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL = "BAAI/bge-small-en-v1.5"
SRC = pathlib.Path(__file__).resolve().parents[1] / "data" / "anilist"
FIELDS = ("mood", "themes", "protagonist", "pacing", "emotional_intensity", "style", "audience", "keywords")


def card_text(row):
    card = row.get("card")
    if not isinstance(card, dict):
        return row.get("raw") or ""
    parts = []
    for k in FIELDS:
        v = card.get(k)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        if v:
            parts.append(f"{k.replace('_', ' ')}: {v}")
    return ". ".join(parts)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rows = [json.loads(line) for line in open(SRC / "llm_cards.jsonl", encoding="utf-8")]
    ids, texts = zip(*[(r["id"], card_text(r)) for r in rows if card_text(r)])
    print(f"카드 {len(ids):,}개 · JSON 실패 {sum(not isinstance(r.get('card'), dict) for r in rows)}개 · "
          f"평균 {np.mean([len(x) for x in texts]):.0f}자", flush=True)
    t0 = time.time()
    model = SentenceTransformer(MODEL, device="cpu")
    emb = model.encode(list(texts), batch_size=64, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
    print(f"임베딩 {emb.shape} · {time.time() - t0:.0f}초", flush=True)
    np.save(SRC / "card_emb.npy", emb.astype(np.float32))
    (SRC / "card_emb_ids.json").write_text(json.dumps({"model": MODEL, "ids": list(ids)}), encoding="utf-8")
    print(f"저장 → {SRC / 'card_emb.npy'}")


if __name__ == "__main__":
    main()
