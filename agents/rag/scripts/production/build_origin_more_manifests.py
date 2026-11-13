"""그림이 2장(책 표지 + 포스터)뿐이라 빠진 원작 이야기 36개 → .cache/mass24 (11/13 낮 서원: '안쪽에 주연 그림 보태기')
- 장 구성은 글감 만들기 pick_origin 그대로: 1장 원작 책 표지 · 2장 포스터 · 3장 장면 · 4장 작품 그림
- 3 · 4장에 쓸 그림을 보탠다: Kitsu 번호가 없는 시즌 · 극장판의 AniList 표지 (있으면, 최대 3장) + 주연 캐릭터 그림 (최대 3명)
  (보탠 그림은 그 작품 그림으로 묶여서 '장면 → 작품 그림' 순서로 빈 장을 채움. 표지는 책 표지 그대로)
- 대상: scratchpad/mass9_build.log 에서 '그림이 모자람'으로 건너뛴 글
  uv run python -I <이 파일> [--only 글번호 ...]
"""
import argparse
import json
import pathlib
import re
import sys

import psycopg

import ani_rag.build_manifest as bm
from ani_rag.config import PACKAGE_ROOT, load_settings

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
ap = argparse.ArgumentParser()
ap.add_argument("--only", nargs="*", default=[])
args = ap.parse_args()
out = PACKAGE_ROOT / ".cache" / "mass24"
bm.MASS = True
for kind in bm.QUOTA:
    bm.QUOTA[kind] = 10 ** 6
targets = {f"dict-guide-origin-w{m.group(1)}" for line in (HERE / "mass9_build.log").read_text(encoding="utf-8").splitlines()
           if "그림이 모자람" in line and (m := re.search(r"origin-w(\d+)", line))}
if args.only:
    targets &= set(args.only)
with psycopg.connect(load_settings().dict_conninfo) as conn, conn.cursor() as cur:
    ctx = bm.Ctx(cur)
    mains = bm.main_casts(ctx)
    decks = [d for d in bm.pick_origin(ctx) if d.kind == "origin" and d.request_id in targets]
    ctx.cur = None
    conn.rollback()
print(f"원작 이야기 다시 만들 글감 {len(decks)}개 (대상 {len(targets)}개)", flush=True)
(out / "manifests").mkdir(parents=True, exist_ok=True)
(out / "fixtures").mkdir(parents=True, exist_ok=True)
written = skipped = 0
for d in decks:
    if (out / "manifests" / f"{d.request_id}.json").exists():
        written += 1
        continue
    w = d.works[0]
    extra = [e for e in ctx.work_entries.get(w, []) if e in ctx.anilist and e not in ctx.kitsu][:3]
    d.plan += [("anilist_cover", e, w) for e in extra]
    chars = [c for c in sorted(mains.get(w, {}), key=lambda c: mains[w][c].fact_id) if c in ctx.anilist][:3]
    d.plan += [("character", ctx.anilist[c], d.cname(c), w) for c in chars]
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
    kinds = [next(i["asset_kind"] for i in d.images if i["id"] == p["image_id"]) for p in d.resolved_plan]
    print(f"  {written} {d.request_id} {d.topic} · 장별 그림 {kinds}", flush=True)
(out / "manifests_done.flag").write_text("done", encoding="utf-8")
print(f"끝: 글감 {written}개 · 건너뜀 {skipped}개 → {out}", flush=True)
