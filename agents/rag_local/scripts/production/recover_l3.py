"""11/13 새벽: 디스크가 꽉 차서 팬 추천(mass-1113L3) 묶음 8개의 results.json 이 0바이트로 깨짐.
일꾼마다 쓰던 로컬 저장소(.cache/content/agent-mass22-w{k}.sqlite3)에서 기록을 되살린다 (API 비용 없음).
- 멀쩡한 p2 로 확인함: 저장소 feed_queue 의 글 35개가 results.json 의 글과 똑같음
- 상태 · 점수 · 비용 · 걸린 시간은 생산 기록(scratchpad/mass22_gen_p{k}.log)에서 가져옴
- 카드 PNG 가 폴더에 없으면 저장소에서 꺼내고, 그래도 없으면 그 글은 '오류'로 둠 (묶음에서 빠지고 나중에 다시 만듦)
- 깨진 0바이트 파일은 지우지 않고 results.broken.json 으로 이름만 바꿔 둠
  uv run python -I <이 파일>            → 확인만 (아무것도 안 씀)
  uv run python -I <이 파일> --write    → results.json 다시 씀
"""
import hashlib
import json
import pathlib
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
CONTENT = pathlib.Path(__file__).resolve().parents[2] / ".cache" / "content"
RUNS = CONTENT / "runs"
WRITE = "--write" in sys.argv
LINE = re.compile(r"^\s+\d+ (\S+): (\S+) · 점수 (\S+) · \$([\d.]+) · (\d+)초 ?(.*)$")

total = {"ready_for_feed": 0, "other": 0, "error": 0, "png_from_db": 0, "png_missing": 0}
for k in range(10):
    out = RUNS / f"mass-1113L3-p{k}"
    res = out / "results.json"
    try:
        json.loads(res.read_text(encoding="utf-8"))
        print(f"p{k}: results.json 멀쩡함 → 건드리지 않음")
        continue
    except (json.JSONDecodeError, OSError):
        pass
    con = sqlite3.connect(f"file:{CONTENT / f'agent-mass22-w{k}.sqlite3'}?mode=ro", uri=True)
    drafts = {r[0]: r for r in con.execute(
        "select request_id, topic, content_type, status, package_json, quality_score, quality_report_json "
        "from content_drafts")}
    feed = {r[0]: r[1] for r in con.execute("select request_id, package_json from feed_queue")}
    logged = []
    for line in (HERE / f"mass22_gen_p{k}.log").read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m:
            logged.append(m.groups())
    records = []
    seen = set()
    for rid, status, score, cost, secs, err in logged:
        seen.add(rid)
        rec = {"request_id": rid, "topic": rid, "content_type": "", "n_facts": 0, "status": status,
               "seconds": float(secs), "cost": float(cost), "cards": {}, "package": {}, "recovered": True}
        if status == "error":
            rec["error"] = err.strip()[:500]
            records.append(rec)
            total["error"] += 1
            continue
        d = drafts.get(rid)
        assert d is not None, f"p{k} {rid}: 저장소에 글이 없음"
        assert d[3] == status, f"p{k} {rid}: 상태가 다름 (기록 {status} / 저장소 {d[3]})"
        package = json.loads(feed.get(rid) or d[4])
        report = json.loads(d[6]) if d[6] else {}
        rec.update(topic=d[1], content_type=d[2] or "", n_facts=len(package.get("facts") or []),
                   score=int(score) if score != "None" else d[5],
                   issues=report.get("issues") if isinstance(report.get("issues"), list) else [],
                   package=package)
        if status == "ready_for_feed":
            folder = out / rid
            pngs = {hashlib.sha256(p.read_bytes()).hexdigest(): p for p in folder.glob("card_*.png")}
            ok = True
            for card in package["cards"]:
                key = card.get("rendered_image_key")
                if key in pngs:
                    rec["cards"][card["page"]] = f"{rid}/{pngs[key].name}"
                    continue
                row = con.execute("select binary_data from image_blobs where sha256 = ?", (key,)).fetchone()
                if row and hashlib.sha256(row[0]).hexdigest() == key:
                    total["png_from_db"] += 1
                    if WRITE:
                        folder.mkdir(exist_ok=True)
                        (folder / f"card_{card['page']:02d}.png").write_bytes(row[0])
                    rec["cards"][card["page"]] = f"{rid}/card_{card['page']:02d}.png"
                else:
                    ok = False
            if not ok:
                total["png_missing"] += 1
                rec.update(status="error", error="카드 PNG 없음 (11/13 디스크 부족) → 다시 만들기", package={})
                total["error"] += 1
                records.append(rec)
                continue
            total["ready_for_feed"] += 1
        else:
            total["other"] += 1
        records.append(rec)
    extra = sorted(set(drafts) - seen)
    if extra:
        print(f"  p{k}: 기록에 없는데 저장소에만 있는 글 {extra} → 건너뜀 (다음에 다시 만들어짐)")
    from collections import Counter
    print(f"p{k}: {len(records)}개 되살림 {dict(Counter(r['status'] for r in records))}")
    if WRITE:
        if res.exists():
            res.rename(out / "results.broken.json")
        res.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    con.close()
print("합계:", total, "(확인만)" if not WRITE else "(씀)")
