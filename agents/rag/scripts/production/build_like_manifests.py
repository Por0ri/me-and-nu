"""팬 추천 글감 → .cache/mass20 (11/13 새벽 서원: 겹침 규칙 + 바둑판 표지, 3개 시범 먼저)
- 〈A〉 좋아한 사람들이 많이 추천한 작품 4편 (AniList 추천 기록). 인기순으로 고르며
  · 두 글이 같이 소개하는 작품(기준 작품 포함)은 2편까지 (서로 추천하는 짝 정도만 허용)
  · 한 작품은 추천작으로 4번까지
  · 추천 8위 안에서 채움 (위 규칙에 걸리면 다음 순위로)
  · 서버에 이미 있는 팬 추천 12개도 겹침 계산에 넣음
- 표지 = 추천작 4편 포스터 바둑판을 어둡게 깔고 원작 애니 포스터를 가운데 크게 (11/13 새벽 서원 시안 C:
  '진격거 팬이라면' 이면 표지에 진격의 거인이 보여야 함). 글마다 새 그림이라 다른 글 표지와 안 겹침
- 추천작 카드 그림은 그 작품이 나올 때마다 Kitsu 포스터 · AniList 시즌 표지를 돌아가며
  uv run python -I <이 파일> [--limit N]
"""
import argparse
import hashlib
import json
import pathlib
import sys
from collections import Counter, defaultdict

import psycopg

import ani_rag.build_manifest as bm
from ani_rag.build_manifest import Deck, Row
from ani_rag.config import PACKAGE_ROOT, load_settings

sys.stdout.reconfigure(encoding="utf-8")
ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=0, help="시범: 인기순 앞에서 N개만")
args = ap.parse_args()
out = PACKAGE_ROOT / ".cache" / "mass22"  # 11/13 새벽: 표지 시안 C (mass20 · 21 은 시범)
MAX_SHARED, CAP, POOL = 2, 4, 8
bm.MASS = True
for kind in bm.QUOTA:
    bm.QUOTA[kind] = 10 ** 6
kept = set(json.loads((PACKAGE_ROOT / ".cache" / "server_upload_20261112" / "kept.json").read_text(encoding="utf-8")))

with psycopg.connect(load_settings().dict_conninfo) as conn, conn.cursor() as cur:
    ctx = bm.Ctx(cur)
    cur.execute("""select entity_id, value_entity_id, (value->>'rating')::int, source_url, fact_id, fetched_at
                   from dict.fact where field = 'recommendation' and value_entity_id is not null""")
    rec: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    rec_row: dict[tuple[int, int], tuple] = {}
    for e, t, r, url, fid, at in cur.fetchall():
        a, b = ctx.entry_work.get(e), ctx.entry_work.get(t)
        if a and b and a != b and (r or 0) > 0:
            rec[a][b] += r
            rec_row.setdefault((a, b), (a, "recommendation", {"rating": r}, b, "anilist", url, at, fid))
    by_menu = {v: k for k, v in ctx.menu_id.items()}
    mains = bm.main_casts(ctx)
    accepted: list[frozenset] = []
    used = Counter()
    for rid in kept:  # 서버에 있는 팬 추천
        if "-like-" in rid:
            doc = json.loads((PACKAGE_ROOT / ".cache" / "server_bundles_20261112" / rid / "bundle.json").read_text(encoding="utf-8"))
            ws = [by_menu[m] for m in doc["items"][0]["package"]["menu_work_ids"] if m in by_menu]
            accepted.append(frozenset(ws))
            used.update(ws[1:])
    seen_image = Counter()  # 작품마다 몇 번째로 나오는지 (그림 돌려 쓰기)
    decks = []
    for work in ctx.order[:1000]:
        if args.limit and len(decks) >= args.limit:
            break
        req = f"dict-curation-like-w{ctx.menu_id[work]}"
        if req in kept or not ctx.free(work):
            continue
        ranked = [b for b in sorted(rec[work], key=lambda b: -rec[work][b]) if ctx.ok_work(b)]
        picks = [b for b in ranked[:POOL] if used[b] < CAP][:4]
        if len(picks) < 4:
            continue
        group = frozenset([work, *picks])
        if any(len(group & g) > MAX_SHARED for g in accepted):
            continue
        accepted.append(group)
        used.update(picks)
        title = ctx.title[work]
        obj = '을' if bm.has_batchim(title) else '를'
        d = Deck(ctx, "like", f"w{ctx.menu_id[work]}", "curation", f"〈{title}〉{obj} 좋아한다면 볼 만한 작품",
                 [work, *picks], [f"〈{title}〉", "다음엔 뭘 볼까"],
                 f"〈{title}〉{obj} 좋아한 사람들이 많이 추천한 다음 작품을 소개하는 글. 다른 팬들의 선택이라는 느낌을 "
                 f"살린다 (예: '〈{title}〉 좋아한 사람들이 입 모아 추천한'). 시청 기록이 아니라 추천 기록이라서 "
                 "'본 사람들이 보는'처럼 쓰지 않는다. 표지는 추천작 포스터 바둑판. 작품 카드는 그 작품이 어떤 이야기인지 "
                 "짧게 소개하고, 점수 · 순위 숫자는 쓰지 않는다")
        rows = ctx.rows(bm.entries_of(ctx, picks), bm.WORK_FIELDS)
        # 줄거리 참고 글이 영어라 'Ichigo Kurosaki' 처럼 로마자가 남음 (11/13 시범 귀멸 팬 추천) → 추천작 주연의 한국어 이름을
        # 원어 · 로마자(두 어순) 모두에 묶어 넘긴다
        for b in picks:
            for c in sorted(mains.get(b, {}), key=lambda c: mains[b][c].fact_id)[:4]:
                name = d.cname(c)
                romaji = ctx.ent[c][3]
                if name and romaji:
                    for form in dict.fromkeys([romaji, " ".join(reversed(romaji.split()))]):
                        if all(g["original"] != form for g in d.glossary):
                            d.glossary.append({"original": form, "korean": name, "category": "character"})
        items = []
        for b in picks:
            rank = ranked.index(b) + 1
            subject = '이' if bm.has_batchim(ctx.title[b]) else '가'
            fid = d.fact(f"〈{title}〉{obj} 좋아한 사람들에게 많이 추천된 작품 순위에서 〈{ctx.title[b]}〉{subject} "
                         f"{rank}위다.", Row(rec_row[(work, b)]), "user_recommendation",
                         recommendation_rating=rec[work][b])
            items.append((b, [fid, *bm.add_work_summary(d, b, rows)]))
        eyebrow = f"〈{title}〉 팬들의 추천"
        d.page(work=picks[0], image=("collage",), eyebrow=eyebrow, facts=[items[0][1][0], items[1][1][0]],
               note=f"큰 글씨: 〈{title}〉 좋아한 사람들이 고른 다음 작품이라는 게 바로 보이는 멘트. 본문: 네 작품을 한 문장으로")
        plan = [("kitsu", b, 0) for b in picks]  # 바둑판용 대표 포스터 (네 편)
        lead_src = ctx.work_entries[work][0]
        if ctx.kitsu_entry(work) is not None:
            plan.insert(0, ("kitsu", work, 0))  # 표지 가운데 원작 애니 포스터
        elif lead_src in ctx.anilist:
            plan.insert(0, ("anilist_cover", lead_src, work))
        for b, fids in items:
            # 이 작품이 팬 추천에 k번째로 나오면 k번째 그림 (대표 포스터 → 다른 시즌 · 극장판 표지)
            choices = [e for e in ctx.work_entries[b] if e in ctx.kitsu or e in ctx.anilist]
            lead = ctx.kitsu_entry(b) or (choices[0] if choices else None)
            others = [e for e in choices if e != lead][:5]
            pick_entry = ([lead] + others)[seen_image[b] % (1 + len(others))] if lead else None
            seen_image[b] += 1
            if pick_entry is not None and pick_entry != lead:
                plan.append(("kitsu_one", pick_entry, b) if pick_entry in ctx.kitsu else ("anilist_cover", pick_entry, b))
            d.page(work=b, image=("entry", pick_entry) if pick_entry is not None else ("work", b), eyebrow=eyebrow,
                   title=[ctx.title[b]], facts=fids,
                   note="이 작품이 어떤 이야기인지 한두 문장 (줄거리 참고 글을 새로 씀). 점수 · 순위 숫자는 쓰지 않는다")
        d.plan = plan
        decks.append(d)
    ctx.cur = None
    conn.rollback()
print(f"팬 추천 글감 {len(decks)}개 (규칙: 겹침 {MAX_SHARED}편 · 추천작 {CAP}번 · {POOL}위 안)", flush=True)


def poster_of(d, w: int) -> dict | None:
    """작품의 대표 포스터 (Kitsu 대표 항목 포스터 → AniList 첫 항목 표지)"""
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


def make_like_cover(d) -> bool:
    """시안 C: 추천작 4편 바둑판(어둡게) + 가운데에 원작 애니 포스터 (칸의 1.18배, 카드 42% 높이 중심, 흰 테두리 · 그림자)"""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    source, tiles = poster_of(d, d.works[0]), [poster_of(d, b) for b in d.works[1:]]
    if source is None or any(t is None for t in tiles):
        return False
    W, H = 1308, 1632
    cw, ch = W // 2, H // 2
    canvas = Image.new("RGB", (W, H), (17, 17, 21))
    for k, img in enumerate(tiles[:4]):
        with Image.open(img["local_path"]) as t:
            tile = t.convert("RGB")
        sc = min((cw - 16) / tile.width, (ch - 16) / tile.height, 2.0)
        tile = tile.resize((int(tile.width * sc), int(tile.height * sc)), Image.LANCZOS)
        r, c = divmod(k, 2)
        canvas.paste(tile, (c * cw + (cw - tile.width) // 2, r * ch + (ch - tile.height) // 2))
    canvas = ImageEnhance.Brightness(canvas).enhance(0.55)
    with Image.open(source["local_path"]) as t:
        art = t.convert("RGB")
    sc = min((cw - 16) / art.width, (ch - 16) / art.height) * 1.18
    art = art.resize((int(art.width * sc), int(art.height * sc)), Image.LANCZOS)
    border = 12
    framed = Image.new("RGB", (art.width + border * 2, art.height + border * 2), (255, 255, 255))
    framed.paste(art, (border, border))
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
    titles = [ctx.title[w] for w in d.works]
    d.images.insert(0, {
        **source, "id": f"collage-{tag}", "url": f"{source['url']}{'&' if '?' in source['url'] else '?'}collage={tag}",
        "local_path": str(path), "menu_work_ids": [ctx.menu_id[w] for w in d.works], "related_fact_ids": [],
        "width": W, "height": H, "media_type": "image/png", "file_size_bytes": len(data),
        "fingerprint": hashlib.sha256(data).hexdigest(), "has_alpha": False, "alpha_coverage": None, "subject_box": None,
        "source_description": f"〈{titles[0]}〉 포스터를 가운데에 크게, 추천작 포스터 {len(tiles)}장을 뒤에 깐 표지",
        "depicted_entities": titles,
        "attribution_text": f"공개 Kitsu · AniList 카탈로그 포스터 {len(tiles) + 1}장을 한 장에 모음 (사용 허가 확인과는 별개)"})
    return True
(out / "manifests").mkdir(parents=True, exist_ok=True)
(out / "fixtures").mkdir(parents=True, exist_ok=True)
written = skipped = 0
for d in decks:
    if (out / "manifests" / f"{d.request_id}.json").exists():
        written += 1
        continue
    bm.collect_images(d)
    if not make_like_cover(d):
        bm.make_collage(d)  # 원작 포스터를 못 받으면 추천작 바둑판만
    if not bm.resolve_plan(d):
        skipped += 1
        print(f"건너뜀 {d.request_id}: 장별 계획에 맞는 그림이 모자람 (그림 {len(d.images)}장)", flush=True)
        continue
    written += 1
    (out / "manifests" / f"{d.request_id}.json").write_text(
        json.dumps(d.manifest(), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "fixtures" / f"{d.request_id}.json").write_text(
        json.dumps(d.fixture(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {written} {d.request_id} {d.topic} → {[ctx.title[w] for w in d.works[1:]]}", flush=True)
(out / (("trial_" if args.limit else "") + "manifests_done.flag")).write_text("done", encoding="utf-8")
print(f"끝: 글감 {written}개 · 건너뜀 {skipped}개 → {out}", flush=True)
