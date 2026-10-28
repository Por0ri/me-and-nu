# llm_card_inputs — LLM 취향 카드 (EXP-38)에 넣을 작품 정보 파일을 만든다
#
# 작품마다: 줄거리 (embed_descriptions.clean 과 같은 정리 + 작품 제목은 [TITLE] 로 바꿈) · 장르 · 태그 (관련도 50% 이상) · 메인 스튜디오
# 작품 정보만 쓴다 (유저 기록 없음). 제목을 지우는 건 LLM 이 기억한 평판 · "같이 보는 정보"가 카드에 새는 것을 줄이려는 것
#
#   python -m model.scripts.llm_card_inputs  → model/data/anilist/llm_card_inputs.jsonl (커밋 안 함)

import json
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "data" / "anilist"
SPOILER = re.compile(r"~!.*?!~", re.S)
TAG = re.compile(r"<[^>]+>")
SOURCE = re.compile(r"[\(\[]\s*(source|written by)[^\)\]]*[\)\]]", re.I)
MIN_TAG_RANK = 50


def clean(text):
    # embed_descriptions.clean 과 같음 (스포일러 · HTML · 출처 꼬리 빼기)
    text = SPOILER.sub(" ", text or "")
    text = TAG.sub(" ", text)
    text = SOURCE.sub(" ", text)
    return " ".join(text.split())


def strip_titles(text, titles):
    # 긴 제목부터 지움 (짧은 제목이 긴 제목 일부를 먼저 지우지 않게). 3글자 미만 제목은 일반 단어와 겹칠 수 있어 둠
    for t in sorted({t.strip() for t in titles if t and len(t.strip()) >= 3}, key=len, reverse=True):
        text = re.sub(re.escape(t), "[TITLE]", text, flags=re.I)
    return text


def pages(name):
    with open(SRC / name, encoding="utf-8") as f:
        for line in f:
            yield from json.loads(line).get("media") or []


def main():
    media = {m["id"]: m for fn in ("media.jsonl", "media_recent.jsonl") for m in pages(fn)}
    rows = []
    for d in pages("detail.jsonl"):
        m = media.get(d["id"])
        desc = clean(d.get("description"))
        if not m or not desc:
            continue
        titles = list((m.get("title") or {}).values())
        tags = [t["name"] for t in sorted(m.get("tags") or [], key=lambda t: -(t.get("rank") or 0))
                if (t.get("rank") or 0) >= MIN_TAG_RANK and not t.get("isMediaSpoiler") and not t.get("isAdult")]
        studios = [e["node"]["name"] for e in (d.get("studios") or {}).get("edges", [])
                   if e.get("isMain") and e["node"].get("isAnimationStudio")]
        rows.append({"id": d["id"], "year": d.get("seasonYear"), "description": strip_titles(desc, titles),
                     "genres": m.get("genres") or [], "tags": tags, "studios": studios})
    out = SRC / "llm_card_inputs.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_title = sum("[TITLE]" in r["description"] for r in rows)
    print(f"작품 {len(rows):,}편 → {out} (줄거리에서 제목을 지운 작품 {n_title:,}편)")


if __name__ == "__main__":
    main()
