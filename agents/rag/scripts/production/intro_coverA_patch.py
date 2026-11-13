"""주연 소개 표지를 '주연 얼굴 바둑판(꽉 채움)'으로 바꾸기 — 돈 안 듦 (그림만 다시 그림, 글은 그대로)
11/13 낮 서원: 가운데 포스터를 얹는 것보다 처음 예시(A)처럼 주연 얼굴만 꽉 채운 게 더 예쁨. 3명이면 포스터에 얼굴이 가려짐
- 4명: 2×2 로 카드를 꽉 채움 (예시 A 와 같음, 칸 사이 8px)
- 3명: 얼굴 3칸을 글자 위쪽(카드 위 66%)에 채우고, 글자 자리엔 같은 그림을 흐리고 어둡게 깖
  (세로 3칸으로 카드 끝까지 채우면 230px 그림을 4.7배 키워야 해서 흐려짐 → 66% 칸이면 3.1배로 4명일 때와 비슷)
- 표지 그림 정보(바둑판 그림)를 새 그림으로 바꾸고, 표지 그림 설명에서 '포스터'를 뺌. 표지 글은 그대로 (포스터 말 없음)
- 한 장에 몇 초씩 걸려서 6개씩 동시에 그리고, 하나 끝날 때마다 scratchpad/intro_coverA_patch.jsonl 에 이어 씀
  (끊겨도 다시 돌리면 이어서). 다 끝나면 intro_coverA_patch.json {글 번호: 고친 꾸러미} 과 미리보기를 씀
- 결과: .cache/cover_patch_20261113/<글 번호>/card_01.png · .cache/reports/intro_coverA.html
  uv run python -I <이 파일>
"""
import hashlib
import html
import io
import json
import pathlib
import sys
import urllib.parse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed

from PIL import Image, ImageEnhance, ImageFilter

from ani_rag.config import PACKAGE_ROOT
from ani_rag.content.agents.content_generation.renderer import render_card_png
from ani_rag.content.layout_engine import build_cards
from ani_rag.content.models import CopyCard, ImageAsset

HERE = pathlib.Path(__file__).parent
CACHE = PACKAGE_ROOT / ".cache"
RUNS = CACHE / "content" / "runs"
PATCH = CACHE / "cover_patch_20261113"
PARTIAL = HERE / "intro_coverA_patch.jsonl"
W, H, GAP = 1308, 1632, 8
TOP = int(H * 0.66)  # 앱 글자가 시작하는 높이


def local_bytes(rid: str, url: str) -> bytes | None:
    name = hashlib.sha1(url.encode()).hexdigest()[:16]
    suffix = pathlib.PurePosixPath(urllib.parse.urlsplit(url).path).suffix.lower()[:5]
    path = CACHE / "trial" / "images" / rid / (name + suffix)
    return path.read_bytes() if path.exists() and path.stat().st_size > 0 else None


def fill(img: Image.Image, w: int, h: int, top: float = 0.18) -> Image.Image:
    """칸을 꽉 채우게 자름 (캐릭터 그림은 얼굴이 위쪽이라 세로로 자를 땐 위쪽 18% 지점 기준)"""
    sc = max(w / img.width, h / img.height)
    r = img.resize((max(w, round(img.width * sc)), max(h, round(img.height * sc))), Image.LANCZOS)
    x = (r.width - w) // 2
    y = int((r.height - h) * top)
    return r.crop((x, y, x + w, y + h))


def grid4(faces: list[Image.Image]) -> Image.Image:
    canvas = Image.new("RGB", (W, H), (17, 17, 21))
    cw, ch = (W - GAP) // 2, (H - GAP) // 2
    for k, face in enumerate(faces[:4]):
        r, c = divmod(k, 2)
        canvas.paste(fill(face, cw, ch), (c * (cw + GAP), r * (ch + GAP)))
    return canvas


def grid3(faces: list[Image.Image]) -> Image.Image:
    cw = (W - 2 * GAP) // 3
    top = Image.new("RGB", (W, TOP), (17, 17, 21))
    for k, face in enumerate(faces[:3]):
        top.paste(fill(face, cw, TOP), (k * (cw + GAP), 0))
    canvas = ImageEnhance.Brightness(top.resize((W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))).enhance(0.55)
    canvas.paste(top, (0, 0))
    return canvas


def make_one(rid: str, package: dict) -> tuple[str, dict | None, str]:
    """글 하나의 표지를 다시 그려 (글 번호, 고친 꾸러미 또는 None, 결과 이름)"""
    p = json.loads(json.dumps(package))
    cards = sorted(p["cards"], key=lambda c: c["page"])
    cover = cards[0]
    imgs = {i["id"]: i for i in p["images"]}
    old = imgs.get(cover["image_id"])
    face_imgs = []
    for c in cards[1:]:
        im = imgs.get(c["image_id"])
        if im and im.get("asset_kind") == "character_art" and im not in face_imgs:
            face_imgs.append(im)
    datas = [local_bytes(rid, im["url"]) for im in face_imgs]
    if old is None or not old["id"].startswith("collage-") or len(face_imgs) < 3 or any(d is None for d in datas):
        return rid, None, "건너뜀 (표지가 바둑판이 아니거나 얼굴 그림 3장 미만 · 파일 없음)"
    faces = []
    for d in datas:
        with Image.open(io.BytesIO(d)) as im:
            faces.append(im.convert("RGB"))
    canvas = grid4(faces) if len(faces) >= 4 else grid3(faces)
    n = min(4, len(faces))
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    data = buf.getvalue()
    tag = hashlib.sha1(data).hexdigest()[:16]
    title = p["topic"].split("〉")[0].lstrip("〈") if "〈" in p["topic"] else p["topic"]
    names = [(im.get("depicted_entities") or [""])[0] for im in face_imgs[:n]]
    first = face_imgs[0]
    new_img = {**first, "id": f"collage-{tag}", "url": f"{first['url']}{'&' if '?' in first['url'] else '?'}collage={tag}",
               "local_path": None, "storage_key": hashlib.sha256(data).hexdigest(),
               "fingerprint": hashlib.sha256(data).hexdigest(), "media_type": "image/png", "file_size_bytes": len(data),
               "width": W, "height": H, "has_alpha": False, "alpha_coverage": None, "subject_box": None,
               "menu_work_ids": old.get("menu_work_ids") or first.get("menu_work_ids") or [], "related_fact_ids": [],
               "source_description": f"〈{title}〉 주연 캐릭터 그림 {n}장을 바둑판으로 꽉 채운 표지",
               "depicted_entities": [title, *[x for x in names if x]],
               "attribution_text": f"공개 AniList 캐릭터 그림 {n}장을 한 장에 모음 (사용 허가 확인과는 별개)"}
    asset = ImageAsset.model_validate(new_img)
    copy = CopyCard.model_validate({**{k: v for k, v in cover.items() if k != "layout"}, "image_id": new_img["id"],
                                    "image_explanation": f"〈{title}〉 주연 {n}명의 얼굴을 모은 이미지."})
    card = build_cards([copy], [asset], art_directions={1: {"background_style": cover["layout"]["background_style"]}})[0]
    png = render_card_png(None, card, asset, data)
    (PATCH / rid).mkdir(parents=True, exist_ok=True)
    (PATCH / rid / "card_01.png").write_bytes(png)
    cover.update({"image_id": new_img["id"], "image_explanation": copy.image_explanation,
                  "layout": card.layout.model_dump(mode="json"), "rendered_image_key": hashlib.sha256(png).hexdigest(),
                  "rendered_width": W, "rendered_height": H})
    p["images"] = [new_img if i["id"] == old["id"] else i for i in p["images"]]
    return rid, p, f"얼굴 {n}명"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    posts = {}
    for f in sorted(RUNS.glob("mass-1113i-p*/results.json")):
        try:
            recs = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # 일꾼이 쓰는 중이면 다음 번에
        for r in recs:
            if r.get("status") == "ready_for_feed" and r.get("package"):
                posts[r["request_id"]] = (f.parent.name, r)
    done: dict[str, tuple[dict | None, str]] = {}
    if PARTIAL.exists():
        for line in PARTIAL.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["rid"] in posts and (rec["pkg"] is None or (PATCH / rec["rid"] / "card_01.png").exists()):
                done[rec["rid"]] = (rec["pkg"], rec["outcome"])
    todo = [rid for rid in posts if rid not in done]
    print(f"주연 소개 {len(posts)}개 · 전에 끝낸 것 {len(done)}개 · 이번에 그릴 것 {len(todo)}개", flush=True)
    with ProcessPoolExecutor(max_workers=6) as pool, PARTIAL.open("a", encoding="utf-8") as out:
        futures = [pool.submit(make_one, rid, posts[rid][1]["package"]) for rid in todo]
        for k, fut in enumerate(as_completed(futures), 1):
            rid, pkg, outcome = fut.result()
            done[rid] = (pkg, outcome)
            out.write(json.dumps({"rid": rid, "pkg": pkg, "outcome": outcome}, ensure_ascii=False) + "\n")
            out.flush()
            if k % 25 == 0:
                print(f"  {k}/{len(todo)}", flush=True)
    patched = {rid: pkg for rid, (pkg, _) in done.items() if pkg is not None}
    (HERE / "intro_coverA_patch.json").write_text(json.dumps(patched, ensure_ascii=False), encoding="utf-8")
    outcome = Counter(o for _, o in done.values())
    figs = []
    for rid in sorted(patched, key=lambda r: int(r.rsplit("-w", 1)[1])):
        p = patched[rid]
        cover = min(p["cards"], key=lambda c: c["page"])
        folder = posts[rid][0]
        t = "<br>".join(html.escape(x) for x in cover.get("title_lines") or [])
        b = " ".join(html.escape(x) for x in cover.get("body_sentences") or [])
        figs.append(f'<figure><div class=frame><img loading=lazy src="../cover_patch_20261113/{rid}/card_01.png" alt="">'
                    f'<div class=ov><div class=eb>{html.escape(cover.get("eyebrow") or "")}</div><div class=tt>{t}</div>'
                    f'<div class=bd>{b}</div></div></div><figcaption><b>{html.escape(p.get("topic") or rid)}</b> '
                    f'<span class=muted>· 전: <a href="../content/runs/{folder}/{rid}/card_01.png">가운데 포스터판</a></span>'
                    f'</figcaption></figure>')
    (CACHE / "reports" / "intro_coverA.html").write_text(f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>주연 소개 얼굴 바둑판</title><style>
:root{{--bg:#f7f7f8;--card:#fff;--text:#1d1d22;--muted:#6b6b76;--line:#e4e4ea}}
@media (prefers-color-scheme: dark){{:root{{--bg:#141418;--card:#1e1e24;--text:#ececf1;--muted:#a0a0ad;--line:#2e2e37}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}}
main{{max-width:1300px;margin:0 auto;padding:20px 16px 60px}} h1{{font-size:22px;margin:0 0 4px}} .muted{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}}
figure{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px}}
.frame{{position:relative;aspect-ratio:1308/1632;border-radius:9px;overflow:hidden;background:#000}}
.frame img{{width:100%;height:100%;display:block;object-fit:cover}}
.ov{{position:absolute;left:6%;right:6%;top:66%;color:#fff;text-shadow:0 1px 4px #000c}}
.eb{{font-size:10px;opacity:.9}} .tt{{font-size:15px;font-weight:800;margin:2px 0;line-height:1.3}} .bd{{font-size:11px;line-height:1.4}}
figcaption{{margin-top:6px;font-size:12px}} a{{color:inherit}}</style></head><body><main>
<h1>주연 소개 — 주연 얼굴 바둑판 표지 {len(figs)}개</h1>
<p class=muted>{' · '.join(f'{k} {v}' for k, v in outcome.items())}. 4명은 2×2 로 꽉 채움, 3명은 얼굴 3칸을 글자 위쪽에 채우고 글자 자리엔 같은 그림을 흐리게.
글자는 앱처럼 얹은 흉내. 아직 묶음은 안 바꿈.</p>
<div class=grid>{''.join(figs)}</div></main></body></html>""", encoding="utf-8")
    print("결과:", dict(outcome), flush=True)
    print(CACHE / "reports" / "intro_coverA.html", flush=True)


if __name__ == "__main__":
    main()
