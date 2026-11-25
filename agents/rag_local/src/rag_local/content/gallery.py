# ruff: noqa: E501
"""생성 결과를 눈으로 보는 HTML 한 장 — 카드 그림 위에 실제 앱처럼 문구를 얹어 보여 준다

  run.py 가 글 하나 끝날 때마다 다시 쓴다. 파일을 더블클릭하면 열린다 (외부 스크립트 없음)
"""

from __future__ import annotations

import html
import pathlib
from collections import Counter

LABEL = {"curation": "큐레이션", "music": "음악", "people": "인물", "character": "캐릭터", "guide": "작품 가이드"}
PASSED = "ready_for_feed"
CSS = """
:root{color-scheme:light dark;--bg:#fafafa;--fg:#1b1b1f;--dim:#6b6b76;--card:#fff;--line:#e4e4ea;--ok:#0a7d4f;--no:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#121216;--fg:#ececf1;--dim:#9c9ca8;--card:#1c1c22;--line:#2e2e37;--ok:#5fd3a2;--no:#ff8a80}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px 80px;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,'Malgun Gothic',sans-serif}
main{max-width:1200px;margin:auto}h1{font-size:24px;margin:0 0 12px}h2{font-size:18px;margin:36px 0 12px;padding-top:12px;border-top:1px solid var(--line)}
table{border-collapse:collapse;margin:8px 0}td,th{padding:4px 12px;border-bottom:1px solid var(--line);text-align:left}
.post{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}
.head{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline}.topic{font-weight:700;font-size:16px}.meta{color:var(--dim);font-size:13px}
.ok{color:var(--ok);font-weight:700}.no{color:var(--no);font-weight:700}
.deck{display:flex;gap:10px;overflow-x:auto;padding:12px 0}
.c{position:relative;flex:0 0 240px;aspect-ratio:327/408;border-radius:10px;overflow:hidden;background:#222}
.c img{position:absolute;inset:0;width:100%;height:100%;display:block}
.t{position:absolute;color:#fff;display:flex;flex-direction:column;justify-content:flex-end;text-shadow:0 1px 4px rgba(0,0,0,.85)}
.c:after{content:'';position:absolute;inset:50% 0 0;background:linear-gradient(transparent,rgba(0,0,0,.72))}.t{z-index:1}
.e{font-size:10px;opacity:.85}.h{font-size:15px;font-weight:800;line-height:1.3}.b{font-size:11px;line-height:1.4;margin-top:3px}
.detail{white-space:pre-wrap;margin:6px 0}.small{font-size:13px;color:var(--dim)}ul{margin:4px 0;padding-left:18px}details{margin-top:6px}summary{cursor:pointer;color:var(--dim);font-size:13px}
"""


def esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""))


def card_html(card: dict, src: str | None) -> str:
    box = (card.get("layout") or {}).get("text_overlay_box") or {"x": 0.06, "y": 0.66, "width": 0.88, "height": 0.28}
    title = "<br>".join(esc(line) for line in card.get("title_lines") or [])
    body = " ".join(esc(line) for line in card.get("body_sentences") or [])
    style = f"left:{box['x'] * 100:.1f}%;top:{box['y'] * 100:.1f}%;width:{box['width'] * 100:.1f}%;height:{box['height'] * 100:.1f}%"
    image = f'<img src="{esc(src)}" alt="" loading="lazy">' if src else ""
    return (f'<div class="c">{image}<div class="t" style="{style}"><div class="e">{esc(card.get("eyebrow"))}</div>'
            f'<div class="h">{title}</div><div class="b">{body}</div></div></div>')


def post_html(rec: dict) -> str:
    package = rec.get("package") or {}
    passed = rec["status"] == PASSED
    mark = '<span class="ok">통과</span>' if passed else f'<span class="no">{esc(rec["status"])}</span>'
    cards = sorted(package.get("cards") or [], key=lambda c: c["page"])
    deck = "".join(card_html(c, rec["cards"].get(c["page"]) or rec["cards"].get(str(c["page"]))) for c in cards)
    body = (package.get("detail_body") or "").strip()
    facts = "".join(f"<li>{esc(f['statement'])}</li>" for f in package.get("facts") or [])
    review = rec.get("review") or {}
    notes = [esc(review.get("summary"))] if review.get("summary") else []
    for c in review.get("cards") or []:
        scores = f"재미 {c.get('fan_engagement')} · 흐름 {c.get('card_flow')} · 자연스러움 {c.get('korean_naturalness')} · 그림 {c.get('image_relevance')}"
        notes.append(f"{c.get('page')}장 ({scores}): {esc(c.get('feedback'))}")
    issues = "".join(f"<li>{esc(i.get('code'))}: {esc(i.get('message'))}</li>" for i in rec.get("issues") or [])
    error = f'<p class="no">{esc(rec["error"])}</p>' if rec.get("error") else ""
    return f"""<article class="post"><div class="head"><span class="topic">{esc(rec['topic'])}</span>{mark}
<span class="meta">점수 {esc(rec.get('score'))} · ${rec['cost']:.4f} · {rec['seconds']:.0f}초 · 고쳐 쓴 횟수 {esc(rec.get('revisions', 0))} · 사실 {rec['n_facts']}개 · 상세 글 {len(body)}자</span></div>
{error}<div class="deck">{deck}</div>
{f'<div class="detail">{esc(body)}</div>' if body else ''}
{f'<div class="small">떨어진 이유</div><ul>{issues}</ul>' if issues else ''}
{f'<details><summary>검수 의견</summary><ul>{"".join(f"<li>{n}</li>" for n in notes)}</ul></details>' if notes else ''}
<details><summary>넘긴 사실 {rec['n_facts']}개</summary><ul>{facts}</ul></details></article>"""


def write_gallery(records: list[dict], path: pathlib.Path) -> None:
    total, passed = Counter(r["content_type"] for r in records), Counter(r["content_type"] for r in records if r["status"] == PASSED)
    rows = "".join(f"<tr><td>{LABEL.get(t, t)}</td><td>{passed[t]} / {n}</td></tr>" for t, n in total.items())
    spent = sum(r["cost"] for r in records)
    groups = []
    for t in total:
        posts = "".join(post_html(r) for r in records if r["content_type"] == t)
        groups.append(f"<h2>{esc(LABEL.get(t, t))}</h2>{posts}")
    path.write_text(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>사전 글 시범</title><style>{CSS}</style></head>
<body><main><h1>사전 글 시범</h1><p class="small">전체 {len(records)}개 · 통과 {sum(passed.values())}개 · 비용 ${spent:.3f}</p><table><tr><th>유형</th><th>통과</th></tr>{rows}</table>{''.join(groups)}</main></body></html>""", encoding="utf-8")
