"""그림이 모자라 빠진 입문 가이드 261개 → .cache/mass19 (11/12 밤 서원: 3개 시범 먼저)
그림을 더 모은다: Kitsu 번호가 없는 시즌 · 극장판의 AniList 표지 (최대 4장) + 원작 만화 · 소설 표지 (AniList, 근거 사실과 함께).
그림 5장 이상이면 5장짜리, 4장이면 4장짜리, 3장 이하면 뺀다.
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
out = PACKAGE_ROOT / ".cache" / "mass19"  # 11/13 새벽: 본 성우 고르기 고친 뒤 전부 (mass17 은 시범)
skipped_ids = {line.split()[0] for line in (HERE / "basic_skipped.txt").read_text(encoding="utf-8").splitlines() if line.strip()}
bm.MASS = True
for kind in bm.QUOTA:
    bm.QUOTA[kind] = 10 ** 6
bm.QUOTA["birthday"] = 0
with psycopg.connect(load_settings().dict_conninfo) as conn, conn.cursor() as cur:
    ctx = bm.Ctx(cur)
    decks = [d for d in bm.pick_guide(ctx) if d.kind == "basic" and d.request_id in skipped_ids
             and (not args.only or d.request_id in args.only)]
    origins, rows = bm.origin_of(ctx)
    cur.execute("select external_id, entity_id from dict.external_id where source_id = 'anilist' and id_kind = 'staff'")
    staff = dict(cur.fetchall())
    books = 0
    for d in decks:
        w = d.works[0]
        title = ctx.title[w]
        # Kitsu 포스터가 없는 시즌 · 극장판은 AniList 표지로
        extra = [e for e in ctx.work_entries[w] if e in ctx.anilist and e not in ctx.kitsu][:4]
        d.plan += [("anilist_cover", e, w) for e in extra]
        row = origins.get(w)
        manga = re.search(r"anilist\.co/manga/(\d+)", (row.url or "") if row else "")
        if manga:
            entry = ctx.work_entries[w][0]
            kind = bm.SOURCE_KO.get((rows.get((entry, "source_material")) or [None])[0].value
                                    if rows.get((entry, "source_material")) else "", "원작")
            a = bm.story_author(row.value)
            pid = staff.get(str(a["anilist_staff"])) if a else None
            who = f"{d.pname(pid)}의 " if pid and ctx.ko.get(pid) else ""
            d.fact(f"〈{title}〉의 원작은 {who}{bm.ida(kind)}.", row, "source_work")
            d.plan.append(("manga_cover", manga.group(1), w, f"〈{title}〉 원작"))
            books += 1
    ctx.cur = None
    conn.rollback()
print(f"글감 후보 {len(decks)}개 · 원작 책 표지 넣을 것 {books}개", flush=True)
(out / "manifests").mkdir(parents=True, exist_ok=True)
(out / "fixtures").mkdir(parents=True, exist_ok=True)
written = skipped = 0
for d in decks:
    if (out / "manifests" / f"{d.request_id}.json").exists():
        written += 1
        continue
    bm.collect_images(d)
    if len(d.images) < 4 or not d.facts:
        skipped += 1
        print(f"건너뜀 {d.request_id}: 그림 {len(d.images)}장", flush=True)
        continue
    d.target_cards = 5 if len(d.images) >= 5 else 4
    if d.target_cards == 4:
        d.angle = d.angle.replace("5장 주연 캐릭터와 성우.", "그림이 4장이라 만든 사람과 주연 · 성우를 한 장에 줄여 4장으로.")
    written += 1
    (out / "manifests" / f"{d.request_id}.json").write_text(
        json.dumps(d.manifest(), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "fixtures" / f"{d.request_id}.json").write_text(
        json.dumps(d.fixture(), ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = sorted({i["asset_kind"] + ("(원작 책)" if "/manga/" in i["source_url"] else "") for i in d.images})
    print(f"  {written} {d.request_id} 〈{d.ctx.title[d.works[0]]}〉 그림 {len(d.images)}장 → {d.target_cards}장 · {kinds}",
          flush=True)
(out / (("trial_" if args.only else "") + "manifests_done.flag")).write_text("done", encoding="utf-8")
print(f"끝: 글감 {written}개 · 건너뜀 {skipped}개 → {out}", flush=True)
