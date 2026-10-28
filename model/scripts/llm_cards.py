# llm_cards — 작품 정보 → LLM 취향 카드 (EXP-38 1단계). 학교 GPU 서버 (V100 32GB)에서 돌린다
#
# 모델: Qwen/Qwen3-8B (Apache 2.0). 16비트 (V100 은 bfloat16 이 없어 float16), 생각 모드 끔, 고정 답 (샘플링 안 함)
# 입력: llm_card_inputs.py 결과 (줄거리 · 장르 · 태그 · 스튜디오, 제목은 [TITLE])
# 출력: 작품마다 JSON 카드 한 줄. 이미 만든 작품은 건너뜀 (끊겨도 이어서 돌림)
#
#   python llm_cards.py <입력.jsonl> <출력.jsonl> [--limit N] [--batch 32]

import argparse
import json
import pathlib
import re
import sys
import time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL = "Qwen/Qwen3-8B"
SYSTEM = ("You write short taste profiles of anime for a recommender system. Describe what kind of viewer would "
          "enjoy the work, using only the given information. Never write the work's title or the name of any other "
          "anime, manga, game, or franchise. Answer with one JSON object and nothing else.")
ASK = """Information about an anime:
Synopsis: {description}
Genres: {genres}
Tags: {tags}
Studio: {studios}

Write a JSON object with exactly these keys:
"mood": overall atmosphere (short phrase)
"themes": main themes (short phrase)
"protagonist": kind of main character or cast (short phrase)
"pacing": slow, moderate, or fast, with a few words
"emotional_intensity": light, moderate, or heavy, with a few words
"style": storytelling or visual style (short phrase)
"audience": who would enjoy it (short phrase)
"keywords": list of 10 taste keywords"""
JSON_OBJ = re.compile(r"\{.*\}", re.S)


def prompt(tok, r):
    msg = ASK.format(description=r["description"], genres=", ".join(r["genres"]) or "-",
                     tags=", ".join(r["tags"]) or "-", studios=", ".join(r["studios"]) or "-")
    return tok.apply_chat_template([{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg}],
                                   tokenize=False, add_generation_prompt=True, enable_thinking=False)


def parse(text):
    m = JSON_OBJ.search(text)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs")
    ap.add_argument("out")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-new", type=int, default=320)
    a = ap.parse_args()
    rows = [json.loads(line) for line in open(a.inputs, encoding="utf-8")]
    out = pathlib.Path(a.out)
    done = {json.loads(line)["id"] for line in open(out, encoding="utf-8")} if out.exists() else set()
    rows = [r for r in rows if r["id"] not in done][: a.limit or None]
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(MODEL)
    tok.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(MODEL, dtype=torch.float16, device_map="cuda")
    model.eval()
    print(f"모델 불러옴 {time.time() - t0:.0f}초 · 만들 카드 {len(rows):,}개 (이미 {len(done):,}개)", flush=True)
    texts = {r["id"]: prompt(tok, r) for r in rows}
    rows.sort(key=lambda r: len(texts[r["id"]]))          # 길이가 비슷한 것끼리 묶어 빈칸 줄이기
    n_bad, t1 = 0, time.time()
    with open(out, "a", encoding="utf-8") as f:
        for b in range(0, len(rows), a.batch):
            part = rows[b: b + a.batch]
            enc = tok([texts[r["id"]] for r in part], return_tensors="pt", padding=True).to("cuda")
            with torch.no_grad():
                gen = model.generate(**enc, max_new_tokens=a.max_new, do_sample=False,
                                     temperature=None, top_p=None, top_k=None, pad_token_id=tok.pad_token_id)
            for r, seq in zip(part, gen[:, enc["input_ids"].shape[1]:]):
                raw = tok.decode(seq, skip_special_tokens=True).strip()
                card = parse(raw)
                n_bad += card is None
                f.write(json.dumps({"id": r["id"], "card": card, "raw": raw}, ensure_ascii=False) + "\n")
            f.flush()
            k = b + len(part)
            left = (time.time() - t1) / k * (len(rows) - k)
            print(f"{k:,}/{len(rows):,} · JSON 실패 {n_bad} · {time.time() - t1:.0f}초 · 남은 시간 약 {left / 60:.0f}분", flush=True)
    print("끝", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
