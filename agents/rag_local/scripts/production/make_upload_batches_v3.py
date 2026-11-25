"""11/13 서버에 넣을 업로드 묶음: 표지 겹침 거르기 → 묶음(최대 40개 · 그림 180MB) → 작은 묶음 part_NN.json (그림 35MB 이하)
- 서버에 이미 있는 1,364개(.cache/server_upload_20261112/kept.json)와 표지 그림이 같으면 보류
- 새 글끼리 표지가 같으면 한 글만 (입문 가이드 → 원작 이야기 → 주제가 → 소재 모음 → 성우 → 이달의 생일 순으로 남김)
- 카드는 <글 번호>/card_XX.png 하드 링크. part_NN.json 은 batch 폴더 안 (서버 apply_parts.sh 가 하나씩 컨테이너 /tmp 에 넣음)
  python -I <이 파일> <묶음 폴더> <출력 폴더>
"""
import json
import os
import pathlib
import shutil
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")
CACHE = pathlib.Path(__file__).resolve().parents[2] / ".cache"
src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
PRIORITY = ["basic", "origin", "themes", "overlap", "va", "bdaymonth"]
MAX_ITEMS, MAX_BYTES, PART_BYTES = 40, 180 * 2**20, 35 * 2**20


def cover_key(package: dict) -> str:
    cover = min(package["cards"], key=lambda c: c["page"])
    image = next((i for i in package["images"] if i["id"] == cover["image_id"]), {})
    return image.get("fingerprint") or image.get("source_url") or cover["image_id"]


server_keys: dict[str, str] = {}
for rid in json.loads((CACHE / "server_upload_20261112" / "kept.json").read_text(encoding="utf-8")):
    doc = json.loads((CACHE / "server_bundles_20261112" / rid / "bundle.json").read_text(encoding="utf-8"))
    server_keys[cover_key(doc["items"][0]["package"])] = rid

posts = {}
for f in sorted(src.glob("*/bundle.json")):
    doc = json.loads(f.read_text(encoding="utf-8"))
    item = doc["items"][0]
    posts[item["package"]["request_id"]] = (f.parent, doc, item, cover_key(item["package"]))


def rank(rid: str) -> tuple:
    kind = rid.split("-")[2]
    return (PRIORITY.index(kind) if kind in PRIORITY else len(PRIORITY), rid)


held_server, held_new, keep = {}, {}, []
groups = defaultdict(list)
for rid, (_, _, _, key) in posts.items():
    if key in server_keys and not (rid.endswith("-v2") and server_keys[key] == rid[:-3]):
        held_server[rid] = server_keys[key]  # (고친 판 -v2 는 옛 글을 숨기니 같은 표지여도 넣음)
    else:
        groups[key].append(rid)
for key, rids in groups.items():
    rids = sorted(rids, key=rank)
    keep.append(rids[0])
    for r in rids[1:]:
        held_new[r] = rids[0]

if out.exists():
    shutil.rmtree(out)
out.mkdir(parents=True)
batches, current, size = [], [], 0
for rid in sorted(keep):
    n = sum(p.stat().st_size for p in (posts[rid][0] / "cards").glob("*.png"))
    if current and (len(current) >= MAX_ITEMS or size + n > MAX_BYTES):
        batches.append(current)
        current, size = [], 0
    current.append(rid)
    size += n
if current:
    batches.append(current)
parts_total, biggest = 0, 0
for i, rids in enumerate(batches, 1):
    root = out / f"batch_{i:03d}"
    root.mkdir()
    items = []
    for rid in rids:
        folder, doc, item, _ = posts[rid]
        (root / rid).mkdir()
        assets = []
        for asset in item["assets"]:
            name = pathlib.Path(asset["path"]).name
            dst = root / rid / name
            try:
                os.link(folder / asset["path"], dst)
            except OSError:
                shutil.copy2(folder / asset["path"], dst)
            assets.append({**asset, "path": f"{rid}/{name}"})
        items.append({**item, "assets": assets})
    head = {k: v for k, v in posts[rids[0]][1].items() if k != "items"}
    (root / "bundle.json").write_text(json.dumps({**head, "items": items}, ensure_ascii=False), encoding="utf-8")
    chunk, csize, k = [], 0, 0
    for it in items:
        n = sum((root / a["path"]).stat().st_size for a in it["assets"])
        if chunk and csize + n > PART_BYTES:
            k += 1
            (root / f"part_{k:02d}.json").write_text(json.dumps({**head, "items": chunk}, ensure_ascii=False), encoding="utf-8")
            parts_total, biggest = parts_total + 1, max(biggest, csize)
            chunk, csize = [], 0
        chunk.append(it)
        csize += n
    if chunk:
        k += 1
        (root / f"part_{k:02d}.json").write_text(json.dumps({**head, "items": chunk}, ensure_ascii=False), encoding="utf-8")
        parts_total, biggest = parts_total + 1, max(biggest, csize)
hide = sorted(r[:-3] for r in keep if r.endswith("-v2"))  # 고친 판을 넣은 뒤 서버에서 숨길 옛 글
(out / "hide_on_server.json").write_text(json.dumps(hide, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"고친 판(-v2) {len(hide)}개 → 넣은 뒤 숨길 옛 글:", hide)
(out / "kept.json").write_text(json.dumps(sorted(keep), ensure_ascii=False, indent=1), encoding="utf-8")
(out / "held_cover_server.json").write_text(json.dumps(held_server, ensure_ascii=False, indent=1), encoding="utf-8")
(out / "held_cover_new.json").write_text(json.dumps(held_new, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"새 글 {len(posts)}개 · 서버 글과 표지 겹침 {len(held_server)}개 · 새 글끼리 겹쳐 뺀 것 {len(held_new)}개")
print(f"서버에 넣을 글 {len(keep)}개 → 묶음 {len(batches)}개 · 작은 묶음 {parts_total}개 (가장 큰 것 그림 {biggest / 2**20:.1f}MB)")
print("넣을 유형:", dict(Counter(r.split('-')[2] for r in keep).most_common()))
print("서버와 겹친 유형:", dict(Counter(r.split('-')[2] for r in held_server).most_common()))
print("새 글끼리 겹친 유형:", dict(Counter(r.split('-')[2] for r in held_new).most_common()))
