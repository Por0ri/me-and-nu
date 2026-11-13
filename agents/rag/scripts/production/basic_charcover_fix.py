"""[캐릭터 표지판 11/13 낮] 오늘 넣을 입문 가이드 중 표지가 캐릭터 · 장면 그림인 글 (암살교실 살생님은 서원 선택이라 뺌)을
아래 규칙 그대로 바꾸기. 원래판(보류 59개) 설명 — 11/13 서원:
  '포스터 · 표지 그림은 괜찮은데 캐릭터 · 장면 그림은 입문 가이드 표지로 별로. AniList 에 그림 많은데 왜 안 써?'
- 글 안에 포스터 · 표지 그림이 있으면 (basic_cover_avoid.preview.json 중 포스터 · 표지 그림) 그 카드와 그림 자리만 맞바꿈
- 없으면 그 작품의 AniList 항목(본편 · 시즌 · OVA · 극장판, 인기순) 표지 중
  · 어떤 글(서버 글 + 오늘 넣을 글)도 표지로 안 쓰고
  · 같은 작품이 나오는 글들의 포스터 표지와 달라 보이는 것 (dHash 64칸 중 SAME 칸 넘게 다름)
  을 받아서 표지 카드 한 장만 다시 그림. 카드 그림엔 글자가 없고 앱이 글자를 얹으므로 글은 그대로
- 묶음은 안 건드림. 결과: scratchpad/basic_charcover_patch.json {글 번호: 고친 꾸러미}
  · .cache/cover_patch_20261113/<글 번호>/card_01.png · 미리보기 .cache/reports/basic_charcover.html
- 공용 AniList 표지 주소 저장 파일(.cache/trial/raw/anilist_covers.json)은 팬 추천 글감 만들기가 쓰는 중이라 읽기만 함
  uv run python -I <이 파일>
"""
import datetime
import hashlib
import html
import io
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

import psycopg
from PIL import Image

import ani_rag.build_manifest as bm
from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.content.agents.content_generation.renderer import render_card_png
from ani_rag.content.layout_engine import build_cards
from ani_rag.content.models import CopyCard, ImageAsset

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
CACHE = PACKAGE_ROOT / ".cache"
NEW, OLD = CACHE / "server_bundles_20261113", CACHE / "server_bundles_20261112"
UPLOAD = CACHE / "server_upload_20261113"
PATCH = CACHE / "cover_patch_20261113"
SRC = PATCH / "src"
SAME = 16  # dHash 거리가 이 값 이하면 같은 그림으로 봄 (같은 그림은 0~5, 다른 그림은 20 넘음. 13 은 다르게 자른 같은 그림일 수 있어 뺌)
MIN_WIDTH = 400  # 이보다 좁은 표지는 크게 띄우면 흐려서, 같은 작품에 더 큰 표지가 있으면 그걸 먼저
VISUAL_KEYS = ("image_id", "visual_mode", "image_explanation", "image_reuse_mode", "layout", "rendered_image_key",
               "rendered_width", "rendered_height")
POSTERISH = ("catalog_poster", "catalog_cover")
KIND_KO = {"catalog_poster": "포스터", "catalog_cover": "표지 그림"}
KIND_NOW = {"character_art": "캐릭터 그림", "scene_still": "장면"}


def package(folder: pathlib.Path, rid: str) -> dict:
    return json.loads((folder / rid / "bundle.json").read_text(encoding="utf-8"))["items"][0]["package"]


def cover_of(p: dict) -> tuple[dict, dict]:
    card = min(p["cards"], key=lambda c: c["page"])
    return card, next((i for i in p["images"] if i["id"] == card["image_id"]), {})


def local_bytes(rid: str, url: str) -> bytes | None:
    """글감 만들 때 받아 둔 그림 → 없으면 이번에 받은 그림 → 없으면 새로 받음 (못 받으면 None)"""
    name = hashlib.sha1(url.encode()).hexdigest()[:16]
    suffix = pathlib.PurePosixPath(urllib.parse.urlsplit(url).path).suffix.lower()[:5]
    for path in (CACHE / "trial" / "images" / rid / (name + suffix), SRC / (name + suffix)):
        if path.exists() and path.stat().st_size > 0:
            return path.read_bytes()
    try:
        data = bm.get_bytes(url)
    except (urllib.error.HTTPError, SystemExit) as exc:
        print(f"    그림 못 받음 ({type(exc).__name__}): {url[:80]}")
        return None
    SRC.mkdir(parents=True, exist_ok=True)
    (SRC / (name + suffix)).write_bytes(data)
    return data


HASHES: dict[str, list[int] | None] = {}


def dhash(key: str, data: bytes | None) -> list[int] | None:
    """그림 전체 · 가운데 80% 두 가지 dHash (테두리 · 여백 차이를 줄이려고)"""
    if key in HASHES:
        return HASHES[key]
    if data is None:
        HASHES[key] = None
        return None
    out = []
    with Image.open(io.BytesIO(data)) as im:
        g = im.convert("L")
    for crop in (None, 0.1):
        x = g if crop is None else g.crop((int(g.width * crop), int(g.height * crop),
                                           int(g.width * (1 - crop)), int(g.height * (1 - crop))))
        px = list(x.resize((9, 8), Image.LANCZOS).getdata())
        bits = 0
        for r in range(8):
            for c in range(8):
                bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
        out.append(bits)
    HASHES[key] = out
    return out


def distance(a: list[int], b: list[int]) -> int:
    return min(bin(x ^ y).count("1") for x in a for y in b)


def anilist_cover_urls(aids: list[str]) -> dict[str, str]:
    """AniList 작품 번호 → 표지 주소. 공용 저장 파일은 읽기만 하고, 없는 것만 따로 물어서 scratchpad 에 저장"""
    mine_file = HERE / "anilist_covers_patch.json"
    known: dict[str, str] = json.loads(mine_file.read_text(encoding="utf-8")) if mine_file.exists() else {}
    shared: dict[str, str] = {}
    for _ in range(3):
        try:
            shared = json.loads((CACHE / "trial" / "raw" / "anilist_covers.json").read_text(encoding="utf-8"))
            break
        except (json.JSONDecodeError, OSError):
            time.sleep(1)  # 팬 추천 글감 만들기가 막 쓰는 중
    missing = sorted({a for a in aids if not known.get(a) and not shared.get(a)}, key=int)
    query = ("query ($ids: [Int]) { Page(page: 1, perPage: 50) { media(id_in: $ids, type: ANIME) "
             "{ id coverImage { extraLarge large } } } }")
    for s in range(0, len(missing), 50):
        body = json.dumps({"query": query, "variables": {"ids": [int(i) for i in missing[s:s + 50]]}}).encode()
        req = urllib.request.Request("https://graphql.anilist.co", data=body, headers={
            "User-Agent": bm.UA, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                data = json.loads(res.read())
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"AniList 가 {exc.code} 를 돌려줌 → 멈춤 (우회하지 않음)")
        for m in data["data"]["Page"]["media"]:
            cover = m.get("coverImage") or {}
            known[str(m["id"])] = cover.get("extraLarge") or cover.get("large") or ""
        mine_file.write_text(json.dumps(known, ensure_ascii=False), encoding="utf-8")
        time.sleep(bm.ANILIST_PAUSE)
    return {a: known.get(a) or shared.get(a) or "" for a in aids}


# ── 표지로 쓰이는 그림 (서버 글 + 오늘 넣을 글) ─────────────────────────────────
held = {}
in_post = {}
cover_keys, cover_srcs = set(), set()
posters_by_menu: dict[int, list[tuple[str, dict]]] = defaultdict(list)  # 작품 → 그 작품이 나오는 글들의 포스터 표지
for folder, kept_file in ((OLD, CACHE / "server_upload_20261112" / "kept.json"), (NEW, UPLOAD / "kept.json")):
    for r in json.loads(kept_file.read_text(encoding="utf-8")):
        card, im = cover_of(package(folder, r))
        cover_keys.add(im.get("fingerprint") or im.get("source_url") or card["image_id"])
        cover_srcs.add(im.get("source_url"))
        if im.get("asset_kind") in POSTERISH and not str(im.get("id", "")).startswith(("collage-", "player-")):
            for m in im.get("menu_work_ids") or []:
                posters_by_menu[m].append((r, im))

with psycopg.connect(load_settings().dict_conninfo) as conn, conn.cursor() as cur:
    ctx = bm.Ctx(cur)
    conn.rollback()
by_menu = {v: k for k, v in ctx.menu_id.items()}

# 새 AniList 표지 그림 틀: 이미 서버에 넣는 입문 가이드의 AniList 표지 그림 하나를 본뜸 (필드 모양을 같게)
template = None
for p in sorted(NEW.glob("dict-guide-basic-*")):
    for im in package(NEW, p.name)["images"]:
        if (im.get("source_url") or "").startswith("https://anilist.co/anime/") and im.get("asset_kind") == "catalog_cover":
            template = im
            break
    if template:
        break
assert template is not None, "본뜰 AniList 표지 그림이 없음"

basic = []
for r in json.loads((UPLOAD / "kept.json").read_text(encoding="utf-8")):
    if "-basic-" in r and r != "dict-guide-basic-w17":
        p0 = package(NEW, r)
        c0, im0 = cover_of(p0)
        if im0.get("asset_kind") in ("character_art", "scene_still"):
            basic.append(r)
            held[r] = im0.get("asset_kind")
            # 글 안 포스터 · 표지 그림 (원작 책 표지는 뺌, 다른 글 표지로 안 쓰는 것, 포스터 먼저 · 앞 장 먼저)
            opts = []
            for c in sorted(p0["cards"], key=lambda c: c["page"])[1:]:
                ii = next((i for i in p0["images"] if i["id"] == c["image_id"]), {})
                kk = ii.get("fingerprint") or ii.get("source_url")
                if ii.get("asset_kind") in POSTERISH and kk not in cover_keys and "/manga/" not in (ii.get("source_url") or ""):
                    opts.append(((0 if ii["asset_kind"] == "catalog_poster" else 1), c["page"], ii["asset_kind"], kk))
            if opts:
                _, pg, kind, kk = min(opts)
                in_post[r] = {"page": pg, "kind": kind}
                cover_keys.add(kk)
basic.sort()
print(f"표지가 캐릭터 · 장면 그림인 입문 가이드 {len(basic)}개 · 글 안 포스터 · 표지 그림 있는 것 {len(in_post)}개")
# ── 글마다 표지 고르기 ─────────────────────────────────────────────────────────
need_anilist = [r for r in basic if (in_post.get(r) or {}).get("kind") not in POSTERISH]
aids_all = []
for r in need_anilist:
    work = by_menu[int(r.rsplit("-w", 1)[1])]
    aids_all += [ctx.anilist[e] for e in ctx.work_entries.get(work, []) if e in ctx.anilist]
urls = anilist_cover_urls(aids_all)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
patched, rows, outcome = {}, [], Counter()
for rid in basic:
    p = package(NEW, rid)
    menu = int(rid.rsplit("-w", 1)[1])
    work = by_menu[menu]
    title = ctx.title[work]
    cards = sorted(p["cards"], key=lambda c: c["page"])
    cover = cards[0]
    alt = in_post.get(rid) or {}
    if alt.get("kind") in POSTERISH:  # 글 안 포스터 · 표지 그림과 자리 맞바꿈 (서원: 이건 괜찮음)
        other = next(c for c in cards if c["page"] == alt["page"])
        for k in VISUAL_KEYS:
            cover[k], other[k] = other.get(k), cover.get(k)
        _, im = cover_of(p)
        cover_keys.add(im.get("fingerprint") or im.get("source_url"))
        patched[rid] = p
        outcome["글 안 " + KIND_KO[alt["kind"]]] += 1
        rows.append((rid, title, f"server_bundles_20261113/{rid}/cards/card_{alt['page']:02d}.png",
                     f"글 안 {KIND_KO[alt['kind']]} ({alt['page']}장 그림)과 자리 바꿈", cover, ""))
        continue
    # 같은 작품이 나오는 글들의 포스터 표지 (지금 겹치는 서버 글 표지 포함)
    _, held_im = cover_of(package(NEW, rid))
    refs = [(rid, held_im)] + posters_by_menu.get(menu, [])
    ref_hashes = [h for h in (dhash(im.get("fingerprint") or im["url"], local_bytes(r, im["url"])) for r, im in refs
                              if im.get("url")) if h]
    ok, notes = [], []
    for e in ctx.work_entries.get(work, []):
        aid = ctx.anilist.get(e)
        if not aid:
            continue
        label = ctx.ent[e][1] or ctx.ent[e][3] or title
        src = f"https://anilist.co/anime/{aid}"
        if src in cover_srcs:
            notes.append(f"{label}: 다른 글 표지로 이미 씀")
            continue
        url = urls.get(aid)
        if not url or "/default" in url:
            notes.append(f"{label}: AniList 표지 없음")
            continue
        data = local_bytes(rid, url)
        h = dhash(url, data)
        if h is None:
            continue
        d = min((distance(h, r) for r in ref_hashes), default=64)
        if d <= SAME:
            notes.append(f"{label}: 같은 작품 글 표지와 비슷함 (거리 {d})")
            continue
        with Image.open(io.BytesIO(data)) as im:
            w = im.width
        ok.append((e, aid, label, src, url, data, d, w))
        if w >= MIN_WIDTH:
            break  # 큰 표지를 찾았으면 더 안 받음
    # 인기순으로 첫 번째 큰 표지, 큰 게 없으면 작은 것 중 첫 번째
    chosen = next((c for c in ok if c[7] >= MIN_WIDTH), ok[0] if ok else None)
    if chosen is None:
        outcome["지금 표지 그대로 (쓸 만한 AniList 표지 없음)"] += 1
        rows.append((rid, title, "", "지금 표지 그대로: " + " · ".join(notes or ["AniList 항목 없음"]), cover, ""))
        continue
    e, aid, label, src, url, data, d, _ = chosen
    notes += [f"{c[2]}: 더 앞 순위지만 그림이 작음 ({c[7]}px)" for c in ok[:ok.index(chosen)]]
    with Image.open(io.BytesIO(data)) as im:
        width, height, fmt = im.width, im.height, im.format
    fp = hashlib.sha256(data).hexdigest()
    img_id = "anilist-" + hashlib.sha1(url.encode()).hexdigest()[:16]
    new_img = json.loads(json.dumps(template))
    new_img.update({
        "id": img_id, "url": url, "local_path": None, "storage_key": fp,
        "media_type": {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(fmt, "image/jpeg"),
        "file_size_bytes": len(data), "source_url": src, "menu_work_ids": [menu], "width": width, "height": height,
        "fingerprint": fp, "used_in_previous_content": False, "related_fact_ids": [],
        "source_description": f"AniList catalog poster · {label}", "depicted_entities": [title],
        "rights_evidence_url": src, "rights_checked_at": now,
        "attribution_text": f"공개 AniList 카탈로그 이미지 참고: {label} — catalog poster (사용 허가 확인과는 별개)"})
    new_img["provenance_source"] = {**template["provenance_source"], "title": f"{label} — catalog poster", "url": src}
    asset = ImageAsset.model_validate(new_img)
    copy = CopyCard.model_validate({**{k: v for k, v in cover.items() if k != "layout"}, "image_id": img_id,
                                    "image_explanation": f"〈{title}〉 작품 포스터 이미지."})
    card = build_cards([copy], [asset], art_directions={1: {"background_style": cover["layout"]["background_style"]}})[0]
    png = render_card_png(None, card, asset, data)
    key = hashlib.sha256(png).hexdigest()
    (PATCH / rid).mkdir(parents=True, exist_ok=True)
    (PATCH / rid / "card_01.png").write_bytes(png)
    cover.update({"image_id": img_id, "image_explanation": copy.image_explanation, "visual_mode": "source_image",
                  "layout": card.layout.model_dump(mode="json"), "rendered_image_key": key,
                  "rendered_width": 1308, "rendered_height": 1632})
    if all(i["id"] != img_id for i in p["images"]):
        p["images"].append(new_img)
    cover_keys.add(fp)
    cover_srcs.add(src)
    patched[rid] = p
    outcome["AniList 새 표지"] += 1
    rows.append((rid, title, f"cover_patch_20261113/{rid}/card_01.png",
                 f"AniList 새 표지: {label} ({width}×{height}) · 같은 작품 글 표지와 거리 {d}", cover,
                 " · ".join(notes)))

(HERE / "basic_charcover_patch.json").write_text(json.dumps(patched, ensure_ascii=False), encoding="utf-8")
print("결과:", dict(outcome))

# ── 미리보기 ──────────────────────────────────────────────────────────────────
figs = []
for rid, title, new_src, why, cover, notes in rows:
    srv = held[rid]
    t = "<br>".join(html.escape(x) for x in cover.get("title_lines") or [])
    body = " ".join(html.escape(s) for s in cover.get("body_sentences") or [])
    new_html = (f'<div class=frame><img loading=lazy src="../{new_src}" alt=""><div class=ov><div class=eb>'
                f'{html.escape(cover.get("eyebrow") or "")}</div><div class=tt>{t}</div><div class=bd>{body}</div></div></div>'
                if new_src else '<div class="frame empty">지금 표지 그대로</div>')
    figs.append(f"""<figure class="{'' if new_src else 'keep'}"><div class=pair>
<div><div class=cap>지금 표지 ({KIND_NOW.get(srv, srv)})</div><div class=frame><img loading=lazy src="../server_bundles_20261113/{rid}/cards/card_01.png" alt=""></div></div>
<div><div class=cap>새 표지</div>{new_html}</div></div>
<figcaption><b>{html.escape(title)}</b> <span class=muted>· {html.escape(rid)}</span><br>{html.escape(why)}
{f'<br><span class=muted>건너뛴 표지: {html.escape(notes)}</span>' if notes else ''}</figcaption></figure>""")
summary = " · ".join(f"{k} {v}" for k, v in outcome.items())
(CACHE / "reports" / "basic_charcover.html").write_text(f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>입문 가이드 새 표지</title><style>
:root{{--bg:#f7f7f8;--card:#fff;--text:#1d1d22;--muted:#6b6b76;--line:#e4e4ea;--warn:#b45309}}
@media (prefers-color-scheme: dark){{:root{{--bg:#141418;--card:#1e1e24;--text:#ececf1;--muted:#a0a0ad;--line:#2e2e37;--warn:#f0a050}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}}
main{{max-width:1300px;margin:0 auto;padding:20px 16px 60px}} h1{{font-size:22px;margin:0 0 4px}} .muted{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}}
figure{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px}}
figure.keep{{border-color:var(--warn)}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:8px}} .cap{{font-size:12px;color:var(--muted);margin-bottom:3px}}
.frame{{position:relative;aspect-ratio:1308/1632;border-radius:9px;overflow:hidden;background:#000}}
.frame.empty{{display:grid;place-items:center;color:#aaa;background:#333}}
.frame img{{width:100%;height:100%;display:block;object-fit:cover}}
.ov{{position:absolute;left:6%;right:6%;top:66%;color:#fff;text-shadow:0 1px 4px #000c}}
.eb{{font-size:9px;opacity:.9}} .tt{{font-size:13px;font-weight:800;margin:2px 0;line-height:1.3}} .bd{{font-size:10px;line-height:1.4}}
figcaption{{margin-top:6px;font-size:12px}}</style></head><body><main>
<h1>입문 가이드 캐릭터 · 장면 표지 {len(rows)}개 — 새 표지</h1>
<p class=muted>{html.escape(summary)}. 왼쪽은 지금 표지, 오른쪽이 새 표지 (글자는 앱처럼 얹은 흉내). 캐릭터 · 장면 그림은 안 씀. 아직 묶음은 안 바꿈.</p>
<div class=grid>{''.join(figs)}</div></main></body></html>""", encoding="utf-8")
print(CACHE / "reports" / "basic_charcover.html")
