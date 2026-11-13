"""주연 소개 글감 → .cache/mass23 (11/13 낮 서원: 표지는 팬 추천 시안 C 방식으로 —
주연 얼굴 바둑판(어둡게) + 가운데 원작 애니 포스터, 포스터 흰 테두리는 얇게 6px (팬 추천은 12px))
- 주연 고르기 · 장 구성은 글감 만들기의 pick_character 그대로 (소개 참고 글이 있는 주연 3~4명, 한 장에 한 명)
- 서버에 이미 있는 주연 소개(11/12, 81개) 작품은 뺌
- 나눠 돌리기: --shard k --of n (글 번호 지문으로 나눔). 같이 쓰는 저장 파일은 통째 바꾸기로 써서 반쯤 쓴 파일이 안 읽히게,
  끝 표시(manifests_done.flag)는 모든 조각이 끝났을 때만
  uv run python -I <이 파일> [--limit N] [--shard k --of n]
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import time

import psycopg

sys.stdout.reconfigure(encoding="utf-8")


def _atomic_write_text(self, data, encoding=None, errors=None, newline=None):
    tmp = self.with_name(f"{self.name}.tmp{os.getpid()}")
    with open(tmp, "w", encoding=encoding, errors=errors, newline=newline) as f:
        n = f.write(data)
    for attempt in range(50):
        try:
            os.replace(tmp, self)
            return n
        except PermissionError:  # 다른 조각이 그 파일을 읽는 중 (Windows)
            time.sleep(0.1 + 0.05 * attempt)
    os.replace(tmp, self)
    return n


pathlib.Path.write_text = _atomic_write_text

import ani_rag.build_manifest as bm  # noqa: E402
from ani_rag.config import PACKAGE_ROOT, load_settings  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=0, help="시범: 인기순 앞에서 N개만")
ap.add_argument("--shard", type=int, default=0)
ap.add_argument("--of", type=int, default=1)
args = ap.parse_args()
out = PACKAGE_ROOT / ".cache" / "mass23"
BORDER = 6
bm.MASS = True
for kind in bm.QUOTA:
    bm.QUOTA[kind] = 10 ** 6
# pick_character 는 주연 소개 다음에 생일 모음 · 프로필 · 개인 생일 글감도 붙이는데, 여기선 주연 소개만 씀
bm.pick_birthday_book = lambda ctx, mains: []
bm.pick_profile = lambda ctx, mains: []
bm.pick_birthdays = lambda ctx, mains: []
server = set(json.loads((PACKAGE_ROOT / ".cache" / "server_upload_20261112" / "kept.json").read_text(encoding="utf-8")))

with psycopg.connect(load_settings().dict_conninfo) as conn, conn.cursor() as cur:
    ctx = bm.Ctx(cur)
    decks = [d for d in bm.pick_character(ctx) if d.kind == "intro" and d.request_id not in server]
    ctx.cur = None
    conn.rollback()
if args.limit:
    decks = decks[:args.limit]
print(f"주연 소개 글감 {len(decks)}개 (서버에 이미 있는 주연 소개는 뺌)", flush=True)


def poster_of(d, w: int) -> dict | None:
    """작품의 대표 포스터 (Kitsu 대표 항목 포스터 → AniList 첫 항목 표지) — 팬 추천과 같음"""
    urls = []
    kid_entry = ctx.kitsu_entry(w)
    if kid_entry is not None:
        urls.append(f"{bm.KITSU}/{ctx.kitsu[kid_entry]}")
    lead = ctx.work_entries[w][0]
    if lead in ctx.anilist:
        urls.append(f"https://anilist.co/anime/{ctx.anilist[lead]}")
    for u in urls:
        for img in d.images:
            if (img["source_url"] == u and img["asset_kind"] in ("catalog_poster", "catalog_cover")
                    and not img["id"].startswith(("collage-", "player-"))):
                return img
    return None


def fill(img, w: int, h: int, top: float = 0.18):
    """칸을 꽉 채우게 자름 (캐릭터 그림은 얼굴이 위쪽이라 세로로 자를 땐 위쪽 18% 지점 기준)"""
    from PIL import Image
    sc = max(w / img.width, h / img.height)
    r = img.resize((max(w, round(img.width * sc)), max(h, round(img.height * sc))), Image.LANCZOS)
    x = (r.width - w) // 2
    y = int((r.height - h) * top)
    return r.crop((x, y, x + w, y + h))


def make_intro_cover(d) -> bool:
    """주연 얼굴 바둑판(2×2, 3명이면 세로 3칸)을 어둡게 깔고 가운데에 원작 애니 포스터
    (칸의 1.18배, 흰 테두리 6px · 그림자, 카드 42% 높이 중심)"""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    w = d.works[0]
    faces = [img for img in d.images if img["asset_kind"] == "character_art"][:4]
    source = poster_of(d, w)
    if source is None or len(faces) < 3:
        return False
    W, H, gap = 1308, 1632, 8
    canvas = Image.new("RGB", (W, H), (17, 17, 21))
    if len(faces) >= 4:
        cw, ch = (W - gap) // 2, (H - gap) // 2
        boxes = [(c * (cw + gap), r * (ch + gap), cw, ch) for r in range(2) for c in range(2)]
    else:
        cw = (W - 2 * gap) // 3
        boxes = [(k * (cw + gap), 0, cw, H) for k in range(3)]
    for img, (x, y, bw, bh) in zip(faces, boxes):
        with Image.open(img["local_path"]) as t:
            tile = t.convert("RGB")
        canvas.paste(fill(tile, bw, bh), (x, y))
    canvas = ImageEnhance.Brightness(canvas).enhance(0.55)
    with Image.open(source["local_path"]) as t:
        art = t.convert("RGB")
    sc = min((W // 2 - 16) / art.width, (H // 2 - 16) / art.height) * 1.18
    art = art.resize((int(art.width * sc), int(art.height * sc)), Image.LANCZOS)
    framed = Image.new("RGB", (art.width + BORDER * 2, art.height + BORDER * 2), (255, 255, 255))
    framed.paste(art, (BORDER, BORDER))
    x, y = (W - framed.width) // 2, max(16, int(H * 0.42 - framed.height / 2))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x + 10, y + 18, x + framed.width + 10, y + framed.height + 18), 18,
                                             fill=(0, 0, 0, 170))
    out_img = canvas.convert("RGBA")
    out_img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))
    out_img = out_img.convert("RGB")
    out_img.paste(framed, (x, y))
    path = bm.TRIAL / "images" / d.request_id / "collage.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(path, "PNG", optimize=True)
    data = path.read_bytes()
    tag = hashlib.sha1(data).hexdigest()[:16]
    title = ctx.title[w]
    names = [n for img in faces for n in (img.get("depicted_entities") or [])][:4]
    d.images.insert(0, {
        **source, "id": f"collage-{tag}", "url": f"{source['url']}{'&' if '?' in source['url'] else '?'}collage={tag}",
        "local_path": str(path), "menu_work_ids": [ctx.menu_id[w]], "related_fact_ids": [],
        "width": W, "height": H, "media_type": "image/png", "file_size_bytes": len(data),
        "fingerprint": hashlib.sha256(data).hexdigest(), "has_alpha": False, "alpha_coverage": None, "subject_box": None,
        "source_description": f"〈{title}〉 포스터를 가운데에 크게, 주연 캐릭터 그림 {len(faces)}장을 뒤에 어둡게 깐 표지",
        "depicted_entities": [title, *names],
        "attribution_text": f"공개 Kitsu 카탈로그 포스터와 AniList 캐릭터 그림 {len(faces)}장을 한 장에 모음 (사용 허가 확인과는 별개)"})
    return True


(out / "manifests").mkdir(parents=True, exist_ok=True)
(out / "fixtures").mkdir(parents=True, exist_ok=True)
written = skipped = 0
for d in decks:
    if int(hashlib.sha1(d.request_id.encode()).hexdigest()[:8], 16) % args.of != args.shard:
        continue  # 다른 조각 몫
    if (out / "manifests" / f"{d.request_id}.json").exists():
        written += 1
        continue
    bm.collect_images(d)
    if not make_intro_cover(d):
        skipped += 1
        print(f"건너뜀 {d.request_id}: 표지 재료 모자람 (포스터 또는 주연 그림 3장)", flush=True)
        continue
    d.card_plan[0]["image"] = ("collage",)  # 표지 장은 바둑판 그림
    if not bm.resolve_plan(d) or len(d.images) < (d.target_cards or 3) or not d.facts:
        skipped += 1
        print(f"건너뜀 {d.request_id}: 장별 계획에 맞는 그림이 모자람 (그림 {len(d.images)}장)", flush=True)
        continue
    written += 1
    (out / "manifests" / f"{d.request_id}.json").write_text(
        json.dumps(d.manifest(), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "fixtures" / f"{d.request_id}.json").write_text(
        json.dumps(d.fixture(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {written} {d.request_id} {d.topic}", flush=True)
(out / f"shard_{args.shard}_of_{args.of}.done").write_text("done", encoding="utf-8")
if all((out / f"shard_{k}_of_{args.of}.done").exists() for k in range(args.of)):
    (out / (("trial_" if args.limit else "") + "manifests_done.flag")).write_text("done", encoding="utf-8")
    print("모든 조각 끝 → 끝 표시 씀", flush=True)
print(f"조각 {args.shard}/{args.of} 끝: 글감 {written}개 · 건너뜀 {skipped}개 → {out}", flush=True)
