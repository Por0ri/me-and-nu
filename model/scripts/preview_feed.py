# preview_feed — 팀원 샘플 유저에게 실제 에이전트 글로 피드를 만들어 본다 (EXPERIMENTS 4장 3-2 연결 연습)
#
# 1) me;nu 작품 목록 위에 v0.5 문맥을 만든다 (model/algo/service.py)
# 2) 에이전트 DB(SQLite)의 글을 v1 글 표로 바꾼다: 대표 작품 · 유형 · 노출 기간 (service.exposure_end). DB 는 읽기만 한다
# 3) 팀원을 본뜬 샘플 유저(make_sample_data.py)의 온보딩(좋아하는 작품 · 장르 · 유형)으로 v0.5 작품 추천 10개와
#    v1 피드 순서를 만든다. 반응이 아직 없는 첫 접속 (v1 출발값, EXP-22)
#    → model/data/feed_preview/index.html (커밋 안 함)
#
#   python model/scripts/preview_feed.py "…/Downloads/ani_menu_local.sqlite3"
#   python model/scripts/preview_feed.py "…" --all                  # 검수 대기 · 탈락 글도 넣기 (모델 시험용)
#   python model/scripts/preview_feed.py "…" --today 2026-11-25     # 다른 날 기준 (노출 기간 확인용)

import argparse
import html
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

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from model.algo import anime_ranker as ar  # noqa: E402
from model.algo import content_v1 as v1  # noqa: E402
from model.algo import service  # noqa: E402
from model.scripts.make_sample_data import ENTITIES, USERS  # noqa: E402

OUT = REPO / "model" / "data" / "feed_preview"
CSS = """
body{margin:0;padding:16px;font-family:system-ui,'Malgun Gothic',sans-serif;background:#f4f4f5;color:#18181b}
h1{font-size:20px} h2{font-size:17px;margin:0 0 8px} h3{font-size:14px;margin:14px 0 6px}
.box{background:#fff;border-radius:12px;padding:16px;margin:0 0 20px;box-shadow:0 1px 3px #0002;overflow-x:auto}
.meta{font-size:13px;color:#52525b;line-height:1.7} .chip{display:inline-block;background:#eef2ff;color:#3730a3;
border-radius:6px;padding:0 6px;margin:0 4px 2px 0} .warn{color:#b91c1c}
table{border-collapse:collapse;font-size:13px;width:100%} th,td{border-bottom:1px solid #e4e4e7;padding:5px 6px;
text-align:left;vertical-align:top} th{background:#fafafa;white-space:nowrap} td.n{text-align:right;white-space:nowrap}
ol{margin:0;padding-left:22px;font-size:13px;line-height:1.7}
"""


STATE = {"ready": "피드 대기", "held_for_quality_review": "검수 대기", "rejected": "탈락"}


def esc(x):
    return html.escape(str(x))


def day_of(s):
    return date.fromisoformat(s[:10]) if s else None


def load_posts(db, include_all):
    """에이전트 글 → service.make_feed 에 넣을 dict 목록. 발표일 = 원문 발표일 중 가장 늦은 날 (없으면 만든 날)
    include_all: 피드 대기(ready)뿐 아니라 검수 대기 · 탈락 글까지 (모델 시험용. 탈락은 대부분 글 표현 문제라 작품 · 유형 · 날짜는 쓸 수 있음).
    탈락했다가 다시 만들어져 피드 대기열에 있는 글은 피드 대기열 쪽만 씀"""
    primary = {}
    for rid, wid in db.execute("select request_id, menu_work_id from content_work_links order by is_primary desc, sort_order"):
        primary.setdefault(rid, wid)
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
        posts.append({"id": rid, "topic": topic, "type": ctype, "status": status, "menu_work_id": primary.get(rid),
                      "published": published, "until": end, "event": (day_of(ev0), day_of(ev1)), "notes": notes})
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
    cfg = v1.Config()
    names = {key: name for key, name, *_ in ENTITIES}

    sections = []
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
        order = v1.rank_feed(state, feed, today_day, cfg, k=len(posts))
        tp = v1.type_pref(state, cfg)
        rows = []
        for rank, i in enumerate(order.tolist(), 1):
            p, ty = int(feed["pos"][i]), int(feed["type"][i])
            parts = (cfg.w_work * (float(state.work_aff[p]) if p >= 0 else 0.0), cfg.w_type * tp[ty],
                     cfg.w_fresh * math.exp(-(today_day - feed["day"][i]) / v1.TAU[ty]))
            rows.append((rank, posts[i], parts))
        mine = {ctx["series"][a] for a in user["history"].anime.tolist()}
        rec_name = lambda a: entry_name(ctx, a) + (" · 고른 작품의 다른 시즌" if ctx["series"][a] in mine else "")
        print(f"\n[{u['name']}] 작품 {', '.join(work_name(ctx, w) for w in works)} · 유형 {', '.join(u['content_types'])}")
        print("  추천 작품:", " / ".join(rec_name(a) for a in top[:5]))
        for rank, post, parts in rows[:5]:
            print(f"  {rank}. [{labels.get(post['type'], post['type'])}] {post['topic'][:30]} · "
                  f"{work_name(ctx, post['menu_work_id'])} · 합계 {sum(parts):.2f}")

        picks = "".join(f"<span class=chip>{esc(entry_name(ctx, a))}"
                        f"{' · 같이 본 기록 없음 (2022년 이후)' if a in ctx['no_record'] else ''}</span>"
                        for a in user["history"].anime.tolist())
        table = "".join(
            f"<tr><td class=n>{rank}</td><td>{esc(post['topic'])}"
            + (f" <span class=meta>({STATE.get(post['status'], post['status'])})</span>" if post["status"] != "ready" else "")
            + f"</td><td>{esc(labels.get(post['type'], post['type']))}</td>"
            f"<td>{esc(work_name(ctx, post['menu_work_id']))}<br><span class=meta>{rank_of(post['menu_work_id'])}</span></td>"
            + "".join(f"<td class=n>{x:.2f}</td>" for x in parts) + f"<td class=n><b>{sum(parts):.2f}</b></td></tr>"
            for rank, post, parts in rows)
        sections.append(
            f"<div class=box><h2>{esc(u['name'])}</h2><div class=meta>고른 작품 (대표 시즌): {picks}"
            + (f" <span class=warn>목록에서 못 찾음: {esc(', '.join(missing))}</span>" if missing else "")
            + f"<br>장르: {esc(', '.join(u['genres']))} · 유형: "
            f"{esc(', '.join(labels.get(t, t) for t in u['content_types']))} · v0.5 설정: "
            f"{'신규 유저' if len(works) <= ar.COLD_MAX_WATCHED else '기존 유저'}</div>"
            f"<h3>v0.5 작품 추천 10개</h3><ol>{''.join(f'<li>{esc(rec_name(a))}</li>' for a in top)}</ol>"
            f"<h3>v1 피드 순서 (반응 없는 첫 접속)</h3><table><tr><th>순위</th><th>글</th><th>유형</th><th>작품</th>"
            f"<th>작품 취향</th><th>유형 취향</th><th>새 글</th><th>합계</th></tr>{table}</table></div>")

    post_rows = "".join(
        f"<tr><td>{esc(p['topic'])}</td><td>{STATE.get(p['status'], p['status'])}</td><td>{esc(labels.get(p['type'], p['type']))}</td><td>{esc(work_name(ctx, p['menu_work_id']))}</td>"
        f"<td>{p['published']}</td><td>{p['event'][0] or '-'} ~ {p['event'][1] or '-'}</td><td>{p['until']}</td>"
        f"<td class=warn>{esc(' · '.join(p['notes']))}</td></tr>" for p in posts)
    users_n = len({line.split(",", 1)[0] for line in open(service.ANILIST_USERS, encoding="utf-8")}) - 1 \
        if service.ANILIST_USERS.exists() else 0
    page = (f"<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>피드 미리 보기</title><style>{CSS}</style></head><body><h1>피드 미리 보기 · 기준일 {today}</h1>"
            f"<div class=box><div class=meta>작품 {len(ctx['ids']):,}편 (시리즈 {len(ctx['works']):,}개) · "
            f"같이 본 기록: MAL {ctx['cw_users']:,}명 · 2022년 이후 작품 {len(ctx['no_record']):,}편은 비슷한 작품에서 빌려 옴 · "
            f"최근 인기: AniList 유저 표본 {users_n:,}명의 최근 30일 기록 {int(recent.sum()):,}건<br>"
            f"글 {len(posts)}개 (" + " · ".join(f"{STATE.get(k, k)} {v}" for k, v in Counter(p["status"] for p in posts).items())
            + f") · 오늘 노출 중 {live}개 · "
            f"v1 설정: 출발값 (작품 취향 {cfg.w_work} · 유형 취향 {cfg.w_type} · 새 글 {cfg.w_fresh})</div>"
            f"<h3>글 → 피드 (노출 기간 = 모델 규칙)</h3><table><tr><th>글</th><th>상태</th><th>유형</th><th>작품</th><th>발표일</th>"
            f"<th>행사 · 발매</th><th>노출 끝</th><th>메모</th></tr>{post_rows}</table></div>"
            f"{''.join(sections)}</body></html>")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(page, encoding="utf-8")
    print(f"\n→ {OUT / 'index.html'}")
    webbrowser.open((OUT / "index.html").as_uri())


if __name__ == "__main__":
    main()
