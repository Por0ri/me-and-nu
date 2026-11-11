"""온나다 → 캐릭터 한국어 이름 · 생일 · 작품 한국어 제목 후보 (사실만. 소개 글은 저장하지 않는다)

  uv run python -m ani_rag.load_onnada [--top 1000] [--limit N]        (agents/rag 폴더에서)

- 구독 전용 표시(subscription-gated)가 있는 작품 상세는 요청하지 않는다. 캐릭터 검색 · 캐릭터 페이지 · 생일 달력만 쓴다
- 대상: 인기 상위 작품의 주연 중 확정 한국어 이름이나 생일이 없는 캐릭터. 일본어 이름으로 검색한다 (인기순)
- 3초에 한 번. 403 · 429 를 받으면 바로 멈춘다. 받은 결과는 .cache/raw/onnada/ 에 필요한 칸만 저장 (끊겨도 이어서)
- 맞추기: 검색 결과의 작품 제목이 우리 제목과 맞으면 그것. 안 맞아도 한국어 이름이 하나뿐이고 일본어 이름이
  세 글자 이상이면 인정. 그 밖에는 넣지 않는다
- 이 스크립트가 맡는 칸 (출처 onnada): name_ko, birthday. 별칭: 캐릭터 한국어 이름, 작품 한국어 제목(후보)
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
from collections import Counter, defaultdict
from http.client import HTTPException

import psycopg

from ani_rag.build_manifest import Ctx, main_casts
from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store

ONNADA = "onnada"
BASE = "https://onnada.com"
UA = "menu-dict/0.1 (school project)"
PAUSE = 3.0
FIELDS = ("name_ko", "birthday")
RAW = PACKAGE_ROOT / ".cache" / "raw" / "onnada"
RESULT = re.compile(r'href="/character/(\d+)"><article.*?<p[^>]*>(.*?)</p><p[^>]*>(.*?)</p><p[^>]*>(.*?)</p>', re.S)
CALENDAR_DAY = re.compile(r'aria-label="\d{4}년 (\d{1,2})월 (\d{1,2})일 [^"]*"')
CALENDAR_ITEM = re.compile(r'href="/character/(\d+)">.*?<p[^>]*>(.*?)</p></a><a[^>]*href="/anime/\d+"[^>]*>(.*?)</a>', re.S)
last_request = 0.0


def norm(text: str | None) -> str:
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def get(url: str) -> str:
    global last_request
    time.sleep(max(0.0, last_request + PAUSE - time.time()))
    last_request = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=40) as res:
                return res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            if e.code in (403, 429):
                raise SystemExit(f"온나다가 요청을 막음 ({e.code}) → 멈춤. 지금까지 받은 것은 저장돼 있음") from None
            time.sleep(30 * (attempt + 1))
        except (OSError, HTTPException):
            time.sleep(30 * (attempt + 1))
    raise SystemExit("온나다 요청이 4번 연속 실패함")


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def cached(path, fetch) -> dict:
    """저장해 둔 결과가 있으면 그것, 없으면 받아서 저장"""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    record = {"at": now(), **fetch()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return record


def search(native: str) -> dict:
    text = get(f"{BASE}/character/search?q={urllib.parse.quote(native)}")
    return {"q": native, "results": [{"id": int(a), "name": clean(b), "work": clean(c), "date": clean(d)}
                                     for a, b, c, d in RESULT.findall(text)]}


def character(onnada_id: int) -> dict:
    """캐릭터 페이지에서 이름 · 생일만 꺼낸다. 구독 표시가 있으면 쓰지 않는다"""
    text = get(f"{BASE}/character/{onnada_id}")
    if "subscription-gated" in text:
        return {"gated": True}
    name = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
    chips = re.findall(r'data-slot="chip-label">(.*?)</span>', text)
    born = re.search(r"<dt[^>]*>생일</dt><dd[^>]*>(\d{2})(\d{2})</dd>", text)
    return {"name": clean(name.group(1)) if name else None, "native": clean(chips[0]) if chips else None,
            "month": int(born.group(1)) if born else None, "day": int(born.group(2)) if born else None}


def calendar() -> dict:
    """이번 달 생일 달력 (달을 고르는 주소가 없어 이번 달만)"""
    text = get(f"{BASE}/character/birthday")
    if "subscription-gated" in text:
        return {"gated": True, "items": []}
    parts = CALENDAR_DAY.split(text)
    items = []
    for i in range(1, len(parts) - 2, 3):
        month, day, body = int(parts[i]), int(parts[i + 1]), parts[i + 2]
        items += [{"id": int(a), "name": clean(b), "work": clean(c), "month": month, "day": day}
                  for a, b, c in CALENDAR_ITEM.findall(body)]
    return {"items": items}


def choose(results: list[dict], titles: set[str], native: str) -> dict | None:
    """검색 결과에서 고른다. 여러 시즌에 같은 캐릭터가 있으면 가장 이른 것"""
    earliest = lambda items: min(items, key=lambda r: r["date"] or "9999")
    hits = [r for r in results if any(t in norm(r["work"]) or norm(r["work"]) in t for t in titles if len(t) >= 2)]
    if hits:
        return earliest(hits)
    if len({norm(r["name"]) for r in results}) == 1 and len(native) >= 3:
        return earliest(results)
    return None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=1000, help="인기 상위 몇 작품의 주연까지")
    ap.add_argument("--limit", type=int, default=0, help="시험용: 앞에서 N명만")
    ap.add_argument("--cached", action="store_true", help="새로 요청하지 않고 받아 둔 결과만 사전에 넣는다")
    args = ap.parse_args()
    started = time.time()
    settings = load_settings()
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        ctx = Ctx(cur)
        mains = main_casts(ctx)
        order = [(c, w) for w in ctx.order[:args.top] for c in mains.get(w, {})]
        have = {c for (c, _), rows in ctx.rows({c for c, _ in order}, ("birthday",)).items()
                if any(r.source != ONNADA for r in rows)}  # 온나다 말고 다른 출처의 생일이 있는 캐릭터
        cur.execute("select entity_id, alias from dict.alias where lang = 'ko'")
        aliases: dict[int, set[str]] = defaultdict(set)
        for e, alias in cur.fetchall():
            aliases[e].add(norm(alias))
        conn.rollback()

    def titles_of(w: int) -> set[str]:
        found = set(aliases.get(w, ())) | {norm(ctx.title.get(w))}
        for e in ctx.work_entries[w]:
            found |= aliases.get(e, set()) | {norm(ctx.entry_ko.get(e))}
        return {t for t in found if t}

    seen: set[int] = set()
    targets = []
    for c, w in order:
        native = (ctx.ent[c][2] or "").strip()
        # 이미 받은 캐릭터는 다시 돌려도 빠지지 않게 (이번 결과로 칸을 맞추기 때문에 빠지면 지워진다)
        if c not in seen and native and (c not in ctx.ko or c not in have or (RAW / "search" / f"{c}.json").exists()):
            seen.add(c)
            targets.append((c, w, native))
    if args.limit:
        targets = targets[:args.limit]
    print(f"대상 주연 {len(targets):,}명 · 약 {len(targets) * PAUSE / 60:.0f}분 + 캐릭터 페이지", flush=True)

    month = dt.datetime.now().strftime("%Y%m")
    births = cached(RAW / f"birthday_{month}.json", calendar)
    by_onnada = {it["id"]: it for it in births["items"]}
    print(f"이번 달 생일 달력 {len(by_onnada):,}명", flush=True)

    matched: dict[int, tuple[int, dict, str]] = {}
    for n, (c, w, native) in enumerate(targets, 1):
        if args.cached and not (RAW / "search" / f"{c}.json").exists():
            continue
        record = cached(RAW / "search" / f"{c}.json", lambda: search(native))
        hit = choose(record["results"], titles_of(w), native)
        if hit:
            matched[c] = (w, hit, record["at"])
        if n % 100 == 0 or n == len(targets):
            print(f"  검색 {n:,}/{len(targets):,} · 찾음 {len(matched):,}", flush=True)

    pages: dict[int, dict] = {}
    need = [(c, v) for c, v in matched.items() if c not in have and v[1]["id"] not in by_onnada]
    print(f"생일을 보려고 캐릭터 페이지를 볼 캐릭터 {len(need):,}명 · 약 {len(need) * PAUSE / 60:.0f}분", flush=True)
    for n, (c, (_, hit, _)) in enumerate(need, 1):
        if args.cached and not (RAW / "character" / f"{hit['id']}.json").exists():
            continue
        pages[c] = cached(RAW / "character" / f"{hit['id']}.json", lambda: character(hit["id"]))
        if n % 100 == 0 or n == len(need):
            print(f"  캐릭터 페이지 {n:,}/{len(need):,}", flush=True)

    names: dict[int, list[tuple[str, str]]] = defaultdict(list)
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        birthdays = 0
        for c, (w, hit, at) in matched.items():
            url, when = f"{BASE}/character/{hit['id']}", dt.datetime.fromisoformat(at)
            store.fact(c, "name_ko", hit["name"], when, url, ONNADA)
            store.alias(c, hit["name"], "ko", ONNADA)
            names[w].append((hit["work"], hit["date"]))
            born = by_onnada.get(hit["id"]) or pages.get(c) or {}
            if born.get("month") and born.get("day"):
                store.fact(c, "birthday", {"month": born["month"], "day": born["day"]}, when, url, ONNADA)
                birthdays += 1
        candidates = 0
        for w, items in names.items():
            if w not in ctx.title:  # 확정 제목이 없는 작품만 후보를 남긴다. 가장 이른 시즌의 제목을 표로 고른다
                first_date = min(d or "9999" for _, d in items)
                pick = Counter(t for t, d in items if (d or "9999") == first_date).most_common(1)[0][0]
                store.alias(w, pick, "ko", ONNADA, candidate=True)
                candidates += 1
        upserted, deleted = store.write_facts(ONNADA, FIELDS)
        aliases_written = store.write_aliases(sync_source=ONNADA)

    new_names = sum(1 for c in matched if c not in ctx.ko)
    print(f"찾은 캐릭터 {len(matched):,}명 / {len(targets):,}명 · 새 한국어 이름 {new_names:,}명 · 생일 {birthdays:,}명 · 작품 제목 후보 {candidates:,}편")
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,} · 별칭 {aliases_written:,}")
    print(f"걸린 시간 {time.time() - started:.0f}초")
    for c, (w, hit, _) in list(matched.items())[:12]:
        print(f"  {ctx.ent[c][2]} → {hit['name']} | {hit['work']} | 우리 작품: {ctx.title.get(w, '-')}")


if __name__ == "__main__":
    main()
