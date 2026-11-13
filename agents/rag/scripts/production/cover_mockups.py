"""주연 소개 · 주연 생일 모음 표지 예시 (11/13 서원: 바둑판 / 작품 사진 / 바둑판 어둡게 + 가운데 원작 포스터 비교) — 돈 안 듦
- 남은 글(서버에 아직 그 유형 글이 없는 작품) 중 인기순 3편씩, 실제 글감 만들기와 같은 규칙으로 주연 4명을 고름
  · 주연 소개: 소개 참고 글이 있는 주연 (글감 만들기 pick_character 와 같음)
  · 생일 모음: 생일이 있는 주연 중 인기 4명 (pick_birthday_book 과 같음)
- 표지 세 가지 (1308×1632, 실제 글처럼 프로젝트 렌더러로 그림 → 앱처럼 글자를 HTML 로 얹음)
  A 바둑판: 주연 4명 AniList 캐릭터 그림 2×2
  B 작품 사진: 그 작품 AniList 표지 중 다른 글 표지로 안 쓰고 서버 표지와 달라 보이는 것 (입문 가이드 표지 바꾸기와 같은 규칙)
  C 바둑판 어둡게 + 가운데 원작 포스터: 팬 추천 시안 C 와 같은 방식 (바둑판 칸만 캐릭터 그림)
- 글자는 서버 글 표지 말투를 본뜬 예시 (실제 글은 AI 가 씀)
- Kitsu 응답 저장 파일을 팬 추천 글감 만들기와 같이 쓸 수 있어서 저장은 통째 바꾸기로 함
  uv run python -I <이 파일>   → .cache/reports/cover_mockups_intro_bday.html
"""
import hashlib
import html
import io
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import psycopg
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

sys.stdout.reconfigure(encoding="utf-8")


def _atomic_write_text(self, data, encoding=None, errors=None, newline=None):
    tmp = self.with_name(f"{self.name}.tmp{os.getpid()}")
    with open(tmp, "w", encoding=encoding, errors=errors, newline=newline) as f:
        n = f.write(data)
    for attempt in range(50):
        try:
            os.replace(tmp, self)
            return n
        except PermissionError:
            time.sleep(0.1 + 0.05 * attempt)
    os.replace(tmp, self)
    return n


pathlib.Path.write_text = _atomic_write_text

import ani_rag.build_manifest as bm  # noqa: E402
from ani_rag.config import PACKAGE_ROOT, load_settings  # noqa: E402
from ani_rag.content.agents.content_generation.renderer import render_card_png  # noqa: E402
from ani_rag.content.layout_engine import build_cards  # noqa: E402
from ani_rag.content.models import CopyCard, ImageAsset  # noqa: E402

HERE = pathlib.Path(__file__).parent
CACHE = PACKAGE_ROOT / ".cache"
OUT = CACHE / "cover_mockups_20261113"
SRC = OUT / "src"
REPORT = CACHE / "reports" / "cover_mockups_intro_bday.html"
W, H = 1308, 1632
SAME = 16
N_WORKS = 3


def fetch(url: str) -> bytes | None:
    name = hashlib.sha1(url.encode()).hexdigest()[:16]
    path = SRC / name
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    try:
        data = bm.get_bytes(url)
    except (urllib.error.HTTPError, SystemExit) as exc:
        print(f"  그림 못 받음 ({type(exc).__name__}): {url[:80]}")
        return None
    SRC.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def open_rgb(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as im:
        return im.convert("RGB")


def fill(img: Image.Image, w: int, h: int, top: float = 0.18) -> Image.Image:
    """칸을 꽉 채우게 자름 (얼굴이 위쪽에 있어서 세로로 자를 땐 위쪽 18% 지점 기준)"""
    sc = max(w / img.width, h / img.height)
    r = img.resize((max(w, round(img.width * sc)), max(h, round(img.height * sc))), Image.LANCZOS)
    x = (r.width - w) // 2
    y = int((r.height - h) * top)
    return r.crop((x, y, x + w, y + h))


def grid(portraits: list[Image.Image], gap: int = 8) -> Image.Image:
    canvas = Image.new("RGB", (W, H), (17, 17, 21))
    cw, ch = (W - gap) // 2, (H - gap) // 2
    for k, p in enumerate(portraits[:4]):
        r, c = divmod(k, 2)
        canvas.paste(fill(p, cw, ch), (c * (cw + gap), r * (ch + gap)))
    return canvas


def grid_with_poster(portraits: list[Image.Image], poster: Image.Image) -> Image.Image:
    """팬 추천 시안 C 와 같은 모양: 바둑판 어둡게(0.55) + 가운데 포스터 (칸의 1.18배, 흰 테두리 · 그림자, 42% 높이)"""
    canvas = ImageEnhance.Brightness(grid(portraits)).enhance(0.55)
    cw, ch = W // 2, H // 2
    sc = min((cw - 16) / poster.width, (ch - 16) / poster.height) * 1.18
    art = poster.resize((int(poster.width * sc), int(poster.height * sc)), Image.LANCZOS)
    border = 12
    framed = Image.new("RGB", (art.width + border * 2, art.height + border * 2), (255, 255, 255))
    framed.paste(art, (border, border))
    x, y = (W - framed.width) // 2, max(16, int(H * 0.42 - framed.height / 2))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x + 10, y + 18, x + framed.width + 10, y + framed.height + 18), 18,
                                             fill=(0, 0, 0, 170))
    out = canvas.convert("RGBA")
    out.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))
    out = out.convert("RGB")
    out.paste(framed, (x, y))
    return out


def render_cover(img: Image.Image | bytes, name: str) -> str:
    """프로젝트 렌더러로 표지 카드 그림 (실제 글과 같은 배치 · 아래쪽 글자 자리 처리)"""
    if isinstance(img, Image.Image):
        buf = io.BytesIO()
        img.save(buf, "PNG")
        data = buf.getvalue()
    else:
        data = img
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
    asset = ImageAsset.model_validate({"id": "mock", "url": "https://example.invalid/mock.png",
                                       "source_url": "https://example.invalid/", "width": w, "height": h})
    copy = CopyCard.model_validate({"page": 1, "role": "cover", "eyebrow": "", "title_lines": ["예시"],
                                    "body_sentences": ["예시"], "fact_ids": [], "image_id": "mock",
                                    "visual_mode": "source_image", "image_explanation": "예시"})
    card = build_cards([copy], [asset])[0]
    png = render_card_png(None, card, asset, data)
    (OUT / name).write_bytes(png)
    return name


def anilist_cover_urls(aids: list[str]) -> dict[str, str]:
    mine_file = HERE / "anilist_covers_patch.json"
    known = json.loads(mine_file.read_text(encoding="utf-8")) if mine_file.exists() else {}
    shared = {}
    for _ in range(3):
        try:
            shared = json.loads((CACHE / "trial" / "raw" / "anilist_covers.json").read_text(encoding="utf-8"))
            break
        except (json.JSONDecodeError, OSError):
            time.sleep(1)
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


def dhash(data: bytes) -> list[int]:
    out = []
    g = open_rgb(data).convert("L")
    for crop in (None, 0.1):
        x = g if crop is None else g.crop((int(g.width * crop), int(g.height * crop),
                                           int(g.width * (1 - crop)), int(g.height * (1 - crop))))
        px = list(x.resize((9, 8), Image.LANCZOS).getdata())
        bits = 0
        for r in range(8):
            for c in range(8):
                bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
        out.append(bits)
    return out


def distance(a: list[int], b: list[int]) -> int:
    return min(bin(x ^ y).count("1") for x in a for y in b)


# ── 지금 표지로 쓰이는 그림 (서버 글 + 오늘 넣을 글) ─────────────────────────
def pkg(folder: pathlib.Path, rid: str) -> dict:
    return json.loads((folder / rid / "bundle.json").read_text(encoding="utf-8"))["items"][0]["package"]


cover_srcs, posters_by_menu = set(), {}
server_ids = set(json.loads((CACHE / "server_upload_20261112" / "kept.json").read_text(encoding="utf-8")))
for folder, kept_file in ((CACHE / "server_bundles_20261112", CACHE / "server_upload_20261112" / "kept.json"),
                          (CACHE / "server_bundles_20261113", CACHE / "server_upload_20261113" / "kept.json")):
    for r in json.loads(kept_file.read_text(encoding="utf-8")):
        p = pkg(folder, r)
        card = min(p["cards"], key=lambda c: c["page"])
        im = next((i for i in p["images"] if i["id"] == card["image_id"]), {})
        cover_srcs.add(im.get("source_url"))
        if im.get("asset_kind") in ("catalog_poster", "catalog_cover") and not str(im.get("id")).startswith("collage-"):
            for m in im.get("menu_work_ids") or []:
                posters_by_menu.setdefault(m, []).append((r, im))

with psycopg.connect(load_settings().dict_conninfo) as conn, conn.cursor() as cur:
    ctx = bm.Ctx(cur)
    mains = bm.main_casts(ctx)
    pool = ctx.order[:bm.top(300)]
    described = ctx.rows({c for w in pool for c in mains.get(w, {})}, ("character_description_ref",))
    bday_rows = {w: ctx.rows(mains.get(w, {}), ("birthday", "character_favourites")) for w in pool}
    conn.rollback()


def has_desc(c: int) -> bool:
    found = described.get((c, "character_description_ref"))
    return bool(found) and len(bm.ref_text(bm.PROFILE_LINE.sub("", str(found[0].value)))) >= 80


picks = {"intro": [], "bdaybook": []}
for w in pool:
    menu = ctx.menu_id[w]
    if not ctx.ok_work(w):
        continue
    if len(picks["intro"]) < N_WORKS and f"dict-character-intro-w{menu}" not in server_ids:
        cids = [c for c in sorted(mains.get(w, {}), key=lambda c: mains[w][c].fact_id) if c in ctx.anilist and has_desc(c)][:4]
        if len(cids) >= 4:
            picks["intro"].append((w, cids))
    if len(picks["bdaybook"]) < N_WORKS and f"dict-character-bdaybook-w{menu}" not in server_ids:
        rows = bday_rows[w]
        have = [c for c in mains.get(w, {}) if (c, "birthday") in rows and c in ctx.anilist]
        if len(have) >= 4:
            fav = {c: rows[(c, "character_favourites")][0].value for c in have if (c, "character_favourites") in rows}
            chosen = sorted(have, key=lambda c: (-fav.get(c, 0), mains[w][c].fact_id))[:4]
            dates = {c: rows[(c, "birthday")][0].value for c in chosen}
            chosen.sort(key=lambda c: (dates[c]["month"], dates[c]["day"]))
            picks["bdaybook"].append((w, chosen))
    if all(len(v) >= N_WORKS for v in picks.values()):
        break

portrait_urls = bm.anilist_portraits([ctx.anilist[c] for v in picks.values() for _, cs in v for c in cs])
OUT.mkdir(parents=True, exist_ok=True)
LABEL = {"intro": "주연 소개", "bdaybook": "주연 생일 모음"}
COPY = {  # 서버 글 표지 말투를 본뜬 예시 글자 (실제 글은 AI 가 씀)
    "intro": lambda t: (f"〈{t}〉 주연 소개", [f"〈{t}〉", "주연들을 만나 보자"], "이 이야기를 끌고 가는 네 사람, 한 장에 한 명씩."),
    "bdaybook": lambda t: (f"〈{t}〉 주연 생일 모음", [f"〈{t}〉", "주연 네 명 생일표"], "날짜마다 이름 하나씩, 달력에 적어 두자."),
}
sections = []
for kind, items in picks.items():
    rows_html = []
    for w, cids in items:
        title, menu = ctx.title[w], ctx.menu_id[w]
        names = [ctx.char_name(c) or "" for c in cids]
        portraits = [open_rgb(d) for d in (fetch(portrait_urls.get(ctx.anilist[c], "")) for c in cids
                                            if portrait_urls.get(ctx.anilist[c])) if d]
        sizes = sorted({f"{p.width}×{p.height}" for p in portraits})
        # 원작 포스터 (팬 추천과 같음: Kitsu 대표 포스터)
        kid = ctx.kitsu[ctx.kitsu_entry(w)]
        attrs = bm.kitsu_json(kid)["data"]["attributes"]
        poster_url = (attrs.get("posterImage") or {}).get("original") or (attrs.get("posterImage") or {}).get("large")
        poster_data = fetch(poster_url) if poster_url else None
        # 작품 사진 후보: AniList 항목 표지 중 다른 글 표지로 안 쓰고, 이 작품 글 표지들 · Kitsu 포스터와 달라 보이는 것
        refs = [dhash(poster_data)] if poster_data else []
        for r, im in posters_by_menu.get(menu, []):
            d = fetch(im["url"]) if im.get("url") else None
            if d:
                refs.append(dhash(d))
        aids = [ctx.anilist[e] for e in ctx.work_entries.get(w, []) if e in ctx.anilist]
        urls = anilist_cover_urls(aids)
        work_photo, work_note = None, "쓸 만한 AniList 표지 없음 (표지가 1장뿐이고 이미 쓰는 그림과 같음)"
        for e in ctx.work_entries.get(w, []):
            aid = ctx.anilist.get(e)
            if not aid or f"https://anilist.co/anime/{aid}" in cover_srcs or not urls.get(aid) or "/default" in urls[aid]:
                continue
            d = fetch(urls[aid])
            if d is None or min((distance(dhash(d), r) for r in refs), default=64) <= SAME:
                continue
            label = ctx.ent[e][1] or ctx.ent[e][3]
            work_photo = d
            with Image.open(io.BytesIO(d)) as im:
                work_note = f"AniList 표지: {label} ({im.width}×{im.height})"
            break
        tag = f"{kind}-w{menu}"
        a = render_cover(grid(portraits), f"{tag}-A.png") if len(portraits) >= 4 else None
        b = render_cover(work_photo, f"{tag}-B.png") if work_photo else None
        c = render_cover(grid_with_poster(portraits, open_rgb(poster_data)), f"{tag}-C.png") \
            if len(portraits) >= 4 and poster_data else None
        eyebrow, lines, body = COPY[kind](title)

        def fig(name: str | None, cap: str, note: str) -> str:
            if not name:
                return f'<figure><div class="frame empty">{html.escape(note)}</div><figcaption><b>{cap}</b></figcaption></figure>'
            return (f'<figure><div class=frame><img src="../cover_mockups_20261113/{name}" alt="">'
                    f'<div class=ov><div class=eb>{html.escape(eyebrow)}</div><div class=tt>'
                    f'{"<br>".join(html.escape(x) for x in lines)}</div><div class=bd>{html.escape(body)}</div></div></div>'
                    f'<figcaption><b>{cap}</b><br><span class=muted>{html.escape(note)}</span></figcaption></figure>')

        rows_html.append(
            f'<h3>〈{html.escape(title)}〉 <span class=muted>· 주연 {html.escape(", ".join(names))}</span></h3><div class=row>'
            + fig(a, "A 바둑판", f"캐릭터 그림 {', '.join(sizes)} → 칸에 맞춰 키움")
            + fig(b, "B 작품 사진", work_note)
            + fig(c, "C 바둑판 어둡게 + 가운데 원작 포스터", "팬 추천과 같은 방식")
            + "</div>")
        print(f"{LABEL[kind]} 〈{title}〉: A {'○' if a else '×'} · B {'○' if b else '×'} ({work_note}) · C {'○' if c else '×'}")
    sections.append(f"<h2>{LABEL[kind]}</h2>" + "".join(rows_html))

REPORT.write_text(f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>주연 소개 · 생일 모음 표지 예시</title><style>
:root{{--bg:#f7f7f8;--card:#fff;--text:#1d1d22;--muted:#6b6b76;--line:#e4e4ea}}
@media (prefers-color-scheme: dark){{:root{{--bg:#141418;--card:#1e1e24;--text:#ececf1;--muted:#a0a0ad;--line:#2e2e37}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:20px 16px 60px}} h1{{font-size:22px;margin:0 0 4px}} h2{{margin:30px 0 4px}}
h3{{font-size:15px;margin:18px 0 8px}} .muted{{color:var(--muted)}}
.row{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
@media (max-width:700px){{.row{{grid-template-columns:1fr 1fr}}}}
figure{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px}}
.frame{{position:relative;aspect-ratio:1308/1632;border-radius:9px;overflow:hidden;background:#000}}
.frame.empty{{display:grid;place-items:center;color:#bbb;background:#333;padding:12px;text-align:center;font-size:12px}}
.frame img{{width:100%;height:100%;display:block;object-fit:cover}}
.ov{{position:absolute;left:6%;right:6%;top:66%;color:#fff;text-shadow:0 1px 4px #000c}}
.eb{{font-size:10px;opacity:.9}} .tt{{font-size:15px;font-weight:800;margin:2px 0;line-height:1.3}} .bd{{font-size:11px;line-height:1.4}}
figcaption{{margin-top:6px;font-size:12px}}</style></head><body><main>
<h1>주연 소개 · 주연 생일 모음 — 표지 예시</h1>
<p class=muted>아직 그 유형 글이 서버에 없는 작품 중 인기순, 실제 글감 만들기와 같은 규칙으로 주연 4명을 고름. 표지는 실제 글처럼 프로젝트 렌더러로 그렸고,
글자는 서버 글 표지 말투를 본뜬 예시 (실제 글은 AI 가 씀). 돈 안 드는 예시라 글은 안 만듦.</p>
{''.join(sections)}</main></body></html>""", encoding="utf-8")
print(REPORT)
