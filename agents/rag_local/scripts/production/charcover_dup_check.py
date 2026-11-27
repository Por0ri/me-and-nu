"""입문 가이드 표지를 포스터로 바꾼 글 점검 (읽기만, 11/13 낮 서원 질문: '왜 원래 캐릭터였어? 서버 글과 겹쳐서였던 거 아냐?')
1) 바꿔 넣은 포스터에 생성기가 받은 '전에 쓴 그림' 표시(used_in_previous_content)가 있었는지 · 이전 글 그림 지문 목록에 있었는지
2) 새 표지가 같은 작품이 나오는 서버 · 오늘 글들의 포스터 표지와 그림이 비슷한지 (dHash 거리 16 이하 = 같은 그림)
   파일 지문이 달라도 같은 포스터의 다른 크기일 수 있어서 그림 모양으로 비교
  uv run python -I <이 파일> [OK 파일 대신 볼 꾸러미 파일]
"""
import hashlib
import io
import json
import pathlib
import sys
import urllib.parse
from collections import Counter

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
C = pathlib.Path(__file__).resolve().parents[2] / ".cache"
OLD, NEW = C / "server_bundles_20261112", C / "server_bundles_20261113"
SAME = 16
patch = json.loads((HERE / (sys.argv[1] if len(sys.argv) > 1 else "basic_charcover_patch.json")).read_text(encoding="utf-8"))


def pkg(folder, rid):
    return json.loads((folder / rid / "bundle.json").read_text(encoding="utf-8"))["items"][0]["package"]


def cover(p):
    card = min(p["cards"], key=lambda c: c["page"])
    return card, next((i for i in p["images"] if i["id"] == card["image_id"]), {})


def local(rid, url):
    name = hashlib.sha1(url.encode()).hexdigest()[:16]
    suffix = pathlib.PurePosixPath(urllib.parse.urlsplit(url).path).suffix.lower()[:5]
    for path in (C / "trial" / "images" / rid / (name + suffix), C / "cover_patch_20261113" / "src" / (name + suffix)):
        if path.exists() and path.stat().st_size > 0:
            return path.read_bytes()
    return None


def dhash(data):
    out = []
    with Image.open(io.BytesIO(data)) as im:
        g = im.convert("L")
    for crop in (None, 0.1):
        x = g if crop is None else g.crop((int(g.width * crop), int(g.height * crop),
                                           int(g.width * (1 - crop)), int(g.height * (1 - crop))))
        px = list(x.resize((9, 8), Image.LANCZOS).getdata())
        bits = 0
        for r in range(8):
            for c in range(8):
                bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
        out.append(bits)
    return out


def dist(a, b):
    return min(bin(x ^ y).count("1") for x in a for y in b)


# 같은 작품이 나오는 글들의 포스터 표지 (서버 + 오늘, 이번에 바꾼 글은 바꾼 뒤 표지로)
refs: dict[int, list[tuple[str, list[int]]]] = {}
for folder, kept_file in ((OLD, C / "server_upload_20261112" / "kept.json"), (NEW, C / "server_upload_20261113" / "kept.json")):
    for r in json.loads(kept_file.read_text(encoding="utf-8")):
        p = patch.get(r) or pkg(folder, r)
        _, im = cover(p)
        if im.get("asset_kind") not in ("catalog_poster", "catalog_cover") or str(im.get("id")).startswith(("collage-", "player-")):
            continue
        data = local(r, im.get("url") or "")
        if data is None:
            continue
        h = dhash(data)
        for m in im.get("menu_work_ids") or []:
            refs.setdefault(m, []).append((r, h))

flag, prev, prior, close = Counter(), 0, 0, []
for rid, p in sorted(patch.items()):
    orig = pkg(NEW, rid)
    _, new_im = cover(p)
    old_card, old_im = cover(orig)
    # 1) 바꿔 넣은 그림이 원래 글에서 '전에 쓴 그림'으로 표시돼 있었나
    same = next((i for i in orig["images"] if i["id"] == new_im["id"]), None)
    if same is not None and same.get("used_in_previous_content"):
        prev += 1
    if new_im.get("fingerprint") in set(orig.get("prior_image_fingerprints") or []):
        prior += 1
    # 2) 같은 작품 글들의 포스터 표지와 비슷한가
    data = local(rid, new_im.get("url") or "")
    if data is None:
        flag["새 표지 그림 파일 없음"] += 1
        continue
    h = dhash(data)
    menu = int(rid.rsplit("-w", 1)[1])
    others = [(r, dist(h, rh)) for r, rh in refs.get(menu, []) if r != rid]
    near = [(r, d) for r, d in others if d <= SAME]
    if near:
        flag["같은 작품 글 표지와 비슷함"] += 1
        close.append((rid, near[:3]))
    else:
        flag["비슷한 표지 없음"] += 1
print(f"바꾼 글 {len(patch)}개")
print(f"1) 바꿔 넣은 그림에 '전에 쓴 그림' 표시가 있던 글: {prev}개 · 이전 글 그림 지문 목록에 있던 글: {prior}개")
print("2)", dict(flag))
for rid, near in close[:20]:
    print("  ", rid, "≈", near)
