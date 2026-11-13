"""보류된 입문 가이드(서버 글과 표지 같음) 미리보기 (읽기만, 묶음은 안 건드림)
그 글의 다른 카드 중 아무 글도 표지로 안 쓰는 그림을 표지 후보로 고름: 포스터 > 표지 그림 > 캐릭터 그림 > 장면 (같은 순위면 앞 장)
→ scratchpad/basic_cover_avoid.preview.json {글 번호: {page, source_url, kind}} · .cache/reports/basic_held_covers.html
서원 OK 뒤에만 bundles_v3 fix_cover 가 이 파일을 읽게 바꾸고 그 글들만 다시 묶음
  python -I <이 파일>
"""
import html
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
CACHE = pathlib.Path(__file__).resolve().parents[2] / ".cache"
NEW, OLD = CACHE / "server_bundles_20261113", CACHE / "server_bundles_20261112"
UPLOAD = CACHE / "server_upload_20261113"
ORDER = {"catalog_poster": 0, "catalog_cover": 1, "character_art": 2, "scene_still": 3}
KIND_KO = {"catalog_poster": "포스터", "catalog_cover": "표지 그림", "character_art": "캐릭터 그림", "scene_still": "장면"}


def package(folder: pathlib.Path, rid: str) -> dict:
    return json.loads((folder / rid / "bundle.json").read_text(encoding="utf-8"))["items"][0]["package"]


def key_of(p: dict, card: dict) -> tuple[str, dict]:
    im = next((i for i in p["images"] if i["id"] == card["image_id"]), {})
    return im.get("fingerprint") or im.get("source_url") or card["image_id"], im


def cover_key(p: dict) -> str:
    return key_of(p, min(p["cards"], key=lambda c: c["page"]))[0]


taken = {cover_key(package(OLD, r)) for r in json.loads((CACHE / "server_upload_20261112" / "kept.json").read_text(encoding="utf-8"))}
taken |= {cover_key(package(NEW, r)) for r in json.loads((UPLOAD / "kept.json").read_text(encoding="utf-8"))}
held = json.loads((UPLOAD / "held_cover_server.json").read_text(encoding="utf-8"))
plan, figs = {}, []
for rid in sorted(r for r in held if "-basic-" in r):
    p = package(NEW, rid)
    cards = sorted(p["cards"], key=lambda c: c["page"])
    options = []
    for c in cards[1:]:
        k, im = key_of(p, c)
        if k not in taken and im.get("asset_kind") in ORDER and "/manga/" not in (im.get("source_url") or ""):
            options.append((ORDER[im["asset_kind"]], c["page"], c, k, im))
    if not options:
        continue
    _, page, card, k, im = min(options, key=lambda o: (o[0], o[1]))
    taken.add(k)
    plan[rid] = {"page": page, "source_url": im.get("source_url"), "kind": im.get("asset_kind")}
    cover = cards[0]
    title = "<br>".join(html.escape(t) for t in cover.get("title_lines") or [])
    body = " ".join(html.escape(s) for s in cover.get("body_sentences") or [])
    figs.append(f"""<figure><div class=pair>
<div><div class=cap>지금 (보류)</div><div class=frame><img loading=lazy src="../server_bundles_20261113/{rid}/cards/card_01.png" alt=""></div></div>
<div><div class=cap>바꾸면 ({html.escape(KIND_KO[im['asset_kind']])}, {page}장 그림)</div><div class=frame><img loading=lazy src="../server_bundles_20261113/{rid}/cards/card_{page:02d}.png" alt="">
<div class=ov><div class=eb>{html.escape(cover.get('eyebrow') or '')}</div><div class=tt>{title}</div><div class=bd>{body}</div></div></div></div>
</div><figcaption><b>{html.escape(p.get('topic') or rid)}</b> <span class=muted>· 겹친 서버 글 {html.escape(held[rid])}</span></figcaption></figure>""")
(HERE / "basic_cover_avoid.preview.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
(CACHE / "reports" / "basic_held_covers.html").write_text(f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>입문 가이드 보류 표지</title><style>
:root{{--bg:#f7f7f8;--card:#fff;--text:#1d1d22;--muted:#6b6b76;--line:#e4e4ea}}
@media (prefers-color-scheme: dark){{:root{{--bg:#141418;--card:#1e1e24;--text:#ececf1;--muted:#a0a0ad;--line:#2e2e37}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}}
main{{max-width:1300px;margin:0 auto;padding:20px 16px 60px}} h1{{font-size:22px;margin:0 0 4px}} .muted{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}}
figure{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:8px}} .cap{{font-size:12px;color:var(--muted);margin-bottom:3px}}
.frame{{position:relative;aspect-ratio:1308/1632;border-radius:9px;overflow:hidden;background:#000}}
.frame img{{width:100%;height:100%;display:block;object-fit:cover}}
.ov{{position:absolute;left:6%;right:6%;top:66%;color:#fff;text-shadow:0 1px 4px #000c}}
.eb{{font-size:9px;opacity:.9}} .tt{{font-size:13px;font-weight:800;margin:2px 0;line-height:1.3}} .bd{{font-size:10px;line-height:1.4}}
figcaption{{margin-top:6px;font-size:12px}}</style></head><body><main>
<h1>입문 가이드 보류 {len(figs)}개 — 표지를 다른 카드 그림으로 바꾸면</h1>
<p class=muted>서버에 이미 있는 글과 표지 그림이 같아서 빠진 글. 오른쪽은 그 글 안의 다른 카드 그림을 표지로 올린 모습 (글자는 앱처럼 얹은 흉내). 아직 묶음은 안 바꿈.</p>
<div class=grid>{''.join(figs)}</div></main></body></html>""", encoding="utf-8")
from collections import Counter
print(f"보류 입문 가이드 {len(plan)}개 미리보기:", dict(Counter(KIND_KO[v['kind']] for v in plan.values())))
print(CACHE / "reports" / "basic_held_covers.html")
