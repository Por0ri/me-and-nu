"""주연 소개(시안 C) 글 전체 보기 (읽기만): 카드 전부를 앱처럼 가로로 + 상세 글 본문 + 출처
  python -I <이 파일> [글 번호 ...]   → .cache/reports/intro_content_example.html (번호를 안 주면 인기작 몇 개)
"""
import html
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
CACHE = pathlib.Path(__file__).resolve().parents[2] / ".cache"
RUNS = CACHE / "content" / "runs"
OUT = CACHE / "reports" / "intro_content_example.html"
WANT = sys.argv[1:] or ["dict-character-intro-w1", "dict-character-intro-w4", "dict-character-intro-w6",
                        "dict-character-intro-w19", "dict-character-intro-w23"]

found = {}
for f in sorted(RUNS.glob("mass-1113i-p*/results.json")):
    try:
        recs = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        continue
    for r in recs:
        if r.get("status") == "ready_for_feed" and r["request_id"] in WANT:
            found[r["request_id"]] = (f.parent.name, r)


def card_html(folder: str, rid: str, card: dict) -> str:
    title = "<br>".join(html.escape(t) for t in card.get("title_lines") or [])
    body = " ".join(html.escape(s) for s in card.get("body_sentences") or [])
    src = f"../content/runs/{folder}/{rid}/card_{card['page']:02d}.png"
    return (f'<div class=frame><img src="{src}" alt=""><div class=ov>'
            f'<div class=eb>{html.escape(card.get("eyebrow") or "")}</div><div class=tt>{title}</div>'
            f'<div class=bd>{body}</div></div><span class=pg>{card["page"]}</span></div>')


posts = []
for rid in WANT:
    if rid not in found:
        continue
    folder, r = found[rid]
    p = r["package"]
    cards = "".join(card_html(folder, rid, c) for c in sorted(p["cards"], key=lambda c: c["page"]))
    detail = p.get("detail_body") or ""
    paras = "".join(f"<p>{html.escape(x)}</p>" for x in str(detail).split("\n") if x.strip())
    used = set(p.get("detail_source_ids") or []) | {s for f in p.get("facts") or [] for s in f.get("source_ids") or []}
    srcs = "".join(f'<li>{html.escape(s.get("title") or "")} · {html.escape(s.get("publisher") or "")}</li>'
                   for s in p.get("sources") or [] if not used or s.get("id") in used)
    posts.append(f"""<section><h2>{html.escape(p.get('topic') or rid)} <span class=muted>· 점수 {r.get('score')} · 카드 {len(p['cards'])}장</span></h2>
<div class=strip>{cards}</div>
<div class=detail><h3>상세 글 (카드 넘긴 뒤 보이는 본문 · {len(str(detail).replace(' ', '').replace(chr(10), ''))}자)</h3>{paras}
<h3>출처</h3><ul>{srcs}</ul></div></section>""")

OUT.write_text(f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>주연 소개 글 예시</title><style>
:root{{--bg:#f7f7f8;--card:#fff;--text:#1d1d22;--muted:#6b6b76;--line:#e4e4ea}}
@media (prefers-color-scheme: dark){{:root{{--bg:#141418;--card:#1e1e24;--text:#ececf1;--muted:#a0a0ad;--line:#2e2e37}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:20px 16px 60px}} h1{{font-size:22px;margin:0 0 4px}} .muted{{color:var(--muted);font-weight:400}}
section{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:18px 0}}
h2{{font-size:18px;margin:0 0 10px}} h3{{font-size:14px;margin:14px 0 6px;color:var(--muted)}}
.strip{{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px;scroll-snap-type:x mandatory}}
.frame{{flex:0 0 220px;position:relative;aspect-ratio:1308/1632;border-radius:10px;overflow:hidden;background:#000;scroll-snap-align:start}}
.frame img{{width:100%;height:100%;display:block;object-fit:cover}}
.ov{{position:absolute;left:6%;right:6%;top:66%;color:#fff;text-shadow:0 1px 4px #000c}}
.eb{{font-size:10px;opacity:.9}} .tt{{font-size:15px;font-weight:800;margin:2px 0;line-height:1.3}} .bd{{font-size:11px;line-height:1.4}}
.pg{{position:absolute;top:6px;right:8px;font-size:11px;color:#fff;background:#0008;border-radius:8px;padding:0 6px}}
.detail{{max-width:720px}} .detail p{{margin:0 0 10px}} ul{{margin:4px 0;padding-left:20px;font-size:13px}}</style></head><body><main>
<h1>주연 소개 — 글 예시 (시안 C)</h1>
<p class=muted>실제로 만든 글 그대로: 카드는 옆으로 넘겨 보고(글자는 앱처럼 얹은 흉내), 아래는 카드를 다 넘긴 뒤 나오는 상세 글과 출처.</p>
{''.join(posts)}</main></body></html>""", encoding="utf-8")
print(OUT, len(posts), "개")
