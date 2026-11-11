"""しょぼいカレンダー → 정보사전: 일본 TV 애니의 회차 제목 · 주제가 일본어 원제 · 스태프 · 캐스트 · 공식 사이트

  uv run python -m ani_rag.load_syoboi        (agents/rag 폴더에서)

- 우리 시즌 ↔ 쇼보이 번호(TID)는 arm 대응표(MIT)와 위키데이터 번호로 잇는다
- 요청: TID 100개씩 db.php TitleLookup. 병렬 금지, 응답 받고 1.5초 뒤 다음 요청 (쇼보이 bot 규칙은 최소 1초)
- 받은 원본은 .cache/raw/syoboi/<날짜>/ 에 저장. 같은 날 다시 돌리면 다시 요청하지 않음
- TID 하나가 우리 시즌 여러 개(분할 2쿨 등)에 붙으면 사실은 위 작품에 붙인다
- 이 스크립트가 맡는 칸 (출처 syoboi): start_date · start_year · end_date · broadcaster · official_site ·
  syoboi_link · episode_titles · episodes · jp_staff · director · theme_song_ja · jp_cast
- 인물은 일본어 이름이 우리 인물의 원어 이름과 똑같고 한 명으로만 맞을 때만 연결한다
- 쇼보이는 이용자가 편집하는 자료라, 다른 출처와 맞춰 보는 재료로 쓴다
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

import psycopg

from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store, norm, unique_pairs

SYOBOI = "syoboi"
ARM_URL = "https://raw.githubusercontent.com/kawaiioverflow/arm/master/arm.json"
DB_URL = "https://cal.syoboi.jp/db.php"
PAGE_URL = "https://cal.syoboi.jp/tid/{}"
UA = "menu-dict/0.1 (school project)"
PAUSE = 1.5
BATCH = 100
ITEM_FIELDS = ("TID,Title,TitleYomi,TitleEN,Cat,FirstCh,FirstYear,FirstMonth,FirstEndYear,FirstEndMonth,"
               "Comment,SubTitles")
FIELDS = ("start_date", "start_year", "end_date", "broadcaster", "official_site", "syoboi_link", "episode_titles",
          "episodes", "jp_staff", "director", "theme_song_ja", "jp_cast")
THEME = re.compile(r"(オープニング|エンディング|挿入歌|主題歌)(?:テーマ)?\s*(\d*)\s*「(.+?)」")
THEME_TYPE = {"オープニング": "OP", "エンディング": "ED", "挿入歌": "IN", "主題歌": "TH"}
THEME_ROLE = {"作詞": "lyricist", "作曲": "composer", "編曲": "arranger", "歌": "singer", "使用話数": "episodes"}
LINK = re.compile(r"\[\[(.+?)\s+(https?://\S+?)\]\]")
EPISODE = re.compile(r"^\*(\d+)\*(.*)$")


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.read()
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  요청 실패 ({type(e).__name__}) → {30 * (attempt + 1)}초 쉼", flush=True)
            time.sleep(30 * (attempt + 1))
    raise SystemExit("쇼보이 요청이 4번 연속 실패함")


def parse_comment(text: str) -> dict:
    """쇼보이 설명글 → 링크 · 스태프 · 주제가 · 캐스트"""
    out = {"links": [], "staff": [], "themes": [], "cast": []}
    section, theme = "", None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("*") and not line.startswith("**"):
            section = line[1:].strip()
            theme = None
            m = THEME.search(section)
            if m:
                theme = {"type": THEME_TYPE[m.group(1)], "sequence": int(m.group(2)) if m.group(2) else None,
                         "title": m.group(3)}
                out["themes"].append(theme)
        elif section == "リンク" and line.startswith("-"):
            m = LINK.search(line)
            if m:
                out["links"].append({"label": m.group(1), "url": m.group(2)})
        elif line.startswith(":") and ":" in line[1:]:
            left, right = (s.strip() for s in line[1:].split(":", 1))
            if not right:
                continue
            if theme is not None:
                for part in re.split(r"[・/]", left):
                    if part in THEME_ROLE:
                        theme[THEME_ROLE[part]] = right
            elif section == "スタッフ":
                for name in re.split(r"[、,]", right):
                    if name.strip():
                        out["staff"].append({"role": left, "name": name.strip()})
            elif section == "キャスト":
                out["cast"].append({"character": left, "voice": right})
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir: pathlib.Path = PACKAGE_ROOT / ".cache" / "raw" / "syoboi" / dt.date.today().strftime("%Y%m%d")
    raw_dir.mkdir(parents=True, exist_ok=True)

    arm_path = raw_dir / "arm.json"
    if not arm_path.exists():
        arm_path.write_bytes(get(ARM_URL))
    arm = json.loads(arm_path.read_text(encoding="utf-8"))

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("""select source_id, external_id, entity_id from dict.external_id
                       where (source_id, id_kind) in (('anilist', 'anime'), ('syoboi', 'tid'))""")
        entry_ids, tid_entries = {}, defaultdict(set)
        for source, ext, eid in cur.fetchall():
            if source == "anilist":
                entry_ids[ext] = eid
            else:
                tid_entries[ext].add(eid)
    for row in arm:
        al, tid = str(row.get("anilist_id") or ""), str(row.get("syobocal_tid") or "")
        if al in entry_ids and tid:
            tid_entries[tid].add(entry_ids[al])
    tids = sorted(tid_entries, key=int)
    print(f"우리 시즌에 연결된 TID {len(tids):,}개", flush=True)

    batches = [tids[s:s + BATCH] for s in range(0, len(tids), BATCH)]
    todo = [(n, b) for n, b in enumerate(batches) if not (raw_dir / f"page_{n:03d}.xml").exists()]
    print(f"요청: 전체 {len(batches)}번 중 남은 {len(todo)}번", flush=True)
    for n, batch in todo:
        query = urllib.parse.urlencode({"Command": "TitleLookup", "TID": ",".join(batch), "Fields": ITEM_FIELDS})
        (raw_dir / f"page_{n:03d}.xml").write_bytes(get(f"{DB_URL}?{query}"))
        time.sleep(PAUSE)

    items = []
    for path in sorted(raw_dir.glob("page_*.xml")):
        text = path.read_text(encoding="utf-8")
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            raise SystemExit("응답 XML 에 DOCTYPE/ENTITY 가 있어서 읽지 않음")
        root = ET.fromstring(text)
        code = root.findtext("./Result/Code")
        if code != "200":
            print(f"  {path.name}: 결과 코드 {code} {root.findtext('./Result/Message')}", flush=True)
            continue
        at = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
        items += [(at, {c.tag: c.text or "" for c in it}) for it in root.iter("TitleItem")]
    print(f"받은 작품 {len(items):,}개", flush=True)

    stats: Counter = Counter()
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        cur.execute("select entity_id, parent_id from dict.entity where kind = 'entry'")
        parent_of = dict(cur.fetchall())
        cur.execute("""select a.entity_id, a.alias from dict.alias a join dict.entity e using (entity_id)
                       where e.kind = 'person' and a.lang in ('native', 'ko') and a.source_id = 'anilist'""")
        person_by_name: dict[str, set[int]] = defaultdict(set)
        for eid, alias in cur.fetchall():
            person_by_name[norm(alias)].add(eid)

        def person(name: str) -> int | None:
            found = person_by_name.get(norm(name), set())
            return next(iter(found)) if len(found) == 1 else None

        def target(tid: str) -> int | None:
            entries = tid_entries.get(tid, set())
            if len(entries) == 1:
                return next(iter(entries))
            parents = {parent_of.get(e) for e in entries}
            stats["TID 하나가 시즌 여러 개 → 작품에"] += len(parents) == 1
            return next(iter(parents)) if len(parents) == 1 else None

        tid_pairs = [(t, next(iter(es))) for t, es in tid_entries.items() if len(es) == 1]
        pairs, _ = unique_pairs(tid_pairs)
        added, clash = store.add_external_ids(SYOBOI, "tid", pairs)
        stats["쇼보이 번호 새로 붙임"], stats["쇼보이 번호 겹침"] = added, clash

        for at, it in items:
            tid, eid = it["TID"], target(it["TID"])
            if eid is None:
                stats["연결 못 한 작품"] += 1
                continue
            url = PAGE_URL.format(tid)
            start = {"year": int(it["FirstYear"])} if it["FirstYear"].isdigit() else None
            if start and it["FirstMonth"].isdigit():
                start["month"] = int(it["FirstMonth"])
            if start:
                store.fact(eid, "start_date", start, at, url, SYOBOI)
                store.fact(eid, "start_year", start["year"], at, url, SYOBOI)
            if it["FirstEndYear"].isdigit():
                end = {"year": int(it["FirstEndYear"])}
                if it["FirstEndMonth"].isdigit():
                    end["month"] = int(it["FirstEndMonth"])
                store.fact(eid, "end_date", end, at, url, SYOBOI)
            if it["FirstCh"]:
                store.fact(eid, "broadcaster", it["FirstCh"], at, url, SYOBOI)
            eps = [{"no": int(m.group(1)), "title": m.group(2).strip()}
                   for m in (EPISODE.match(x) for x in it["SubTitles"].splitlines()) if m and m.group(2).strip()]
            if eps:
                store.fact(eid, "episode_titles", eps, at, url, SYOBOI)
                # 1화부터 빠짐없이 이어져 있을 때만 화수로 쓴다 (방영 중이거나 일부만 적힌 건 뺌)
                numbers = sorted(e["no"] for e in eps)
                if numbers == list(range(1, len(numbers) + 1)) and it["FirstEndYear"].isdigit():
                    store.fact(eid, "episodes", len(numbers), at, url, SYOBOI)
            info = parse_comment(it["Comment"])
            for link in info["links"]:
                store.fact(eid, "syoboi_link", link, at, url, SYOBOI)
                if link["label"].startswith("公式") and not re.search(r"(?i)x|twitter|ツイッター", link["label"]):
                    store.fact(eid, "official_site", {"url": link["url"]}, at, url, SYOBOI)
            for s in info["staff"]:
                pid = person(s["name"])
                store.fact(eid, "jp_staff", s, at, url, SYOBOI, value_entity_id=pid)
                if s["role"] in ("監督", "総監督") and pid:
                    store.fact(eid, "director", {}, at, url, SYOBOI, value_entity_id=pid)
                stats["스태프 연결" if pid else "스태프 이름만"] += 1
            for t in info["themes"]:
                store.fact(eid, "theme_song_ja", t, at, url, SYOBOI)
            for c in info["cast"]:
                pid = person(c["voice"])
                store.fact(eid, "jp_cast", c, at, url, SYOBOI, value_entity_id=pid)
                stats["캐스트 연결" if pid else "캐스트 이름만"] += 1
        upserted, deleted = store.write_facts(SYOBOI, FIELDS)
        cur.execute("""select field, count(*), count(distinct entity_id) from dict.fact
                       where source_id = 'syoboi' group by 1 order by 2 desc""")
        by_field = cur.fetchall()

    print("연결: " + " · ".join(f"{k} {v:,}" for k, v in stats.items()))
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,}")
    print("칸별 (줄 · 대상): " + " · ".join(f"{f} {n:,} · {e:,}" for f, n, e in by_field))
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
