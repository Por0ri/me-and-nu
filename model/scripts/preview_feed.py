# preview_feed — 팀원 샘플 유저에게 실제 에이전트 글로 피드를 만들어 본다 (EXPERIMENTS 4장 3-2 연결 연습)
#
# 1) me;nu 작품 목록 위에 v0.5 문맥을 만든다 (model/algo/service.py)
# 2) 에이전트 DB(SQLite)의 글을 v1 글 표로 바꾼다: 대표 작품 · 유형 · 노출 기간 (service.exposure_end). DB 는 읽기만 한다
# 3) 팀원을 본뜬 샘플 유저(make_sample_data.py)의 온보딩(좋아하는 작품 · 장르 · 유형)으로 v0.5 작품 추천 10개와
#    v1 피드 순서를 만든다. 반응이 아직 없는 첫 접속 (v1 출발값, EXP-22)
#    → model/data/feed_preview/index.html + img/ (글 표지 썸네일. 커밋 안 함)
#
#   python model/scripts/preview_feed.py "…/Downloads/ani_menu_local.sqlite3"
#   python model/scripts/preview_feed.py "…" --all                  # 검수 대기 · 탈락 글도 넣기 (모델 시험용)
#   python model/scripts/preview_feed.py "…" --today 2026-11-25     # 다른 날 기준 (노출 기간 확인용)

import argparse
import html
import io
import json
import math
import pathlib
import re
import sqlite3
import sys
import time
import webbrowser
from collections import Counter
from datetime import date, datetime

from PIL import Image

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from model.algo import anime_ranker as ar  # noqa: E402
from model.algo import content_v1 as v1  # noqa: E402
from model.algo import service  # noqa: E402
from model.scripts.make_sample_data import ENTITIES, USERS  # noqa: E402

OUT = REPO / "model" / "data" / "feed_preview"
THUMB_WIDTH = 360            # 표지 썸네일 가로 (픽셀)
SHOW = 10                    # 피드에서 펼쳐 보여 줄 글 수 (나머지는 접어 둠)
STATE = {"ready": "피드 대기", "held_for_quality_review": "검수 대기", "rejected": "탈락"}
TYPE_COLOR = {"news": "#2563eb", "event": "#db2777", "guide": "#059669", "people": "#7c3aed", "goods": "#ea580c",
              "behind": "#0891b2", "humor": "#ca8a04", "character": "#e11d48", "curation": "#4f46e5", "music": "#9333ea",
              "review": "#475569"}
PART_NAMES = ("작품 취향", "유형 취향", "새 글")
CSS = """
:root{--bg:#f4f4f6;--card:#fff;--ink:#17171c;--sub:#5f6170;--line:#e6e6ec;--soft:#f0f0f5;--accent:#5b4bdb;
--work:#5b4bdb;--type:#16a34a;--fresh:#f59e0b;--warn:#c2410c;--shadow:0 1px 2px #0000000f,0 4px 16px #0000000a}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1015;--card:#191a22;--ink:#ececf2;
--sub:#a3a5b5;--line:#2a2c38;--soft:#22232d;--accent:#8b7cf6;--work:#8b7cf6;--shadow:none}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 system-ui,-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:20px 16px 48px}
header h1{font-size:22px;margin:0 0 4px}header p{margin:0;color:var(--sub);font-size:13px}
.pills{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 16px}.pill{background:var(--card);border:1px solid var(--line);
border-radius:999px;padding:3px 10px;font-size:12px;color:var(--sub)}.pill b{color:var(--ink)}
.tabs{display:flex;gap:6px;overflow-x:auto;padding:4px 0 12px;position:sticky;top:0;background:var(--bg);z-index:5}
.tab{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:7px 14px;
font:inherit;font-weight:600;cursor:pointer;white-space:nowrap}.tab.on{background:var(--accent);border-color:var(--accent);color:#fff}
.panel{display:none}.panel.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}
.profile{padding:16px 18px;display:grid;gap:10px;margin-bottom:18px}.who{display:flex;align-items:center;gap:12px}
.avatar{width:44px;height:44px;border-radius:50%;background:var(--accent);color:#fff;display:grid;place-items:center;
font-weight:700;font-size:18px}.who h2{margin:0;font-size:18px}.sub{color:var(--sub);font-size:12.5px;margin:0}
.row{display:flex;flex-wrap:wrap;align-items:center;gap:6px}.lab{width:64px;color:var(--sub);font-size:12px;flex:none}
.chip{background:var(--soft);border-radius:8px;padding:2px 8px;font-size:12.5px}.chip small{color:var(--warn);margin-left:4px}
.type{color:#fff;border-radius:6px;padding:1px 7px;font-size:11.5px;font-weight:600}
h3{font-size:15px;margin:22px 0 10px;display:flex;align-items:baseline;gap:8px}h3 span{font-weight:400;color:var(--sub);font-size:12px}
.recs{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
.rec{padding:10px 12px;display:flex;gap:10px;align-items:flex-start}.rec .no{color:var(--accent);font-weight:700;min-width:18px}
.rec b{display:block;font-size:13.5px}.rec small{color:var(--sub);font-size:11.5px;display:block}
.legend{display:flex;gap:12px;font-size:12px;color:var(--sub);margin:-4px 0 10px}.legend i{display:inline-block;width:10px;
height:10px;border-radius:3px;margin-right:4px;vertical-align:-1px}
.feed{display:grid;grid-template-columns:1fr;gap:10px}@media (min-width:760px){.feed{grid-template-columns:1fr 1fr}}
.post{display:flex;gap:12px;padding:10px;align-items:stretch}.thumb{position:relative;flex:none;width:96px}
.thumb img{width:96px;height:120px;object-fit:cover;border-radius:10px;display:block;background:var(--soft)}
.rank{position:absolute;left:6px;top:6px;background:#000000b3;color:#fff;border-radius:7px;font-size:12px;
font-weight:700;padding:0 6px}.body{min-width:0;flex:1;display:flex;flex-direction:column;gap:3px}
.tags{display:flex;gap:5px;flex-wrap:wrap;align-items:center}.state{font-size:11px;color:var(--warn);
border:1px solid var(--warn);border-radius:6px;padding:0 5px}.eyebrow{color:var(--sub);font-size:11.5px}
.post h4{margin:0;font-size:14px;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.work{font-size:12px;color:var(--sub)}.work b{color:var(--ink);font-weight:600}
.score{display:flex;align-items:center;gap:8px;margin-top:auto}.bar{flex:1;height:8px;border-radius:99px;background:var(--soft);
display:flex;overflow:hidden}.bar i{display:block;height:100%}.score b{font-size:12.5px;min-width:32px;text-align:right}
.dates{font-size:11px;color:var(--sub)}
details{margin-top:10px}summary{cursor:pointer;color:var(--accent);font-weight:600;padding:6px 0}
.notes{display:flex;flex-wrap:wrap;gap:4px;margin-top:2px}.note{font-size:11px;background:#fff1e6;color:#9a3412;border-radius:6px;padding:0 6px}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .note{background:#3b2414;color:#fdba74}}
"""
JS = """
document.querySelectorAll('.tab').forEach(function(b){b.addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(x){x.classList.toggle('on',x===b)});
document.querySelectorAll('.panel').forEach(function(p){p.classList.toggle('on',p.id===b.dataset.p)});window.scrollTo(0,0)})});
"""


def esc(x):
    return html.escape(str(x))


def day_of(s):
    return date.fromisoformat(s[:10]) if s else None


def thumb(db, key):
    # 표지 카드 이미지 → 작은 JPEG (img/키.jpg). 이미 만들었으면 그대로
    if not key:
        return None
    path = OUT / "img" / f"{key}.jpg"
    if not path.exists():
        blob = db.execute("select binary_data from image_blobs where sha256=?", (key,)).fetchone()
        if not blob:
            return None
        im = Image.open(io.BytesIO(blob[0]))
        if im.mode != "RGB":
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").getchannel("A"))
            im = bg
        if im.width > THUMB_WIDTH:
            im = im.resize((THUMB_WIDTH, round(im.height * THUMB_WIDTH / im.width)), Image.LANCZOS)
        path.parent.mkdir(parents=True, exist_ok=True)
        im.save(path, "JPEG", quality=80)
    return f"img/{key}.jpg"


def load_posts(db, include_all):
    """에이전트 글 → service.make_feed 에 넣을 dict 목록. 발표일 = 원문 발표일 중 가장 늦은 날 (없으면 만든 날)
    include_all: 피드 대기(ready)뿐 아니라 검수 대기 · 탈락 글까지 (모델 시험용. 탈락은 대부분 글 표현 문제라 작품 · 유형 · 날짜는 쓸 수 있음).
    탈락했다가 다시 만들어져 피드 대기열에 있는 글은 피드 대기열 쪽만 씀. 표지 카드(1장)의 문구 · 이미지도 같이"""
    primary = {}
    for rid, wid in db.execute("select request_id, menu_work_id from content_work_links order by is_primary desc, sort_order"):
        primary.setdefault(rid, wid)
    cover = {rid: (eyebrow, " ".join(json.loads(lines or "[]")), key) for rid, eyebrow, lines, key in db.execute(
        "select request_id, eyebrow, title_lines_json, rendered_image_key from content_cards where page = 1")}
    rows = db.execute("select request_id, topic, content_type, status, queued_at, valid_until, purge_at, event_start, event_end, "
                      "package_json from feed_queue order by queued_at").fetchall()
    if include_all:
        have = {r[0] for r in rows}
        made = dict(db.execute("select request_id, created_at from content_drafts"))
        for rid, topic, ctype, archived, pj in db.execute(
                "select request_id, topic, content_type, archived_at, package_json from rejected_archive order by archived_at"):
            if rid not in have:
                pk = json.loads(pj)
                rows.append((rid, topic, ctype, "rejected", made.get(rid) or archived, pk.get("feed_valid_until"),
                             pk.get("purge_at"), pk.get("event_start"), pk.get("event_end"), pj))
    posts = []
    for rid, topic, ctype, status, queued, until, purge, ev0, ev1, pj in rows:
        if status != "ready" and not include_all:
            continue
        pubs = [s["published_at"] for s in json.loads(pj).get("sources") or [] if s.get("published_at")]
        published = max(day_of(x) for x in pubs) if pubs else day_of(queued)
        notes = [] if pubs else ["원문 발표일 없음 → 만든 날 기준"]
        end, note = service.exposure_end(ctype, published, day_of(ev0), day_of(ev1))
        notes += [note] if note else []
        if purge and day_of(purge) < end:
            end = day_of(purge)
            notes.append("에이전트가 글을 지우는 날까지만")
        if until and abs((day_of(until) - end).days) > 1:
            notes.append(f"에이전트 노출 끝 {day_of(until)}과 다름")
        if primary.get(rid) is None:
            notes.append("작품 연결 없음 → 작품 취향 0")
        eyebrow, headline, key = cover.get(rid, ("", "", None))
        posts.append({"id": rid, "topic": topic, "type": ctype, "status": status, "menu_work_id": primary.get(rid),
                      "published": published, "until": end, "event": (day_of(ev0), day_of(ev1)), "notes": notes,
                      "eyebrow": eyebrow or "", "headline": headline or topic, "thumb": thumb(db, key)})
    return posts


def norm(s):
    return re.sub(r"[\s!?:·×\-~.,'\"]", "", (s or "").lower())


def find_work(ctx, name):
    # 한국어 제목으로 me;nu 작품 번호 찾기 (같은 이름이 여럿이면 번호가 작은 = 더 인기 있는 시리즈)
    hits = [w for w, v in ctx["works"].items() if norm(v["title_ko"]) == norm(name)]
    return min(hits) if hits else None


def work_name(ctx, wid):
    w = ctx["works"].get(wid)
    return (w["title_ko"] or w["title"]) if w else "-"


def entry_name(ctx, a):
    # 작품(시즌 · 극장판) 이름: 시리즈 한국어 제목 (시즌 원제, 시작 연도)
    return f"{work_name(ctx, ctx['work_of'][a])} ({ctx['anime'][a]['title']}, {(ctx['anime'][a]['start_date'] or '')[:4]})"


# ## 화면
def type_badge(code, labels):
    return f'<span class=type style="background:{TYPE_COLOR.get(code, "#64748b")}">{esc(labels.get(code, code))}</span>'


def post_card(rank, post, parts, reason, labels, ctx):
    scale = 100 / 2.2                                             # 합계 2.2 = 막대 끝 (작품 1 + 유형 0.5 + 새 글 0.3 + 여유)
    bar = "".join(f'<i style="width:{max(x, 0) * scale:.1f}%;background:var(--{c})" title="{n} {x:.2f}"></i>'
                  for x, c, n in zip(parts or (), ("work", "type", "fresh"), PART_NAMES))
    state = f'<span class=state>{STATE[post["status"]]}</span>' if post["status"] != "ready" else ""
    img = f'<img src="{esc(post["thumb"])}" alt="" loading=lazy>' if post["thumb"] else "<img alt=''>"
    rank_html = f'<span class=rank>{rank}</span>' if rank else ""
    notes = "".join(f"<span class=note>{esc(n)}</span>" for n in post["notes"]) if rank is None else ""
    score = (f'<div class=score><div class=bar>{bar}</div><b>{sum(parts):.2f}</b></div>' if parts else "")
    return (f'<article class="card post"><div class=thumb>{img}{rank_html}</div><div class=body>'
            f'<div class=tags>{type_badge(post["type"], labels)}{state}</div>'
            + (f'<div class=eyebrow>{esc(post["eyebrow"])}</div>' if post["eyebrow"] else "")
            + f'<h4>{esc(post["headline"])}</h4>'
            f'<div class=work><b>{esc(work_name(ctx, post["menu_work_id"]))}</b>{" · " + esc(reason) if reason else ""}</div>'
            f'{score}<div class=dates>발표 {post["published"]:%m-%d} · 노출 끝 {post["until"]:%m-%d}</div>'
            + (f'<div class=notes>{notes}</div>' if notes else "") + "</div></article>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--all", action="store_true", help="검수 대기 · 탈락 글도 넣기 (모델 시험용)")
    ap.add_argument("--today", default=date.today().isoformat())
    args = ap.parse_args()
    today = date.fromisoformat(args.today)
    now = ar.to_seconds(datetime(today.year, today.month, today.day))
    today_day = now // ar.DAY

    t0 = time.time()
    ctx = service.build()
    recent = ar.recent_counts({"t": now}, ctx)
    print(f"문맥 준비 {time.time() - t0:.0f}초 · 작품 {len(ctx['ids']):,} · 기록 지운 작품 {len(ctx['no_record']):,} · "
          f"최근 30일 기록 {int(recent.sum()):,}건")

    db = sqlite3.connect(f"file:{pathlib.Path(args.db).as_posix()}?mode=ro&immutable=1", uri=True)
    labels = dict(db.execute("select type_code, label_ko from content_types"))
    posts = load_posts(db, args.all)
    feed = service.make_feed(posts, ctx)
    live = int(((feed["day"] <= today_day) & (feed["until"] >= today_day)).sum())
    cfg = v1.Config(w_genre=service.W_GENRE)
    names = {key: name for key, name, *_ in ENTITIES}

    tabs, panels = [], []
    for u in USERS:
        works = [w for w in (find_work(ctx, names[k]) for k in u["favorite_works"]) if w]
        missing = [names[k] for k in u["favorite_works"] if not find_work(ctx, names[k])]
        user = service.onboarding_user(ctx, works, u["genres"], [], now)
        ranked = ar.next_auto(user, ctx)
        top = ar.pick_top(ranked, user, ctx)
        srank = {}                                                   # 시리즈 순위 (v0.5 순위에서 그 시리즈가 처음 나온 자리)
        for a in ranked:
            srank.setdefault(ctx["work_of"][a], len(srank) + 1)
        rank_of = lambda w: "고른 작품" if w in works else (f"작품 순위 {srank[w]}위" if w in srank else "작품 없음")
        state = v1.UserState(user["history"], list(u["genres"]), [], list(u["content_types"]), now,
                             len(ctx["cw_rows"]), len(posts))
        service.work_affinity(state, ctx, cfg)
        service.genre_affinity(state, ctx)
        order = service.arrange(v1.rank_feed(state, feed, today_day, cfg, k=len(posts)).tolist(), feed, works, ctx)
        tp = v1.type_pref(state, cfg)
        rows = []
        for rank, i in enumerate(order, 1):
            p, ty = int(feed["pos"][i]), int(feed["type"][i])
            parts = (cfg.w_work * (float(state.work_aff[p]) if p >= 0 else 0.0), cfg.w_type * tp[ty],
                     cfg.w_fresh * math.exp(-(today_day - feed["day"][i]) / v1.TAU[ty]))
            rows.append((rank, posts[i], parts))
        mine = {ctx["series"][a] for a in user["history"].anime.tolist()}
        print(f"[{u['name']}] 피드 1~3위: " + " / ".join(f"{p['headline'][:24]}" for _, p, _ in rows[:3]))

        pid = f"p-{u['user_id']}"
        tabs.append(f'<button class=tab data-p="{pid}">{esc(u["name"])}</button>')
        picks = "".join(f'<span class=chip>{esc(work_name(ctx, ctx["work_of"][a]))}'
                        f'{"<small>신작 · 같이 본 기록 없음</small>" if a in ctx["no_record"] else ""}</span>'
                        for a in user["history"].anime.tolist())
        if missing:
            picks += f'<span class=chip><small>목록에 없음: {esc(", ".join(missing))}</small></span>'
        recs = "".join(
            f'<div class="card rec"><span class=no>{n}</span><div><b>{esc(work_name(ctx, ctx["work_of"][a]))}</b>'
            f'<small>{esc(ctx["anime"][a]["title"])} · {(ctx["anime"][a]["start_date"] or "")[:4]}'
            f'{" · 고른 작품의 다른 시즌" if ctx["series"][a] in mine else ""}</small></div></div>'
            for n, a in enumerate(top, 1))
        cards = [post_card(rank, post, parts, rank_of(post["menu_work_id"]), labels, ctx) for rank, post, parts in rows]
        more = (f'<details><summary>나머지 {len(cards) - SHOW}개 더 보기</summary><div class=feed>{"".join(cards[SHOW:])}</div></details>'
                if len(cards) > SHOW else "")
        panels.append(
            f'<section class=panel id="{pid}"><div class="card profile"><div class=who><div class=avatar>{esc(u["name"][0])}</div>'
            f'<div><h2>{esc(u["name"])}</h2><p class=sub>{"신규 유저 설정" if len(works) <= ar.COLD_MAX_WATCHED else "기존 유저 설정"}'
            f' · 반응 없는 첫 접속</p></div></div>'
            f'<div class=row><span class=lab>고른 작품</span>{picks}</div>'
            f'<div class=row><span class=lab>장르</span>{"".join(f"<span class=chip>{esc(g)}</span>" for g in u["genres"])}</div>'
            f'<div class=row><span class=lab>유형</span>{"".join(type_badge(t, labels) for t in u["content_types"])}</div></div>'
            f'<h3>v0.5 추천 작품 <span>이 유저가 좋아할 작품 10개</span></h3><div class=recs>{recs}</div>'
            f'<h3>v1 피드 <span>글 {len(cards)}개 중 위 {min(SHOW, len(cards))}개</span></h3>'
            f'<div class=legend><span><i style="background:var(--work)"></i>작품 취향</span>'
            f'<span><i style="background:var(--type)"></i>유형 취향</span><span><i style="background:var(--fresh)"></i>새 글</span></div>'
            f'<div class=feed>{"".join(cards[:SHOW])}</div>{more}</section>')

    all_cards = "".join(post_card(None, p, None, "", labels, ctx) for p in posts)
    tabs.append(f'<button class=tab data-p="p-all">글 전체 {len(posts)}</button>')
    panels.append(f'<section class=panel id=p-all><h3>글 → 피드 <span>노출 기간은 모델 규칙. 주황 메모 = 확인할 점</span></h3>'
                  f'<div class=feed>{all_cards}</div></section>')
    users_n = len({line.split(",", 1)[0] for line in open(service.ANILIST_USERS, encoding="utf-8")}) - 1 \
        if service.ANILIST_USERS.exists() else 0
    status = " · ".join(f"{STATE.get(k, k)} {v}" for k, v in Counter(p["status"] for p in posts).items())
    pills = [f"기준일 <b>{today}</b>", f"작품 <b>{len(ctx['ids']):,}</b>편 (시리즈 {len(ctx['works']):,})",
             f"글 <b>{len(posts)}</b>개 ({status})", f"오늘 노출 중 <b>{live}</b>개",
             f"같이 본 기록 MAL {ctx['cw_users']:,}명", f"요즘 인기 AniList {users_n:,}명",
             f"v1 출발값 · 작품 {cfg.w_work} · 유형 {cfg.w_type} · 새 글 {cfg.w_fresh}"]
    tabs[0] = tabs[0].replace("class=tab", "class='tab on'", 1)
    panels[0] = panels[0].replace("<section class=panel", "<section class='panel on'", 1)
    page = (f"<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>피드 미리 보기</title><style>{CSS}</style></head><body><div class=wrap>"
            f"<header><h1>피드 미리 보기</h1><p>팀원 취향을 본뜬 샘플 유저에게 실제 에이전트 글로 만든 첫 화면 피드</p></header>"
            f"<div class=pills>{''.join(f'<span class=pill>{x}</span>' for x in pills)}</div>"
            f"<nav class=tabs>{''.join(tabs)}</nav>{''.join(panels)}</div><script>{JS}</script></body></html>")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(page, encoding="utf-8")
    print(f"→ {OUT / 'index.html'}")
    webbrowser.open((OUT / "index.html").as_uri())


if __name__ == "__main__":
    main()
