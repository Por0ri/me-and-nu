"""주연 소개(시안 C, 흰 테두리 6px) 지금까지 나온 글 보기 (읽기만): 표지 + 나머지 카드, 글자는 앱처럼 66% 지점부터 얹음
  python -I <이 파일>   → .cache/reports/intro_C_preview.html
"""
import html
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
CACHE = pathlib.Path(__file__).resolve().parents[2] / ".cache"
RUNS = CACHE / "content" / "runs"
OUT = CACHE / "reports" / "intro_C_preview.html"

posts = []
for f in sorted(RUNS.glob("mass-1113i-p*/results.json")):
    try:
        recs = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        continue  # 일꾼이 쓰는 중
    for r in recs:
        if r.get("status") == "ready_for_feed" and r.get("package"):
            posts.append((f.parent.name, r))
posts.sort(key=lambda x: int(x[1]["request_id"].rsplit("-w", 1)[1]))


def card_html(folder: str, rid: str, card: dict) -> str:
    title = "<br>".join(html.escape(t) for t in card.get("title_lines") or [])
    body = " ".join(html.escape(s) for s in card.get("body_sentences") or [])
    src = f"../content/runs/{folder}/{rid}/card_{card['page']:02d}.png"
    return (f'<div class=frame><img loading=lazy src="{src}" alt=""><div class=ov>'
            f'<div class=eb>{html.escape(card.get("eyebrow") or "")}</div><div class=tt>{title}</div>'
            f'<div class=bd>{body}</div></div></div>')


figs = []
for folder, r in posts:
    cards = sorted(r["package"]["cards"], key=lambda c: c["page"])
    rest = "".join(card_html(folder, r["request_id"], c) for c in cards[1:])
    figs.append(f'<figure>{card_html(folder, r["request_id"], cards[0])}<figcaption><b>{html.escape(r.get("topic") or "")}</b>'
                f' <span class=muted>· 점수 {r.get("score")}</span><details><summary>나머지 카드 {len(cards) - 1}장</summary>'
                f'<div class=rest>{rest}</div></details></figcaption></figure>')
OUT.write_text(f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>주연 소개 시안 C</title><style>
:root{{--bg:#f7f7f8;--card:#fff;--text:#1d1d22;--muted:#6b6b76;--line:#e4e4ea}}
@media (prefers-color-scheme: dark){{:root{{--bg:#141418;--card:#1e1e24;--text:#ececf1;--muted:#a0a0ad;--line:#2e2e37}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}}
main{{max-width:1300px;margin:0 auto;padding:20px 16px 60px}} h1{{font-size:22px;margin:0 0 4px}} .muted{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}}
figure{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px}}
.frame{{position:relative;aspect-ratio:1308/1632;border-radius:9px;overflow:hidden;background:#000}}
.frame img{{width:100%;height:100%;display:block;object-fit:cover}}
.ov{{position:absolute;left:6%;right:6%;top:66%;color:#fff;text-shadow:0 1px 4px #000c}}
.eb{{font-size:10px;opacity:.9}} .tt{{font-size:15px;font-weight:800;margin:2px 0;line-height:1.3}} .bd{{font-size:11px;line-height:1.4}}
figcaption{{margin-top:6px;font-size:12px}} details summary{{cursor:pointer;color:var(--muted);margin-top:4px}}
.rest{{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-top:6px}}</style></head><body><main>
<h1>주연 소개 — 시안 C (흰 테두리 6px) · 지금까지 {len(figs)}개</h1>
<p class=muted>주연 얼굴 바둑판(어둡게) 가운데 원작 애니 포스터. 실제로 만든 글 그대로 (글자는 앱처럼 얹은 흉내). 카드 아래 '나머지 카드'를 누르면 주연 카드가 보임. 다시 열면 늘어난 만큼 보임.</p>
<div class=grid>{''.join(figs)}</div></main></body></html>""", encoding="utf-8")
print(OUT, len(figs), "개")
