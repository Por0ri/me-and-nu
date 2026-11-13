"""원작 이야기 글감 → .cache/mass9 (11/12 서원: 표지는 원작 만화책 표지, 남은 것 전부)
서버에 이미 있는 글 번호는 뺀다. 표지 그림이 책 표지가 아니면 (못 받아서 포스터로 바뀌면) 만들지 않는다.
  uv run python -I <이 파일> [--only 글번호 ...]
"""
import argparse
import json
import sys

import psycopg

import ani_rag.build_manifest as bm
from ani_rag.config import PACKAGE_ROOT, load_settings

sys.stdout.reconfigure(encoding="utf-8")
ap = argparse.ArgumentParser()
ap.add_argument("--only", nargs="*", default=[])
args = ap.parse_args()
out = PACKAGE_ROOT / ".cache" / "mass9"
bm.MASS = True
for kind in bm.QUOTA:
    bm.QUOTA[kind] = 10 ** 6
kept = set(json.loads((PACKAGE_ROOT / ".cache" / "server_upload_20261112" / "kept.json").read_text(encoding="utf-8")))
with psycopg.connect(load_settings().dict_conninfo) as conn, conn.cursor() as cur:
    ctx = bm.Ctx(cur)
    decks = [d for d in bm.pick_origin(ctx) if d.kind == "origin" and d.request_id not in kept
             and (not args.only or d.request_id in args.only)]
    ctx.cur = None
    conn.rollback()
print(f"새 글감 {len(decks)}개 (원작 이야기)", flush=True)
(out / "manifests").mkdir(parents=True, exist_ok=True)
(out / "fixtures").mkdir(parents=True, exist_ok=True)
written = skipped = 0
for d in decks:
    if (out / "manifests" / f"{d.request_id}.json").exists():
        written += 1
        continue
    bm.collect_images(d)
    if not bm.resolve_plan(d):
        skipped += 1
        print(f"건너뜀 {d.request_id}: 장별 계획에 맞는 그림이 모자람 (그림 {len(d.images)}장)", flush=True)
        continue
    cover = next(i for i in d.images if i["id"] == d.resolved_plan[0]["image_id"])
    if "/manga/" not in cover["source_url"]:
        skipped += 1
        print(f"건너뜀 {d.request_id}: 원작 책 표지를 못 받음", flush=True)
        continue
    written += 1
    (out / "manifests" / f"{d.request_id}.json").write_text(
        json.dumps(d.manifest(), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "fixtures" / f"{d.request_id}.json").write_text(
        json.dumps(d.fixture(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {written} {d.request_id} {d.target_cards}장", flush=True)
(out / "manifests_done.flag").write_text("done", encoding="utf-8")
print(f"끝: 글감 {written}개 · 건너뜀 {skipped}개 → {out}", flush=True)
