"""11/13 아침에 볼 결과 페이지 두 개 (읽기만, 돈 안 듦)
  1) 입문 가이드 표지: AI 가 '캐릭터가 크게 나온 쪽'을 고른 97개 (원작 책 표지 그대로 / 포스터 · 캐릭터로 맞바꿈)
  2) 팬 추천 표지: 시안 C 로 만든 188개 전부 (펼치면 나머지 카드)
그림은 서버에 넣을 묶음(.cache/server_bundles_20261113)의 카드 PNG 를 그대로 보여 주고, 글자는 앱처럼 카드 66% 지점부터 얹음
  python -I <이 파일>   → .cache/reports/basic_cover_choice.html · like_L3_covers.html
"""
import html
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
CACHE = pathlib.Path(__file__).resolve().parents[2] / ".cache"
BUNDLES = CACHE / "server_bundles_20261113"
UPLOAD = CACHE / "server_upload_20261113"
REPORTS = CACHE / "reports"
kept = set(json.loads((UPLOAD / "kept.json").read_text(encoding="utf-8")))
held_server = json.loads((UPLOAD / "held_cover_server.json").read_text(encoding="utf-8"))
held_new = json.loads((UPLOAD / "held_cover_new.json").read_text(encoding="utf-8"))

STYLE = """:root{--bg:#f7f7f8;--card:#fff;--text:#1d1d22;--muted:#6b6b76;--line:#e4e4ea;--ok:#0f8a5f;--warn:#b45309}
@media (prefers-color-scheme: dark){:root{--bg:#141418;--card:#1e1e24;--text:#ececf1;--muted:#a0a0ad;--line:#2e2e37;--ok:#3ccf91;--warn:#f0a050}}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}
main{max-width:1300px;margin:0 auto;padding:20px 16px 60px} h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:26px 0 10px}
.muted{color:var(--muted)} .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px}
.frame{position:relative;aspect-ratio:1308/1632;border-radius:9px;overflow:hidden;background:#000}
.frame img{width:100%;height:100%;display:block;object-fit:cover}
.ov{position:absolute;left:6%;right:6%;top:66%;color:#fff;text-shadow:0 1px 4px #000c}
.eb{font-size:10px;opacity:.9} .tt{font-size:15px;font-weight:800;margin:2px 0;line-height:1.3} .bd{font-size:11px;line-height:1.4}
figcaption{margin-top:6px;font-size:12px} .badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;font-weight:700;border:1px solid}
.in{color:var(--ok)} .hold{color:var(--warn)} .alt{display:flex;gap:6px;align-items:center;margin-top:6px}
.alt img{width:56px;border-radius:5px} details summary{cursor:pointer;color:var(--muted);margin-top:4px}
.rest{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-top:6px}"""


def card_html(rid: str, card: dict, page_file: str, lazy: bool = True) -> str:
    title = "<br>".join(html.escape(t) for t in card.get("title_lines") or [])
    body = " ".join(html.escape(s) for s in card.get("body_sentences") or [])
    src = f"../server_bundles_20261113/{rid}/cards/{page_file}"
    return (f'<div class=frame><img {"loading=lazy " if lazy else ""}src="{src}" alt=""><div class=ov>'
            f'<div class=eb>{html.escape(card.get("eyebrow") or "")}</div><div class=tt>{title}</div>'
            f'<div class=bd>{body}</div></div></div>')


def state(rid: str) -> str:
    if rid in kept:
        return '<span class="badge in">넣음</span>'
    if rid in held_server:
        return f'<span class="badge hold">보류 · 서버 글 {html.escape(held_server[rid])} 과 표지 같음</span>'
    if rid in held_new:
        return f'<span class="badge hold">보류 · 새 글 {html.escape(held_new[rid])} 과 표지 같음</span>'
    return '<span class="badge hold">묶음 없음</span>'


def load(rid: str) -> dict | None:
    f = BUNDLES / rid / "bundle.json"
    return json.loads(f.read_text(encoding="utf-8"))["items"][0]["package"] if f.exists() else None


# 1) 입문 가이드 표지
choice = json.loads((HERE / "basic_cover_choice.json").read_text(encoding="utf-8"))
choice.setdefault("dict-guide-basic-w17", {"choice": "swap", "reason": "서원: 딱 봤을 때 '암살교실'이 떠오르는 살생님 그림으로"})
groups = {"manga": [], "swap": []}
for rid, rec in sorted(choice.items(), key=lambda kv: kv[0]):
    package = load(rid)
    if package is None:
        continue
    imgs = {i["id"]: i for i in package["images"]}
    cards = sorted(package["cards"], key=lambda c: c["page"])
    cover = cards[0]

    def src(c: dict) -> str:
        return imgs.get(c["image_id"], {}).get("source_url", "")

    # 다른 후보: 원작 책 표지 그대로면 포스터 · 캐릭터 카드, 맞바꿨으면 원작 책 표지가 간 카드
    if rec["choice"] == "manga":
        other = next((c for c in cards[1:] if src(c) == rec.get("alt")), None)
    else:
        other = next((c for c in cards[1:] if "/manga/" in src(c)), None)
    alt = ""
    if other is not None:
        alt = (f'<div class=alt><img loading=lazy src="../server_bundles_20261113/{rid}/cards/card_{other["page"]:02d}.png" alt="">'
               f'<span class=muted>다른 후보 ({other["page"]}장에 들어감)</span></div>')
    title = package.get("topic") or rid
    groups[rec["choice"]].append(
        f'<figure>{card_html(rid, cover, "card_01.png")}<figcaption><b>{html.escape(title)}</b> {state(rid)}<br>'
        f'<span class=muted>AI: {html.escape(rec.get("reason") or "")}</span>{alt}</figcaption></figure>')
n_manga, n_swap = len(groups["manga"]), len(groups["swap"])
(REPORTS / "basic_cover_choice.html").write_text(f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>입문 가이드 표지 고르기</title><style>{STYLE}</style></head><body><main>
<h1>입문 가이드 표지 — AI 가 '캐릭터가 크게 나온 쪽'을 고른 결과</h1>
<p class=muted>생성기가 원작 만화책 표지를 표지로 고른 글 96개 + 암살교실. 원작 이야기 글과 표지가 같아지는 것은 AI 없이 맞바꿈. 서버에 넣을 묶음 그대로 보여 줌 (글자는 앱처럼 얹은 흉내).</p>
<h2>원작 책 표지 그대로 ({n_manga}개)</h2><div class=grid>{''.join(groups['manga'])}</div>
<h2>포스터 · 캐릭터 그림으로 맞바꿈 ({n_swap}개)</h2><div class=grid>{''.join(groups['swap'])}</div>
</main></body></html>""", encoding="utf-8")
print("입문 가이드 표지:", n_manga, "그대로 ·", n_swap, "맞바꿈")

# 2) 팬 추천 표지 (시안 C)
like = sorted(p.name for p in BUNDLES.iterdir() if "-like-" in p.name)
figs = []
for rid in like:
    package = load(rid)
    cards = sorted(package["cards"], key=lambda c: c["page"])
    rest = "".join(card_html(rid, c, f"card_{c['page']:02d}.png") for c in cards[1:])
    picks = " · ".join(html.escape((c.get("title_lines") or [""])[-1]) for c in cards[1:])
    figs.append(f'<figure>{card_html(rid, cards[0], "card_01.png")}<figcaption>{state(rid)}<br>'
                f'<span class=muted>추천작: {picks}</span><details><summary>나머지 카드 {len(cards) - 1}장</summary>'
                f'<div class=rest>{rest}</div></details></figcaption></figure>')
(REPORTS / "like_L3_covers.html").write_text(f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>팬 추천 표지 모음</title><style>{STYLE}</style></head><body><main>
<h1>팬 추천 — 시안 C 로 만든 {len(figs)}개</h1>
<p class=muted>바탕: 추천작 4편 바둑판(어둡게) · 가운데: 원작 애니 포스터. 디스크가 꽉 차서 508개 중 여기까지 만들어짐. 카드 아래 '나머지 카드'를 누르면 추천작 카드가 보임.</p>
<div class=grid>{''.join(figs)}</div></main></body></html>""", encoding="utf-8")
print("팬 추천 표지:", len(figs), "개")
print(REPORTS / "basic_cover_choice.html")
print(REPORTS / "like_L3_covers.html")
