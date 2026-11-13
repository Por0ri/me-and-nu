"""11/13 아침 서버에 넣을 묶음 (11/12 생산분): 글마다 bundle.json + cards/card_XX.png (하드 링크)
글마다 최종판:
  성우 mass-1112v · 입문 가이드 mass-1112g · 소재 모음 mass-1112u · 원작 이야기 mass-1112r (mass-1112r2 가 덮음)
  · 이달의 생일 mass-1112m · 주제가 mass-1112t3
고칠 것 두 가지 (그림은 그대로, 앱이 글자를 그림 위에 얹으니 PNG 다시 안 그림):
  1) 소재 모음: 표지 둘째 줄을 새 규칙(.cache/mass7/cover_suffix.json, '○○ 애니 모음' 등 5가지)으로. 꼭 남길 말도 같이 바꿈
  2) 주제가: 나무위키 노래 개요로 다시 쓴 작은 글씨(scratchpad/namu_song_rewrites.json, decision=use)를 끼우고
     출처(나무위키 문서, CC BY-NC-SA 2.0 KR) · 사실 한 줄을 글 정보에 붙임
고친 글은 결정 검사(run_quality_checks)를 전후로 돌려 새 지적이 생기면 고치기 전 판으로 되돌린다.
  uv run python -I <이 파일> <출력 폴더>
"""
import hashlib
import json
import os
import pathlib
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone

from ani_rag.content.models import ContentPackage
from ani_rag.content.quality import run_quality_checks

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
CACHE = pathlib.Path(__file__).resolve().parents[2] / ".cache"
RUNS = CACHE / "content" / "runs"
NAMU_SONGS = CACHE / "raw" / "namuwiki_songs"
STAMPS = ["mass-1112v", "mass-1112g", "mass-1112u", "mass-1112r", "mass-1112m", "mass-1112t3", "mass-1112r2",
          "mass-1112v2", "mass-1112t4", "mass-1112t5", "mass-1112t6", "mass-1113b", "mass-1113c", "mass-1113t7",
          "mass-1113L3", "mass-1113i", "mass-1113o"]  # L3 = 팬 추천 (표지 시안 C) · i = 주연 소개 · o = 원작 이야기 (그림 보강)
# 뒤가 앞을 덮음. mass-1112v2 = 서버 옛 주제가 10개의 새 양식판 (-v2) · t4 = 그림 돌려 쓴 주제가 (그림 1~2장 작품)
# t5 · t6 = 표지 큰 글씨가 쉼표로 끊겨 다시 만든 주제가 4개
# 1113b = 입문 가이드 그림 보강 시범 (암살교실만 여기서 씀: 전부 만들기에서 떨어짐) · 1113c = 입문 가이드 그림 보강 전부
# 1113t7 = 그림 돌려 쓴 주제가 중 AniList 시즌 표지로 그림이 늘어난 49개
out = pathlib.Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

final: dict[str, tuple[pathlib.Path, dict, str]] = {}
for stamp in STAMPS:
    for f in sorted(RUNS.glob(f"{stamp}-p*/results.json")):
        for r in json.loads(f.read_text(encoding="utf-8")):
            if r.get("status") == "ready_for_feed":
                final[r["request_id"]] = (f.parent, r, stamp)

suffix = json.loads((CACHE / "mass7" / "cover_suffix.json").read_text(encoding="utf-8"))
rewrites: dict[str, list[dict]] = {}
for rec in json.loads((HERE / "namu_song_rewrites.json").read_text(encoding="utf-8")):
    if rec.get("decision") == "use" and rec.get("after"):
        rewrites.setdefault(rec["request_id"], []).append(rec)


def issues(package: dict) -> set[str]:
    return {f"{i.code}@{i.page}" for i in run_quality_checks(ContentPackage.model_validate(package))}


def fix_overlap(package: dict) -> bool:
    want = suffix.get(package["request_id"])
    cover = min(package["cards"], key=lambda c: c["page"])
    lines = [t for t in cover["title_lines"] if t.strip()]
    if not want or not lines or lines[-1] == want:
        return False
    old = lines[-1]
    cover["title_lines"] = [*lines[:-1], want]
    cover["protected_phrases"] = [want if p == old else p for p in cover.get("protected_phrases") or []]
    return True


def fix_themes(package: dict) -> int:
    recs = rewrites.get(package["request_id"]) or []
    n = 0
    for rec in recs:
        card = next((c for c in package["cards"] if c["page"] == rec["page"]), None)
        if card is None or card["body_sentences"] != rec["before"]:
            continue  # 다시 쓸 때와 판이 다르면 안 건드림
        k = sum(1 for s in package["sources"] if s["id"].startswith("namuref-")) + 1
        sid, fid = f"namuref-{k}", f"n{k}"
        path = NAMU_SONGS / f"{rec['song']}.json"
        checked = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.exists() else None
        package["sources"].append({
            "id": sid, "title": f"나무위키 「{rec['namu_page']}」 (CC BY-NC-SA 2.0 KR)", "url": rec["namu_url"],
            "publisher": "나무위키", "is_official": False, "published_at": None, "checked_at": checked,
            "role": "reference", "region": "global", "allowed_asset_hosts": [], "discovered_via_source_id": None,
            "discovery_url": None, "discovery_title": None})
        package["facts"].append({
            "id": fid, "statement": rec["namu_fact"], "source_ids": [sid], "verified": True, "time_sensitive": False,
            "fact_type": "namu_song_note", "details": {"source_url": rec["namu_url"], "namu_page": rec["namu_page"]}})
        card["body_sentences"] = rec["after"]
        card["protected_phrases"] = rec.get("protected_phrases") or card.get("protected_phrases") or []
        card["fact_ids"] = [*card["fact_ids"], fid]
        # 상세 글 근거 목록에도 넣음 (카드 근거는 모두 상세 글 근거에 있어야 함 · 앱 출처 목록에 나무위키가 보이게)
        package["detail_fact_ids"] = [*(package.get("detail_fact_ids") or []), fid]
        package["detail_source_ids"] = [*(package.get("detail_source_ids") or []), sid]
        n += 1
    return n


# 입문 가이드 표지 바로잡기 (11/13 새벽 서원): 생성기가 해상도로 그림을 다시 섞어서 원작 만화책 표지가 표지로 간 글이 많음
# (원작 이야기 글 표지와 같아지고, 딱 봐서 애니가 떠오르지 않음). 카드끼리 그림 · 배치 · 렌더 PNG 를 통째로 맞바꾼다
# — 모든 배치의 글자 칸이 같은 자리(66% 지점)라 글자 위치는 안 변함
VISUAL_KEYS = ("image_id", "visual_mode", "image_explanation", "image_reuse_mode", "layout", "rendered_image_key",
               "rendered_width", "rendered_height")
COVER_PREF = {"dict-guide-basic-w17": "https://anilist.co/character/65643"}  # 암살교실 표지는 살생님 그림 (서원)
# 11/13 새벽 서원: 원작 책 표지가 캐릭터 얼굴이 크게 나와서 더 나은 것도 많음 → AI 가 '캐릭터가 크게 나온 쪽'을 고름
# (scratchpad/basic_cover_vision.py). manga = 원작 책 표지 그대로, swap = 맞바꿈. 원작 이야기 글과 표지가 같아지는 것은 swap
COVER_CHOICE = json.loads((HERE / "basic_cover_choice.json").read_text(encoding="utf-8"))
# 11/13 오전 서원: 서버 글과 표지가 겹쳐 보류된 입문 가이드 → 글 안 포스터 · 표지 그림과 맞바꾸거나 AniList 다른 표지로
# 표지 카드만 다시 그린 꾸러미 (scratchpad/basic_anilist_cover.py). 서원 OK 뒤 .approved.json 으로 만든 파일만 씀
# 11/13 낮 서원: 주연 소개 표지도 가운데 포스터판 → 주연 얼굴 바둑판 (scratchpad/intro_coverA_patch.py)
COVER_PATCH = {}
for _name in ("basic_cover_patch.approved.json", "intro_coverA_patch.approved.json"):
    if (HERE / _name).exists():
        COVER_PATCH.update(json.loads((HERE / _name).read_text(encoding="utf-8")))
PATCH_PNG = CACHE / "cover_patch_20261113"


def fix_cover(package: dict) -> bool:
    rid = package["request_id"]
    if "-basic-" not in rid:
        return False
    imgs = {i["id"]: i for i in package["images"]}
    cards = sorted(package["cards"], key=lambda c: c["page"])
    cover = cards[0]

    def src(card: dict) -> str:
        return imgs.get(card["image_id"], {}).get("source_url", "")

    def kind(card: dict) -> str:
        return imgs.get(card["image_id"], {}).get("asset_kind", "")

    want = COVER_PREF.get(rid)
    if want:
        if src(cover) == want:
            return False
        cand = [c for c in cards[1:] if src(c) == want]
    elif "/manga/" in src(cover):
        if (COVER_CHOICE.get(rid) or {}).get("choice") != "swap":
            return False  # AI 가 원작 책 표지 쪽이 캐릭터가 크다고 봄 (또는 판단 안 함) → 그대로
        order = {"catalog_poster": 0, "catalog_cover": 1, "character_art": 2}
        cand = sorted([c for c in cards[1:] if "/manga/" not in src(c) and kind(c) in order],
                      key=lambda c: (order[kind(c)], c["page"]))
    else:
        return False
    if not cand:
        return False
    other = cand[0]
    for k in VISUAL_KEYS:
        cover[k], other[k] = other.get(k), cover.get(k)
    return True


# 가수 이름 바로잡기 (즛토마요 · 활동명 · 잘못 붙은 사람) — scratchpad/performer_names.py
sys.path.insert(0, str(HERE))
from performer_names import fix_package  # noqa: E402


def fix_names(package: dict) -> dict:
    return fix_package(package)


ONLY = set(sys.argv[2:])  # 글 번호를 주면 그 글만 다시 묶음
if ONLY:
    final = {rid: v for rid, v in final.items() if rid in ONLY}
kinds, origin, problems, reverted = Counter(), Counter(), [], []
sizes = 0
for rid, (folder, r, stamp) in sorted(final.items()):
    package = json.loads(json.dumps(r["package"]))
    for card in package["cards"]:
        card["title_lines"] = [t for t in card.get("title_lines") or [] if t.strip()]
        card["body_sentences"] = [s for s in card.get("body_sentences") or [] if s.strip()]
    origin[stamp] += 1
    before = issues(package)
    patched = json.loads(json.dumps(package))
    changed = 0
    if "-overlap-" in rid and fix_overlap(patched):
        changed, label = 1, "소재 모음 둘째 줄"
    elif "-themes-" in rid:
        changed, label = fix_themes(patched), "주제가 나무위키 작은 글씨 (카드 수)"
    if changed:
        new = issues(patched) - before
        if new:
            reverted.append((rid, sorted(new)))
        else:
            package = patched
            origin[label] += changed
    swapped = json.loads(json.dumps(package))
    if fix_cover(swapped):
        new = issues(swapped) - before
        if new:
            reverted.append((rid, ["표지 그림 맞바꾸기"] + sorted(new)))
        else:
            package = swapped
            origin["입문 가이드 표지 그림 맞바꿈"] += 1
    named = fix_names(json.loads(json.dumps(package)))
    if named != package:
        new = issues(named) - before
        if new:
            reverted.append((rid, ["이름 고치기"] + sorted(new)))
        else:
            package = named
            origin["가수 이름 고침"] += 1
    if rid in COVER_PATCH:
        repl = json.loads(json.dumps(COVER_PATCH[rid]))
        new = issues(repl) - before
        if new:
            reverted.append((rid, ["보류 표지 바꾸기"] + sorted(new)))
        else:
            package = repl
            origin["보류 입문 가이드 표지 바꿈"] += 1
    root = out / rid
    if root.exists():
        shutil.rmtree(root)
    (root / "cards").mkdir(parents=True)
    assets = []
    try:
        assert package.get("request_id") == rid and not package.get("isTestData"), "글 번호가 다름"
        # 렌더 PNG 는 지문으로 찾음 (표지 그림을 맞바꾼 글은 장 번호와 파일 이름이 다름)
        pngs = {hashlib.sha256(p.read_bytes()).hexdigest(): p for p in (folder / rid).glob("card_*.png")}
        if rid in COVER_PATCH:  # AniList 새 표지로 다시 그린 표지 카드
            pngs.update({hashlib.sha256(p.read_bytes()).hexdigest(): p for p in (PATCH_PNG / rid).glob("card_*.png")})
        for card in package["cards"]:
            page, key = card["page"], card.get("rendered_image_key")
            src = pngs.get(key)
            assert src is not None, f"{page}장 PNG 없음 (지문으로 못 찾음)"
            dst = root / "cards" / f"card_{page:02d}.png"
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
            assets.append({"page": page, "path": f"cards/card_{page:02d}.png", "sha256": key})
            sizes += src.stat().st_size
    except AssertionError as e:
        problems.append((rid, str(e)))
        shutil.rmtree(root)
        continue
    evidence = {"status": "ready", "quality_score": r.get("score"), "quality_report": {"issues": r.get("issues")},
                "batchPurpose": "checking_test_preview", "operatorReviewCreated": False,
                "sourceFile": f"{folder.name}/results.json", "sourceSha256": None}
    doc = {"schemaVersion": 1, "producer": "ani_menu_agent", "topicCode": "anime",
           "items": [{"package": package, "assets": assets, "reviewEvidence": evidence}]}
    (root / "bundle.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    kinds[rid.split("-")[2]] += 1
print(f"묶음 {sum(kinds.values())}개 · 카드 그림 {sizes / 2**30:.2f}GB (하드 링크) · 문제 {len(problems)}개")
print("어디서 왔나 · 고친 것:", dict(origin))
print("유형:", dict(kinds))
print(f"고쳤다가 새 지적이 생겨 되돌린 글 {len(reverted)}개")
for rid, why in reverted[:10]:
    print("  되돌림:", rid, why)
for rid, why in problems[:20]:
    print("  문제:", rid, why)
