"""입문 가이드 표지 고르기 (11/13 새벽 서원 OK · 약 10~20센트): 생성기가 원작 만화책 표지를 표지로 고른 글에서
원작 책 표지 vs 다른 카드의 포스터 · 캐릭터 그림 중 '캐릭터가 크게 나온 쪽'을 AI 가 그림을 보고 고른다.
- 같은 책 표지를 쓰는 원작 이야기 글이 있으면 AI 없이 포스터 쪽 (표지가 같아지면 둘 중 하나가 보류됨)
- 그림 순서에 따른 쏠림을 줄이려고 글마다 A · B 순서를 번갈아 바꾼다
- 결과: scratchpad/basic_cover_choice.json {글 번호: {choice: manga|swap, ...}} → bundles_v3.py 의 fix_cover 가 읽음
  uv run python -I <이 파일>
"""
import base64
import io
import json
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

from langchain_openai import ChatOpenAI
from PIL import Image
from pydantic import BaseModel, Field

from ani_rag.config import PACKAGE_ROOT
from ani_rag.content.config import get_settings
from ani_rag.content.generation import estimate_gpt_6_luna_cost

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
CACHE = PACKAGE_ROOT / ".cache"
RUNS = CACHE / "content" / "runs"
OUT = HERE / "basic_cover_choice.json"
ORDER = {"catalog_poster": 0, "catalog_cover": 1, "character_art": 2}


class Pick(BaseModel):
    bigger: str = Field(description="A 또는 B")
    reason: str = Field(description="한 줄 이유")


def origin_covers() -> set[str]:
    """원작 이야기 글(내일 넣을 것 + 서버)의 표지 그림 주소"""
    urls = set()
    paths = list((CACHE / "server_bundles_20261113").glob("dict-guide-origin-*/bundle.json"))
    kept = json.loads((CACHE / "server_upload_20261112" / "kept.json").read_text(encoding="utf-8"))
    paths += [CACHE / "server_bundles_20261112" / rid / "bundle.json" for rid in kept if "-origin-" in rid]
    for b in paths:
        p = json.loads(b.read_text(encoding="utf-8"))["items"][0]["package"]
        imgs = {i["id"]: i for i in p["images"]}
        cover = min(p["cards"], key=lambda c: c["page"])
        urls.add(imgs[cover["image_id"]]["source_url"])
    return urls


def data_url(path: str) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((512, 512))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def targets() -> list[tuple[str, dict, dict]]:
    out = []
    for f in sorted(RUNS.glob("mass-1113c-p*/results.json")):
        for r in json.loads(f.read_text(encoding="utf-8")):
            if r["status"] != "ready_for_feed":
                continue
            p = r["package"]
            imgs = {i["id"]: i for i in p["images"]}
            cards = sorted(p["cards"], key=lambda c: c["page"])
            manga = imgs[cards[0]["image_id"]]
            if "/manga/" not in manga["source_url"]:
                continue
            alts = sorted((c for c in cards[1:] if "/manga/" not in imgs[c["image_id"]]["source_url"]
                           and imgs[c["image_id"]]["asset_kind"] in ORDER),
                          key=lambda c: (ORDER[imgs[c["image_id"]]["asset_kind"]], c["page"]))
            if alts:
                # 결과 글에는 그림 파일 경로가 비어 있어서 글감(manifest)에서 찾음
                m = json.loads((CACHE / "mass19" / "manifests" / f"{r['request_id']}.json").read_text(encoding="utf-8"))
                paths = {i["id"]: i.get("local_path") for i in m["images"]}
                a = imgs[alts[0]["image_id"]]
                out.append((r["request_id"], {**manga, "local_path": paths.get(manga["id"])},
                            {**a, "local_path": paths.get(a["id"])}))
    return out


def main() -> None:
    clash = origin_covers()
    jobs = targets()
    settings = get_settings()
    model = ChatOpenAI(model=settings.model_name, api_key=settings.openai_api_key).with_structured_output(
        Pick, method="function_calling", include_raw=True)
    result: dict[str, dict] = {}

    def one(k: int, rid: str, manga: dict, alt: dict) -> tuple[str, dict]:
        if manga["source_url"] in clash:
            return rid, {"choice": "swap", "reason": "같은 책 표지를 쓰는 원작 이야기 글이 있음", "cost": 0.0}
        first, second = (manga, alt) if k % 2 == 0 else (alt, manga)
        msg = [
            {"type": "text", "text": (
                "애니 입문 가이드 카드의 표지로 쓸 그림 후보 두 장이야. 둘 중 등장인물이 더 크게 나온 그림을 골라. "
                "기준: 캐릭터 얼굴 · 상반신이 화면에서 차지하는 크기와 또렷함. 글씨 · 로고 · 배경은 상관없어. "
                "첫 번째 그림이 A, 두 번째 그림이 B 야. bigger 에 A 또는 B 만 써.")},
            {"type": "image_url", "image_url": {"url": data_url(first["local_path"]), "detail": "low"}},
            {"type": "image_url", "image_url": {"url": data_url(second["local_path"]), "detail": "low"}},
        ]
        resp = model.invoke([("human", msg)])
        parsed = resp.get("parsed")
        usage = (resp["raw"].usage_metadata or {}) if resp.get("raw") else {}
        cached = (usage.get("input_token_details") or {}).get("cache_read", 0)
        cost = estimate_gpt_6_luna_cost(usage.get("input_tokens", 0), cached, 0, usage.get("output_tokens", 0))
        if not parsed or parsed.bigger.strip().upper() not in ("A", "B"):
            return rid, {"choice": "swap", "reason": "판단 실패 → 포스터 쪽", "cost": cost}
        chosen = first if parsed.bigger.strip().upper() == "A" else second
        return rid, {"choice": "manga" if chosen is manga else "swap", "reason": parsed.reason, "cost": cost,
                     "manga": manga["source_url"], "alt": alt["source_url"], "alt_kind": alt["asset_kind"]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(one, k, *job) for k, job in enumerate(jobs)]
        for fut in futures:
            try:
                rid, rec = fut.result()
                result[rid] = rec
            except Exception as exc:  # 한 글이 실패해도 계속 (그 글은 포스터 쪽)
                print("실패:", type(exc).__name__, str(exc)[:150], flush=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"대상 {len(jobs)}개 →", dict(Counter(v["choice"] for v in result.values())),
          f"· 비용 ${sum(v.get('cost') or 0 for v in result.values()):.3f}")
    print(OUT)


main()
