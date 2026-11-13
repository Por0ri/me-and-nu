"""content.run 과 같지만 글감 일부만 돌리고, 결과 폴더 이름에 묶음 번호를 붙인다 (여러 개를 동시에 돌리려고)

사용 (agents/rag 에서):
  uv run python -I <이 파일> --stamp 20261110-tone --part 0 --of 4
  uv run python -I <이 파일> --stamp 20261110-smoke --names dict-curation-like-w31
  # 줄 서기 (11/11): 여러 일꾼이 글감을 하나씩 잠그고 가져감. 글감 만들기가 끝났다는 표시 파일이 생길 때까지 새 글감을 기다림
  uv run python -I <이 파일> --root .cache/mass --stamp mass-1111c --part 0 --queue --wait-flag <표시 파일> \
      --not-kinds like overlap --done-stamps mass-1111b
"""
import argparse
import json
import os
import pathlib
import random
import sys
import time

from ani_rag.config import PACKAGE_ROOT
from ani_rag.content.gallery import write_gallery
from ani_rag.content.run import TRIAL, run_one

ap = argparse.ArgumentParser()
ap.add_argument("--stamp", required=True)
ap.add_argument("--part", type=int, default=0)
ap.add_argument("--of", type=int, default=1)
ap.add_argument("--names", nargs="*", default=[], help="이 글감만 (파일 이름에서 .json 뺀 것)")
ap.add_argument("--skip", nargs="*", default=["-birthday-"], help="이름에 이 글자가 있으면 뺌")
ap.add_argument("--budget", type=float, default=0.2)
ap.add_argument("--root", type=pathlib.Path, default=TRIAL, help="글감 폴더의 위 (대량 생산은 .cache/mass)")
ap.add_argument("--kinds", nargs="*", default=[], help="이 세부 유형만 (like overlap ...)")
ap.add_argument("--not-kinds", nargs="*", default=[], help="이 세부 유형은 뺌")
ap.add_argument("--queue", action="store_true", help="글감을 잠금 파일로 하나씩 나눠 가짐 (--part/--of 대신)")
ap.add_argument("--wait-flag", type=pathlib.Path, help="줄 서기: 이 파일이 생길 때까지 새 글감을 기다림")
ap.add_argument("--done-stamps", nargs="*", default=[], help="이 묶음들에서 끝낸 글도 건너뜀")
args = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")

runs = PACKAGE_ROOT / ".cache" / "content" / "runs"
OUTAGE = ("Connection error", "ConnectionError", "no credits remaining", "Error code: 429", "Error code: 500",
          "Error code: 502", "Error code: 503", "timed out")
out = runs / f"{args.stamp}-p{args.part}"
out.mkdir(parents=True, exist_ok=True)


def wanted(p: pathlib.Path) -> bool:
    kind = p.stem.split("-")[2]
    return ((p.stem in args.names if args.names else not any(s in p.stem for s in args.skip))
            and (not args.kinds or kind in args.kinds) and kind not in args.not_kinds)


def listing() -> list[pathlib.Path]:
    return sorted(p for p in (args.root / "manifests").glob("*.json") if wanted(p))


def done_ids() -> set[str]:
    """같은 이름(과 --done-stamps)의 묶음에서 오류 없이 끝낸 글"""
    ids: set[str] = set()
    for stamp in (args.stamp, *args.done_stamps):
        for f in runs.glob(f"{stamp}-p*/results.json"):
            try:
                ids |= {r["request_id"] for r in json.loads(f.read_text(encoding="utf-8")) if r.get("status") != "error"}
            except (json.JSONDecodeError, OSError):
                pass  # 다른 일꾼이 쓰는 중. 잠금 파일이 겹침을 막아 줌
    return ids


# 끊겼다가 다시 돌리면 이미 끝난 글은 건너뛴다 (오류로 끝난 글만 다시).
records = json.loads((out / "results.json").read_text(encoding="utf-8")) if (out / "results.json").exists() else []
records = [r for r in records if r.get("status") != "error"]
locks = runs / f"locks-{args.root.name}"
locks.mkdir(exist_ok=True)


def take() -> pathlib.Path | None:
    """줄 서기: 끝나지 않았고 아무도 잠그지 않은 글감 하나를 잠그고 돌려줌. 글감 만들기가 막 쓰는 파일은 건너뜀"""
    done = done_ids()
    for p in listing():
        fixture = args.root / "fixtures" / p.name
        if p.stem in done or not fixture.exists() or time.time() - fixture.stat().st_mtime < 20:
            continue
        try:
            fd = os.open(locks / f"{p.stem}.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        os.write(fd, f"{args.stamp}-p{args.part} {os.getpid()}".encode())
        os.close(fd)
        return p
    return None


def next_paths():
    if not args.queue:
        mine = listing()[args.part::args.of]
        done = done_ids()
        mine = [p for p in mine if p.stem not in done]
        print(f"묶음 {args.part}/{args.of}: 글감 {len(mine)}개 (이미 끝난 {len(done)}개 건너뜀) → {out}", flush=True)
        yield from mine
        return
    print(f"줄 서기 일꾼 {args.part} → {out}", flush=True)
    while True:
        p = take()
        if p is not None:
            yield p
            continue
        if args.wait_flag and not args.wait_flag.exists():
            time.sleep(60)  # 글감 만들기가 아직 도는 중 → 새 글감을 기다림
            continue
        return


spent = 0.0
for n, path in enumerate(next_paths(), 1):
    if spent >= args.budget:
        print(f"상한 도달 (${spent:.3f})", flush=True)
        if args.queue:
            (locks / f"{path.stem}.lock").unlink(missing_ok=True)  # 잠근 것만 풀고 끝
        break
    for attempt in range(6):
        try:
            rec = run_one(path, args.root / "fixtures", False, out)
        except Exception as exc:  # 그림 저장 같은 앞 단계 오류로 묶음 전체가 멈추지 않게 (11/11)
            rec = {"request_id": path.stem, "topic": path.stem, "content_type": "", "n_facts": 0, "status": "error",
                   "error": f"{type(exc).__name__}: {exc}"[:500], "seconds": 0.0, "cost": 0.0, "cards": {},
                   "package": {}}
        # 여러 일꾼이 같은 로컬 저장소에 동시에 쓰다 부딪히면 잠깐 쉬고 다시 (11/11). 대개 글쓰기 전 그림 저장
        # 단계라 비용은 안 듦. 일꾼마다 쉬는 시간을 조금씩 달리해 다시 부딪히지 않게
        err = rec.get("error") or ""
        if rec["status"] == "error" and "database is locked" in err and attempt < 5:
            wait = 30 * (attempt + 1) + random.uniform(0, 20)
            print(f"  {path.stem}: 저장소가 잠겨 {wait:.0f}초 뒤 다시", flush=True)
            time.sleep(wait)
            continue
        break
    # 연결 끊김 · 크레딧 소진은 글 문제가 아니라서, 다음 글로 넘어가 줄줄이 실패시키지 않고 같은 글을 붙잡고 기다린다
    # (11/11: 크레딧 소진 · 연결 끊김 때 일꾼들이 남은 글감을 몇 초 만에 실패로 넘김)
    outage = 0
    while rec["status"] == "error" and any(k in (rec.get("error") or "") for k in OUTAGE):
        outage += 1
        wait = min(600, 60 * outage) + random.uniform(0, 30)
        print(f"  {path.stem}: API 연결 · 크레딧 문제 → {wait:.0f}초 기다렸다 다시 ({outage}번째)", flush=True)
        time.sleep(wait)
        try:
            rec = run_one(path, args.root / "fixtures", False, out)
        except Exception as exc:
            rec = {"request_id": path.stem, "topic": path.stem, "content_type": "", "n_facts": 0, "status": "error",
                   "error": f"{type(exc).__name__}: {exc}"[:500], "seconds": 0.0, "cost": 0.0, "cards": {},
                   "package": {}}
    spent += rec["cost"]
    records.append(rec)
    print(f"  {n} {rec['request_id']}: {rec['status']} · 점수 {rec.get('score')} · "
          f"${rec['cost']:.4f} · {rec['seconds']:.0f}초 {rec.get('error') or ''}", flush=True)
    (out / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    write_gallery(records, out / "index.html")
print(f"끝: {len(records)}개 · ${spent:.3f}", flush=True)
