# view_agent_content — 에이전트가 만든 글을 눈으로 보는 페이지를 만든다
#
# 에이전트 DB(SQLite)에서 피드에 올라갈 글(feed_queue)과 완성된 카드 이미지를 꺼내 HTML 한 장으로 만든다.
# 글마다 유형 · 연결된 작품 · 날짜 · 품질 점수와, 모델 입장에서 확인할 점(발표일 없음 · 대표 작품 없음 · 같은 원문)을 같이 보여준다.
# DB 는 읽기만 한다 (임시 파일도 안 만듦). 결과는 model/data/agent_view/ 에 저장 (커밋 안 함)
# --share 경로 를 주면 이미지를 줄여서 HTML 안에 넣은 파일 하나를 만든다 (팀원에게 보내기용)
# --new 예전DB 를 주면 예전 DB 에 없던 글만 보여준다 (새로 받은 글만 확인할 때)
#
#   python model/scripts/view_agent_content.py "C:/Users/06swl/Downloads/ani_menu_local.sqlite3"
#   python model/scripts/view_agent_content.py "…/ani_menu_local.sqlite3" --share "C:/Users/06swl/Downloads/에이전트_글_보기.html"
#   python model/scripts/view_agent_content.py "…/새 파일.sqlite3" --new "…/예전 파일.sqlite3" --share "…/새_글.html"

import base64
import csv
import html
import io
import json
import pathlib
import sqlite3
import sys
import webbrowser
from urllib.parse import urlsplit

from PIL import Image

SHARE_WIDTH = 540        # 보내기용 파일의 카드 이미지 가로 (픽셀). 원본은 1장에 1MB 가까이라 줄임

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
OUT = DATA / "agent_view"
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
CSS = """
body{margin:0;padding:16px;font-family:system-ui,'Malgun Gothic',sans-serif;background:#f4f4f5;color:#18181b}
h1{font-size:20px} .post{background:#fff;border-radius:12px;padding:16px;margin:0 0 20px;box-shadow:0 1px 3px #0002}
.meta{font-size:13px;color:#52525b;line-height:1.7} .chip{display:inline-block;background:#eef2ff;color:#3730a3;
border-radius:6px;padding:0 6px;margin-right:4px} .warn{color:#b91c1c;font-size:13px;margin-top:6px}
.cards{display:flex;gap:10px;overflow-x:auto;padding:10px 0} .card{flex:0 0 auto;width:300px}
.card img{width:300px;border-radius:8px;display:block} .text{width:300px;min-height:200px;border:1px dashed #a1a1aa;
border-radius:8px;padding:10px;box-sizing:border-box;font-size:13px} .page{font-size:12px;color:#71717a}
details{margin-top:8px;font-size:13px} summary{cursor:pointer;color:#3730a3} table.kv{border-collapse:collapse;margin:6px 0;width:100%}
table.kv th,table.kv td{border:1px solid #e4e4e7;padding:3px 6px;text-align:left;vertical-align:top;word-break:break-all} table.kv th{background:#f4f4f5;white-space:nowrap}
"""


def esc(x):
    return html.escape("" if x is None else str(x))


def shrunk(data):
    # 보내기용: 가로 SHARE_WIDTH 로 줄인 JPEG 을 HTML 에 바로 넣을 수 있는 주소(data URI)로
    im = Image.open(io.BytesIO(data))
    if im.mode != "RGB":
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").getchannel("A"))
        im = bg
    if im.width > SHARE_WIDTH:
        im = im.resize((SHARE_WIDTH, round(im.height * SHARE_WIDTH / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def open_db(path):
    return sqlite3.connect(f"file:{pathlib.Path(path).as_posix()}?mode=ro&immutable=1", uri=True)


def main(db_path, share=None, old_db=None):
    db = open_db(db_path)
    skip = {r for (r,) in open_db(old_db).execute("select request_id from feed_queue")} if old_db else set()
    labels = dict(db.execute("select type_code, label_ko from content_types"))
    names = {}
    works_csv = DATA / "menu_works.csv"
    if works_csv.exists():
        with open(works_csv, encoding="utf-8-sig") as f:
            names = {int(r["menu_work_id"]): r["title_ko"] or r["title_romaji"] for r in csv.DictReader(f)}
    has = lambda t: db.execute("select 1 from sqlite_master where type='table' and name=?", (t,)).fetchone()
    links = {}
    if has("content_work_links"):
        for rid, wid, prim in db.execute("select request_id, menu_work_id, is_primary from content_work_links order by sort_order"):
            links.setdefault(rid, []).append((wid, prim))
    quality = dict(db.execute("select request_id, quality_score from content_drafts"))

    (OUT / "img").mkdir(parents=True, exist_ok=True)
    seen_src, posts = {}, []
    rows = db.execute("select request_id, topic, content_type, package_json, queued_at, status, valid_until "
                      "from feed_queue order by queued_at").fetchall()
    for rid, topic, ctype, pkg, queued, status, until in rows:
        p = json.loads(pkg)
        src = p.get("sources") or []
        warns = []
        if not any(s.get("published_at") for s in src):
            warns.append("원문 발표일 없음")
        w = links.get(rid, [])
        if not any(prim for _, prim in w):
            warns.append("대표 작품 없음")
        for wid, _ in w:
            if len(str(names.get(wid, "")).replace(" ", "")) <= 2:
                warns.append(f"짧은 제목 연결 의심: {names.get(wid, wid)}")
        for s in src:                                  # 같은 원문 (주소의 ? 뒤는 빼고 비교)
            u = urlsplit(s.get("url") or "")
            key = f"{u.netloc}{u.path}"
            if key in seen_src:
                warns.append(f"같은 원문이 앞 글에도 있음: {seen_src[key]}")
            seen_src.setdefault(key, topic)
        last_day = p.get("event_end") or p.get("event_start")   # 행사·발매가 끝나기(또는 시작하기) 전에 노출이 끝나는지
        if last_day and until and until[:10] < last_day[:10]:
            warns.append(f"노출 끝({until[:10]})이 행사·발매 날짜({last_day[:10]})보다 빠름")
        if rid in skip:                                # --new: 예전 DB 에 있던 글은 중복 확인에만 쓰고 안 보여줌
            continue

        cards = []
        for page, role, eyebrow, title, body, key in db.execute(
                "select page, role, eyebrow, title_lines_json, body_json, rendered_image_key from content_cards "
                "where request_id=? order by page", (rid,)):
            blob = db.execute("select media_type, binary_data from image_blobs where sha256=?", (key,)).fetchone() if key else None
            if blob and blob[0] in EXT:
                if share:
                    img = shrunk(blob[1])
                else:
                    img = f"img/{key}.{EXT[blob[0]]}"
                    if not (OUT / img).exists():
                        (OUT / img).write_bytes(blob[1])
                inner = f'<img src="{esc(img)}" loading="lazy" alt="{esc(page)}장">'
            else:                                      # 완성 이미지가 없으면 글자만
                lines = " / ".join(json.loads(title or "[]"))
                inner = (f'<div class="text"><b>{esc(eyebrow)}</b><br>{esc(lines)}<br><br>'
                         f'{esc(" ".join(json.loads(body or "[]")))}</div>')
            cards.append(f'<div class="card"><div class="page">{esc(page)}장 · {esc(role)}</div>{inner}</div>')

        work_html = " ".join(f'<span class="chip">{"대표 " if prim else ""}{esc(names.get(wid, wid))} ({esc(wid)})</span>'
                             for wid, prim in w) or "없음"
        region = (f'지역: 행사 {esc(p.get("event_country") or "-")} · {esc(p.get("event_region") or "-")} '
                  f'({esc(p.get("event_location") or "장소 없음")}) / 원문 사이트 '
                  f'{esc(", ".join(sorted({s.get("region") or "-" for s in src})))} / 독자 {esc(p.get("audience_region") or "-")}')
        # 저장된 데이터 전부 (카드·이미지 말고): 글 정보 · 원문 · 사실
        info = "".join(f"<tr><th>{esc(k)}</th><td>{esc(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)}</td></tr>"
                       for k, v in p.items() if k not in ("cards", "images", "prior_image_fingerprints", "facts", "sources"))
        srcs = "".join(f"<tr><td>{esc(s.get('publisher'))}</td><td>{esc(s.get('region'))}</td><td>{esc(s.get('published_at'))}</td>"
                       f"<td>{esc(s.get('is_official'))}</td><td>{esc(s.get('url'))}</td></tr>" for s in src)
        facts = "".join(f"<li>{esc(f.get('statement'))} <span class='page'>[{esc(f.get('fact_type'))}"
                        f"{' · 원문: ' + esc((f.get('details') or {}).get('source_quote')) if (f.get('details') or {}).get('source_quote') else ''}]</span></li>"
                        for f in p.get("facts", []))
        stored = (f'<details><summary>저장된 데이터 보기</summary><table class="kv">{info}</table>'
                  f'<b>원문</b><table class="kv"><tr><th>출처</th><th>원문 지역</th><th>발표일</th><th>공식</th><th>주소</th></tr>{srcs}</table>'
                  f'<b>사실 {len(p.get("facts", []))}개</b><ol>{facts}</ol></details>')
        src_html = " · ".join(f'<a href="{esc(s.get("url"))}" target="_blank" rel="noopener">{esc(s.get("publisher") or "원문")}</a>'
                              f' (발표일 {esc(s.get("published_at") or "없음")})' for s in src)
        posts.append(
            f'<div class="post"><h2>{esc(topic)}</h2><div class="meta">'
            f'<span class="chip">{esc(labels.get(ctype, ctype))}</span> 상태 {esc(status)} · 품질 {esc(quality.get(rid, "-"))} · '
            f'만든 날 {esc((queued or "")[:10])} · 노출 끝 {esc((until or "")[:10])} · '
            f'행사 {esc(p.get("event_start") or "-")} ~ {esc(p.get("event_end") or "-")}<br>'
            f'작품: {work_html}<br>{region}<br>원문: {src_html or "없음"}</div>'
            + "".join(f'<div class="warn">⚠ {esc(x)}</div>' for x in warns)
            + f'<div class="cards">{"".join(cards)}</div>{stored}</div>')

    page = (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<title>에이전트 글 보기</title><style>{CSS}</style></head><body>'
            f'<h1>에이전트 {"새 " if old_db else ""}글 {len(posts)}개 · {esc(pathlib.Path(db_path).name)}</h1>'
            f'{"".join(posts)}</body></html>')
    target = pathlib.Path(share) if share else OUT / "index.html"
    target.write_text(page, encoding="utf-8")
    print(f"글 {len(posts)}개 → {target} ({target.stat().st_size / 1e6:.1f}MB)")
    webbrowser.open(target.as_uri())


if __name__ == "__main__":
    arg = lambda name: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None
    main(sys.argv[1], arg("--share"), arg("--new"))
