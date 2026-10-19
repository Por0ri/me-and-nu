# fetch_anilist — AniList 에서 작품 정보(제목·장르·태그)를 받아 저장한다
#
# 서비스 기준 작품 번호·장르·태그는 AniList 다. 채점기(MAL)와는 MAL 번호(idMal)로 잇는다.
# AniList 약관: 대량 수집·저장소처럼 쓰기 금지 → 필요한 작품만, 필요한 칸만, 한 번만 받는다.
# - 기본: MAL 표본에서 많이 본 상위 N편 (기본 3000) → media.jsonl
# - recent: 2022년 이후 시작한 인기작 상위 N편 (기본 1000). MAL 데이터(~2022-03)에 없는 최신작 → media_recent.jsonl
# - 칸: AniList id, MAL id, 제목, 형식(TV·극장판 등), 시작 연도, 장르, 태그(이름·관련도·분류·스포일러 여부), 성인물 여부
# - 이미 받은 작품·페이지는 다시 요청하지 않는다
# - 요청 사이 2.1초 이상 쉰다 (분당 30회 이하). 429 응답이면 Retry-After 만큼 기다린다
#
#   python model/scripts/export_mal_sample.py          # 먼저 MAL 표본 (한 번만)
#   python model/scripts/fetch_anilist.py              # MAL 인기 상위 3000편
#   python model/scripts/fetch_anilist.py 50           # 50편만 (시험용)
#   python model/scripts/fetch_anilist.py recent 1000  # 2022년 이후 인기작 1000편
#   → model/data/anilist/ (커밋 안 함)

import csv
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1] / "data"
MAL = ROOT / "mal_sample" / "interactions.csv"
OUT = ROOT / "anilist" / "media.jsonl"
OUT_RECENT = ROOT / "anilist" / "media_recent.jsonl"
URL = "https://graphql.anilist.co"
PER_REQUEST = 50
PAUSE = 2.1
RECENT_AFTER = 20211231  # 이 날짜 다음부터 시작한 작품 (YYYYMMDD)

FIELDS = """
      id idMal isAdult genres format
      startDate { year }
      title { romaji english native }
      tags { name rank category isAdult isGeneralSpoiler isMediaSpoiler }
"""
QUERY = """
query ($ids: [Int]) {
  Page(page: 1, perPage: 50) {
    media(idMal_in: $ids, type: ANIME) {%s}
  }
}
""" % FIELDS
QUERY_RECENT = """
query ($page: Int, $after: FuzzyDateInt) {
  Page(page: $page, perPage: 50) {
    media(type: ANIME, startDate_greater: $after, sort: POPULARITY_DESC, isAdult: false) {%s}
  }
}
""" % FIELDS


def top_mal_ids(n):
    # 표본 유저 중 몇 명이 봤는지로 순위를 매긴다 (보고 싶어요는 표본에서 이미 빠져 있음)
    pop = Counter()
    seen = set()
    with open(MAL, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["user_id"], r["anime_id"])
            if key not in seen:
                seen.add(key)
                pop[int(r["anime_id"])] += 1
    return [a for a, _ in pop.most_common(n)]


def already_fetched():
    if not OUT.exists():
        return set()
    done = set()
    with open(OUT, encoding="utf-8") as f:
        for line in f:
            done.update(json.loads(line)["requested_mal_ids"])
    return done


def post(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "menu-recommender-research/0.1"})
    while True:
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)["data"]["Page"]["media"], resp.headers.get("X-RateLimit-Remaining")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "60"))
                print(f"  요청 한도 초과 → {wait}초 기다림", flush=True)
                time.sleep(wait + 1)
                continue
            raise


def fetch_recent(n):
    # 2022년 이후 시작한 작품을 인기순으로 n편. 이미 받은 페이지는 건너뛴다
    OUT_RECENT.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if OUT_RECENT.exists():
        with open(OUT_RECENT, encoding="utf-8") as f:
            done = {json.loads(line)["page"] for line in f}
    pages = [p for p in range(1, (n + PER_REQUEST - 1) // PER_REQUEST + 1) if p not in done]
    print(f"2022년 이후 인기작 {n}편 → 새로 받을 페이지 {len(pages)}개", flush=True)
    with open(OUT_RECENT, "a", encoding="utf-8", newline="\n") as f:
        for p in pages:
            media, remaining = post(QUERY_RECENT, {"page": p, "after": RECENT_AFTER})
            f.write(json.dumps({"page": p, "media": media}, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  {p}페이지 · {len(media)}편 · 남은 한도 {remaining}", flush=True)
            time.sleep(PAUSE)
    print(f"끝 → {OUT_RECENT}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "recent":
        fetch_recent(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
        return
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = already_fetched()
    todo = [a for a in top_mal_ids(n) if a not in done]
    print(f"상위 {n}편 중 이미 받음 {n - len(todo)} · 새로 받을 것 {len(todo)} "
          f"→ 요청 {(len(todo) + PER_REQUEST - 1) // PER_REQUEST}번", flush=True)
    got = 0
    with open(OUT, "a", encoding="utf-8", newline="\n") as f:
        for i in range(0, len(todo), PER_REQUEST):
            ids = todo[i:i + PER_REQUEST]
            media, remaining = post(QUERY, {"ids": ids})
            # 요청한 번호도 같이 남겨서, AniList 에 없는 작품도 다시 요청하지 않게 한다
            f.write(json.dumps({"requested_mal_ids": ids, "media": media}, ensure_ascii=False) + "\n")
            f.flush()
            got += len(media)
            print(f"  {i + len(ids)}/{len(todo)} · 받은 작품 {got} · 남은 한도 {remaining}", flush=True)
            time.sleep(PAUSE)
    print(f"끝 → {OUT}")


if __name__ == "__main__":
    main()
