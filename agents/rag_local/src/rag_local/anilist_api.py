"""AniList API 공용 — 요청 간격 · 제한 처리 · 원본 저장

- 앞 요청 시작에서 4초가 지나야 다음 요청
  (분당 15번 = 지금 AniList 제한 분당 30번의 절반, 운영진의 "간격을 넉넉히" 부탁)
- 429 면 Retry-After 만큼 쉰다
- 받은 원본은 raw_dir/page_NNN.json 에 저장하고, 이미 받은 번호는 다시 요청하지 않는다 (끊기거나 번호가 늘어도 이어서)
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.client import HTTPException

URL = "https://graphql.anilist.co"
UA = "menu-dict/0.1 (school project)"
PAUSE = 4.0
BATCH = 50


def post(query: str, variables: dict) -> tuple[dict, str | None]:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return json.loads(res.read()), res.headers.get("X-RateLimit-Remaining")
        except urllib.error.HTTPError as e:
            if e.code == 429:  # 제한에 걸리면 알려 준 시간만큼 쉼
                wait = int(e.headers.get("Retry-After") or 60)
                print(f"  제한에 걸림 → {wait + 1}초 쉼", flush=True)
                time.sleep(wait + 1)
            elif e.code >= 500:
                time.sleep(30 * (attempt + 1))
            else:
                raise
        except (OSError, HTTPException):  # 인터넷 끊김 · 연결 끊김 · 시간 초과
            time.sleep(30 * (attempt + 1))
    raise SystemExit("AniList 요청이 5번 연속 실패함")


def fetch_batches(query: str, ids: list[int], raw_dir: pathlib.Path, batch_size: int = BATCH) -> int:
    """ids 중 아직 안 받은 번호만 batch_size 개씩 묶어 {"ids": [...]} 로 요청. 보낸 요청 수를 돌려줌"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    pages = list(raw_dir.glob("page_*.json"))
    have = {i for p in pages for i in json.loads(p.read_text(encoding="utf-8"))["ids"]}
    rest = [i for i in ids if i not in have]
    todo = [rest[s:s + batch_size] for s in range(0, len(rest), batch_size)]
    start = max((int(p.stem.split("_")[1]) for p in pages), default=-1) + 1
    minutes = len(todo) * PAUSE / 60
    print(f"요청: 전체 {len(ids):,}개 중 남은 {len(rest):,}개 · {len(todo)}번 · 약 {minutes:.0f}분", flush=True)
    last = 0.0
    for sent, batch in enumerate(todo, 1):
        time.sleep(max(0.0, last + PAUSE - time.time()))
        last = time.time()
        data, remaining = post(query, {"ids": batch})
        if data.get("errors"):
            raise SystemExit(f"AniList 오류: {data['errors'][:1]}")
        record = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "ids": batch, "data": data}
        path = raw_dir / f"page_{start + sent - 1:03d}.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        if sent % 25 == 0 or sent == len(todo):
            print(f"  {sent}/{len(todo)}번 받음 (이번 분에 남은 요청 {remaining})", flush=True)
    return len(todo)


def read_batches(raw_dir: pathlib.Path) -> Iterator[tuple[dt.datetime, dict]]:
    """(받은 날짜, 응답 data) — 저장해 둔 원본을 순서대로"""
    for path in sorted(raw_dir.glob("page_*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        yield dt.datetime.fromisoformat(record["fetched_at"]), record["data"]["data"]
