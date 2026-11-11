"""나무위키 → 정보사전: 감독 · 작가 · 가수 · 제작사 문서의 개요 · 특징 문단 (참고 글)

  uv run python -m ani_rag.load_namuwiki              (agents/rag 폴더에서)
  uv run python -m ani_rag.load_namuwiki --no-fetch   (요청 없이, 이미 뽑아 둔 문단으로 사전에 넣기만)

- 라이선스 CC BY-NC-SA 2.0 KR (비영리 · 출처 표시 · 같은 조건). 학교 비상업 프로젝트라 서원 결정(11/11)으로 쓴다.
  사전에는 참고 글로만 두고, 글에는 문장을 옮기지 않는다 (새로 쓴다). 상업화하면 다시 확인할 것
- robots.txt 는 문서(/w/)를 허용함 (11/11 확인). 3초 간격, 403 · 429 면 바로 멈춘다. 원본 HTML 은 저장하지 않는다
- 대상: 대량 생산(--mass, 유형마다 100개)의 제작사 · 가수 · 감독 · 작가 글에 나오는 인물 · 제작사
- 문서 찾기: 한국어 이름 → 이름(직업) → 원어 이름 순서. 문서 안에 원어 이름이 있거나(인물),
  애니 제작사라는 말이 있어야(제작사) 맞다고 본다. 동음이의어 문서는 건너뛴다
- 뽑는 것: '개요' + 제목에 특징 · 작풍 · 연출 · 목소리 · 음색 · 창법 · 평가 · 성향 · 스타일이 들어간 문단 (문단마다 500자)
- 이 스크립트가 맡는 칸 (출처 namuwiki): namu_profile_ref
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import psycopg

import ani_rag.build_manifest as bm
from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store

NAMU = "namuwiki"
UA = "menu-dict/0.1 (school project)"
PAUSE = 3.0
CACHE = PACKAGE_ROOT / ".cache" / "raw" / "namuwiki"
FIELDS = ("namu_profile_ref",)
HEAD = re.compile(r"(\d+(?:\.\d+)*)\.\s*([^\[\]]{1,30}?)\s*\[편집\]")
RUBY = re.compile(r"\(\s*[぀-ヿー・\s]+\s*\)")  # 이름 옆 읽는 법 '( ゆあさ )'
KEYS = ("개요", "특징", "작풍", "연출", "목소리", "음색", "창법", "평가", "성향", "스타일", "작품 성향")
ROLE = {"studio": "제작사", "artist": "가수", "director": "감독", "author": "작가"}


class Blocked(Exception):
    pass


_last = 0.0


def fetch(title: str) -> str:
    """문서 하나의 본문 글자 (태그를 걷어 냄). 없으면 빈 문자열, 막히면 Blocked"""
    global _last
    time.sleep(max(0.0, _last + PAUSE - time.time()))
    _last = time.time()
    req = urllib.request.Request("https://namu.wiki/w/" + urllib.parse.quote(title), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=40) as res:
            raw = res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise Blocked(f"나무위키가 {e.code} 로 막음") from e
        return ""
    except (urllib.error.URLError, TimeoutError):
        return ""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text)


def sections(text: str) -> list[tuple[str, str]]:
    """(문단 제목, 내용) — 개요와 특징 쪽 문단만, 문단마다 500자"""
    heads = list(HEAD.finditer(text))
    out = []
    for i, m in enumerate(heads):
        title = m.group(2).strip()
        if not any(k in title for k in KEYS):
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = re.sub(r"\[\d+\]|\[편집\]", " ", text[m.end():end])
        body = re.sub(r"\s+", " ", body).strip()
        if len(body) >= 40:
            out.append((title, body[:500]))
    return out[:4]


def targets(cur) -> list[dict]:
    """대량 생산과 같은 규칙으로 제작사 · 가수 · 감독 · 작가 글감을 골라 그 인물 · 제작사를 모은다 (DB 읽기만)"""
    bm.MASS = True
    for kind in bm.QUOTA:
        bm.QUOTA[kind] = 100
    ctx = bm.Ctx(cur)
    decks = [*bm.pick_studio(ctx), *bm.pick_music(ctx), *bm.pick_directors(ctx), *bm.pick_origin(ctx)]
    out, seen = [], set()
    for d in decks:
        if d.kind not in ROLE:
            continue
        eid = int(d.request_id.rsplit("-", 1)[1][1:])
        if eid in seen:
            continue
        seen.add(eid)
        _, ko, native, romaji, _ = ctx.ent[eid]
        name = ctx.studio(eid) if d.kind == "studio" else ctx.name(eid)
        out.append({"entity": eid, "kind": d.kind, "name": name, "ko": ko or ctx.ko.get(eid), "native": native,
                    "romaji": romaji})
    return out


def find(t: dict) -> tuple[str, str] | None:
    """(문서 제목, 본문) — 맞는 문서를 못 찾으면 None"""
    names = [n for n in dict.fromkeys([t["ko"], t["name"], f"{t['ko'] or t['name']}({ROLE[t['kind']]})",
                                       t["native"], t["romaji"]]) if n]
    for title in names[:4]:
        text = fetch(title)
        if not text or "동음이의어" in text[:4000]:
            continue
        if t["kind"] == "studio":
            ok = "애니메이션" in text and any(k in text for k in ("제작사", "스튜디오", "기업"))
        else:
            # 나무위키는 '湯浅 ( ゆあさ ) 政明 ( まさあき )' 처럼 읽는 법을 달아서, 괄호 속 가나를 걷어 내고 맞춘다 (11/11)
            native = (t["native"] or "").replace(" ", "")
            ok = bool(native) and native in RUBY.sub("", text).replace(" ", "")
        if ok:
            return title, text
    return None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch", action="store_true", help="요청 없이 뽑아 둔 문단으로 사전에 넣기만")
    args = parser.parse_args()
    started = time.time()
    settings = load_settings()
    CACHE.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        todo = targets(cur)
        conn.rollback()
    print(f"대상 {len(todo)}곳 (제작사 · 가수 · 감독 · 작가)", flush=True)
    requests_before = 0
    if not args.no_fetch:
        for n, t in enumerate(todo, 1):
            path = CACHE / f"{t['entity']}.json"
            if path.exists():
                continue
            try:
                found = find(t)
            except Blocked as e:
                print(f"{e} → 멈춤 ({n}번째 대상)", flush=True)
                break
            record = {"entity": t["entity"], "kind": t["kind"], "name": t["name"], "title": None, "sections": []}
            if found:
                title, text = found
                record.update(title=title, sections=sections(text))
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            if n % 20 == 0:
                print(f"  {n}/{len(todo)} · 지금 {t['name']} → {record['title']} · 문단 {len(record['sections'])}",
                      flush=True)
    found = 0
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        now = dt.datetime.now(dt.UTC)
        for path in CACHE.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if not record.get("title") or not record.get("sections"):
                continue
            found += 1
            text = "\n".join(f"[{t}] {b}" for t, b in record["sections"])
            store.fact(record["entity"], "namu_profile_ref", {"text": text, "title": record["title"]}, now,
                       "https://namu.wiki/w/" + urllib.parse.quote(record["title"]), NAMU)
        upserted, deleted = store.write_facts(NAMU, FIELDS)
    print(f"문서 찾고 특징 문단이 있는 곳 {found}/{len(todo)} · 사실 넣거나 고침 {upserted} · 지움 {deleted} · "
          f"{time.time() - started:.0f}초 (요청 전 {requests_before})")


if __name__ == "__main__":
    main()
