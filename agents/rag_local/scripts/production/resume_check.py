"""팬 추천 이어 만들기 전 점검 (읽기만)
1) 글감(manifests) · 받침 파일(fixtures) 짝 · 깨진 JSON
2) 팬 추천 그림 캐시에서 0바이트 · 끝까지 안 열리는 그림 (디스크가 꽉 찼던 01:30~01:55 사이 파일만 꼼꼼히)
3) 잠금 파일 중 '끝나지 않은 글'(오류 · 쓰다 멈춘 글) → 풀어야 다시 만들어짐
  uv run python -I <이 파일>
"""
import datetime
import json
import pathlib
import sys

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
RAG = pathlib.Path(__file__).resolve().parents[2]
ROOT = RAG / ".cache" / "mass22"
RUNS = RAG / ".cache" / "content" / "runs"
IMAGES = RAG / ".cache" / "trial" / "images"
STAMP = "mass-1113L3"

man = {p.stem: p for p in (ROOT / "manifests").glob("*.json")}
fix = {p.stem: p for p in (ROOT / "fixtures").glob("*.json")}
bad_json = []
for p in [*man.values(), *fix.values()]:
    try:
        json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        bad_json.append(f"{p.parent.name}/{p.name} ({p.stat().st_size}B, {type(exc).__name__})")
print(f"1) 글감 {len(man)}개 · 받침 {len(fix)}개 · 짝 없는 것 {sorted(set(man) ^ set(fix))} · 깨진 JSON {bad_json}")
print(f"   끝 표시 파일: {(ROOT / 'manifests_done.flag').exists()}")

lo = datetime.datetime(2026, 11, 13, 1, 30).timestamp()
hi = datetime.datetime(2026, 11, 13, 1, 55).timestamp()
zero, broken, checked = [], [], 0
for d in IMAGES.glob("dict-curation-like-*"):
    for f in d.iterdir():
        if not f.is_file() or f.suffix == ".broken":
            continue
        st = f.stat()
        if st.st_size == 0:
            zero.append(f"{d.name}/{f.name}")
            continue
        if lo <= st.st_mtime <= hi:
            checked += 1
            try:
                with Image.open(f) as im:
                    im.load()
            except Exception as exc:  # noqa: BLE001 - 끝까지 못 여는 그림은 다 기록
                broken.append(f"{d.name}/{f.name} ({st.st_size}B, {type(exc).__name__})")
print(f"2) 0바이트 그림 {zero} · 01:30~01:55 그림 {checked}개 열어 봄 → 깨진 것 {broken}")

done = set()
for f in RUNS.glob(f"{STAMP}-p*/results.json"):
    done |= {r["request_id"] for r in json.loads(f.read_text(encoding="utf-8")) if r.get("status") != "error"}
locks = sorted((RUNS / "locks-mass22").glob("*.lock"))
stale = [p.stem for p in locks if p.stem not in done]
print(f"3) 끝난 글 {len(done)}개 · 잠금 {len(locks)}개 · 끝나지 않았는데 잠긴 글 {len(stale)}개")
print(f"   남은 글감 (잠금 풀면 바로 할 수 있는 것): {len([s for s in man if s not in done])}개")
(HERE / "resume_stale_locks.json").write_text(json.dumps(stale, indent=1), encoding="utf-8")
