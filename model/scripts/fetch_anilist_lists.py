# fetch_anilist_lists — AniList 공개 유저 애니 목록을 받는다 (1-3). AniList 운영진 허락 범위 그대로 (EXPERIMENTS 1장)
#
# 약속: 최대 1만 명 · 한 번만 · 작품 번호 / 상태 / 점수 / 기록 날짜만 · 유저 이름 · 프로필 · 리뷰 · 소셜 데이터 안 받음
#       유저 번호는 무작위 숫자로 바꿔 저장 · 원본은 이 PC 에만 (공유 · 공개 안 함) · 12월 31일까지 삭제
# 속도: 4초에 1번 (분당 15번 = 약속한 분당 30번의 절반. "요청 간격을 더 띄워 달라"는 부탁). 429 면 Retry-After 만큼 기다림
#
# 유저 찾기 (요즘도 애니를 보는 팬을 고르게): AniList 엔 유저 전체 명단이 없어서 "작품 × 주" 명단에서 찾는다
#   1) 2022년 이후 작품 1,000편 중 하나를 그 작품을 목록에 담은 사람 수(인기)에 비례해서 뽑음 → 인기작만 보는 팬도 실제 비율만큼
#   2) 2023-01 ~ 2026-09 중 한 주를 무작위로 → "그 주에 그 작품을 다 본 사람" 명단 (방영 당시 열성 팬만이 아니라 나중에 본 사람도)
#   3) 명단이 50명보다 길면 무작위 쪽을 봄 (앞쪽 = 먼저 목록에 올린 열성 팬만 나오는 것 막기. 볼 수 있는 깊이는 5,000명까지)
#      AniList 는 긴 명단의 마지막 쪽을 실제보다 크게 알려 줌 (몇 쪽짜리도 "100쪽"). 고른 쪽이 비어 있으면
#      그 앞쪽 중에서 다시 뽑는다 → 실제 있는 쪽 중 하나를 고르게 고름 (시범에서 무작위 쪽 13번 중 11번이 빈 쪽이었음)
#   후보를 모은 뒤 무작위로 골라 목록을 받음. 다 본 작품 20~1,000편 · 2025년 이후 활동한 유저만 남김 (보고 싶어요는 안 남김)
#   많이 보는 사람은 여러 명단에 나와 뽑히기 쉬움 → 쓸 때 "나올 수 있었던 명단 수"로 보정 (뽑은 명단 기록을 진행 파일에 남김)
# 이어받기: 진짜 유저 번호가 든 진행 파일(_progress.json)은 다 받은 뒤 --finish 로 지운다
#           (보정에 쓸 명단 기록은 유저 번호 없이 slices.json 으로 남김)
#
#   python model/scripts/fetch_anilist_lists.py 100        # 시범: 100명
#   python model/scripts/fetch_anilist_lists.py 10000      # 이어서 1만 명까지 (중간에 끊겨도 다시 돌리면 이어서)
#   python model/scripts/fetch_anilist_lists.py --finish   # 다 받은 뒤: 진짜 유저 번호가 든 진행 파일 지우기
#   → model/data/anilist_users/entries.csv (커밋 안 함)

import csv
import datetime as dt
import json
import pathlib
import random
import secrets
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1] / "data"
OUT = ROOT / "anilist_users"
ENTRIES = OUT / "entries.csv"
PROGRESS = OUT / "_progress.json"          # 진짜 유저 번호가 들어 있음 → 다 받은 뒤 --finish 로 지움
SLICES = OUT / "slices.json"               # 본 명단 기록 (작품 · 주 · 알려 준 마지막 쪽 · 고른 쪽 · 찾은 수). 유저 번호 없음
URL = "https://graphql.anilist.co"
PAUSE = 4.0                                # 요청 시작 사이 간격 (초). 분당 15번
MAX_USERS = 10000                          # 허락받은 최대 인원
COMPLETED_RANGE = (20, 1000)               # 다 본 작품 수가 이 범위인 유저만 (MAL 표본과 같은 기준)
ACTIVE_SINCE = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp()   # 이때 이후 목록을 고친 적 있는 유저만
WEEKS = (dt.date(2023, 1, 2), dt.date(2026, 9, 28))   # 명단을 볼 주 (월요일 시작) 범위
KEEP_STATUS = {"CURRENT", "COMPLETED", "DROPPED", "PAUSED", "REPEATING"}   # PLANNING(보고 싶어요)은 안 남김
FIELDS = ["user_key", "media_id", "status", "score", "started", "completed", "updated_at"]

FIND = """
query ($media: Int, $page: Int, $from: FuzzyDateInt, $to: FuzzyDateInt) {
  Page(page: $page, perPage: 50) {
    pageInfo { lastPage }
    mediaList(mediaId: $media, type: ANIME, status: COMPLETED, completedAt_greater: $from, completedAt_lesser: $to) { userId }
  }
}
"""
LIST = """
query ($user: Int, $chunk: Int) {
  MediaListCollection(userId: $user, type: ANIME, chunk: $chunk, perChunk: 500, forceSingleCompletedList: true) {
    hasNextChunk
    lists { entries { mediaId status score(format: POINT_100) startedAt { year month day } completedAt { year month day } updatedAt } }
  }
}
"""


class Skip(Exception):
    """비공개 · 없는 유저 등 받을 수 없는 경우"""


def post(query, variables, stats, tries=5):
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "menu-recommender-research/0.1"})
    for attempt in range(tries):
        time.sleep(max(0.0, stats.get("last", 0.0) + PAUSE - time.time()))   # 앞 요청 시작에서 PAUSE 초가 지나야 보냄
        stats["last"] = time.time()
        stats["requests"] += 1
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)["data"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "60"))
                print(f"  요청 한도 → {wait}초 기다림", flush=True)
                time.sleep(wait + 1)
            elif e.code in (403, 404):                     # 비공개 · 없는 유저
                raise Skip(e.code)
            elif e.code == 400:                            # 요청이 틀림 → 조용히 넘기지 않고 멈춤
                raise RuntimeError(f"요청 오류 400: {e.read()[:300]!r}")
            else:
                print(f"  서버 오류 {e.code} → {60 * (attempt + 1)}초 뒤 다시", flush=True)
                time.sleep(60 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  연결 오류 {e} → {60 * (attempt + 1)}초 뒤 다시", flush=True)
            time.sleep(60 * (attempt + 1))
    raise RuntimeError(f"{tries}번 시도해도 실패 → 멈춤 (다시 돌리면 이어서)")


def frame():
    # 명단을 볼 작품: 2022년 이후 시작한 인기작 (성인물 제외) → (작품 번호, 인기, 첫 주). 인기 = 그 작품을 목록에 담은 사람 수
    pop = {}
    with open(ROOT / "anilist" / "links.jsonl", encoding="utf-8") as f:
        for line in f:
            for m in json.loads(line)["media"]:
                pop[m["id"]] = m.get("popularity") or 0
    works = []
    with open(ROOT / "anilist" / "media_recent.jsonl", encoding="utf-8") as f:
        for line in f:
            for m in json.loads(line)["media"]:
                year = (m.get("startDate") or {}).get("year") or 0
                if not m["isAdult"] and 2022 <= year <= WEEKS[1].year and pop.get(m["id"], 0) > 0:   # 아직 시작 안 한 작품은 뺌
                    start = max(dt.date(year, 1, 1), WEEKS[0])
                    works.append((m["id"], pop[m["id"]], start - dt.timedelta(days=start.weekday())))
    return works


def draw_slice(works, rng):
    # 작품은 인기에 비례해서, 주는 그 작품이 나온 해부터 2026-09 사이에서 무작위로
    media, _, first = rng.choices(works, weights=[w[1] for w in works])[0]
    n_weeks = (WEEKS[1] - first).days // 7 + 1
    week = first + dt.timedelta(weeks=rng.randrange(n_weeks))
    fuzzy = lambda d: d.year * 10000 + d.month * 100 + d.day
    return media, week, fuzzy(week - dt.timedelta(days=1)), fuzzy(week + dt.timedelta(days=7))


def date(d):
    if not d or not d.get("year"):
        return ""
    return f"{d['year']:04d}-{d.get('month') or 0:02d}-{d.get('day') or 0:02d}"


def fetch_user(uid, stats):
    rows, chunk = [], 1
    while True:
        data = post(LIST, {"user": uid, "chunk": chunk}, stats)["MediaListCollection"]
        if data is None:
            raise Skip("empty")
        for lst in data["lists"] or []:
            for e in lst["entries"] or []:
                if e["status"] in KEEP_STATUS:
                    rows.append([e["mediaId"], e["status"], e["score"] or 0, date(e["startedAt"]), date(e["completedAt"]),
                                 e["updatedAt"] or 0])
        if not data["hasNextChunk"]:
            return rows
        chunk += 1


def finish():
    if PROGRESS.exists():
        SLICES.write_text(json.dumps(json.loads(PROGRESS.read_text(encoding="utf-8"))["slices"]), encoding="utf-8")
        print(f"명단 기록을 남김 (유저 번호 없음): {SLICES}")
        PROGRESS.unlink()
        print(f"진짜 유저 번호가 든 진행 파일을 지움: {PROGRESS}")
    else:
        print("진행 파일 없음")


def main():
    if sys.argv[1:2] == ["--finish"]:
        return finish()
    target = min(int(sys.argv[1]) if len(sys.argv) > 1 else 100, MAX_USERS)
    OUT.mkdir(parents=True, exist_ok=True)
    prog = json.loads(PROGRESS.read_text(encoding="utf-8")) if PROGRESS.exists() else {
        "slices": [], "pool": [], "tried": [], "kept": 0}
    works = frame()
    tried = set(prog["tried"])
    stats = {"requests": 0}
    rng = random.Random()
    new_file = not ENTRIES.exists()
    t0 = time.time()
    with open(ENTRIES, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(FIELDS)
        while prog["kept"] < target:
            pool = [u for u in prog["pool"] if u not in tried]
            if len(pool) < 2 * (target - prog["kept"]):                      # 후보가 모자라면 명단 하나 더 보기
                media, week, a, b = draw_slice(works, rng)
                try:
                    first = post(FIND, {"media": media, "page": 1, "from": a, "to": b}, stats, tries=2)["Page"]
                    last = hi = first["pageInfo"]["lastPage"] or 1
                    page, p = first, 1
                    while hi > 1:                                            # 앞쪽만 보지 않게 무작위 쪽
                        p = rng.randint(1, hi)
                        if p == 1:
                            page = first
                            break
                        page = post(FIND, {"media": media, "page": p, "from": a, "to": b}, stats, tries=2)["Page"]
                        if page["mediaList"]:
                            break
                        hi = p - 1                                           # 빈 쪽이면 그 앞쪽 중에서 다시
                    found = {e["userId"] for e in page["mediaList"] or [] if e.get("userId")}
                    prog["pool"] = sorted(set(prog["pool"]) | found)
                    prog["slices"].append([media, week.isoformat(), last, p, len(found)])
                except (Skip, RuntimeError) as e:
                    print(f"  명단 {media} · {week} 건너뜀 ({e})", flush=True)
                PROGRESS.write_text(json.dumps(prog), encoding="utf-8")
                continue
            uid = rng.choice(pool)                                           # 후보 중 무작위로
            tried.add(uid)
            prog["tried"].append(uid)
            try:
                rows = fetch_user(uid, stats)
                n_done = sum(r[1] == "COMPLETED" for r in rows)
                active = max((r[5] for r in rows), default=0) >= ACTIVE_SINCE
                if COMPLETED_RANGE[0] <= n_done <= COMPLETED_RANGE[1] and active:
                    key = secrets.randbits(48)                               # 진짜 번호 대신 무작위 번호로 저장
                    w.writerows([key, *r] for r in rows)
                    f.flush()
                    prog["kept"] += 1
            except Skip:
                pass
            PROGRESS.write_text(json.dumps(prog), encoding="utf-8")         # 한 명마다 저장 (끊겨도 같은 유저를 두 번 안 받음)
            if len(prog["tried"]) % 10 == 0 or prog["kept"] >= target:
                print(f"  받은 유저 {prog['kept']:,}/{target:,} · 시도 {len(prog['tried']):,} · 명단 {len(prog['slices']):,}개"
                      f" · 이번 요청 {stats['requests']:,}번 · {(time.time() - t0) / 60:.0f}분", flush=True)
    PROGRESS.write_text(json.dumps(prog), encoding="utf-8")
    print(f"끝: 받은 유저 {prog['kept']:,}명 · 시도 {len(prog['tried']):,}명 · 본 명단 {len(prog['slices']):,}개 · "
          f"후보 {len(prog['pool']):,}명 · 이번 요청 {stats['requests']:,}번 · {(time.time() - t0) / 60:.0f}분 → {ENTRIES}")


if __name__ == "__main__":
    main()
