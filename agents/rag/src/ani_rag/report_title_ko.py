"""한국어 작품 제목 후보표 — 작품 목록(menu_works.csv)에 확정된 한국어 제목이 없는 작품마다
사전에 모인 후보를 모아 CSV 로

  uv run python -m ani_rag.report_title_ko        (agents/rag 폴더에서)

- 작품 목록이 한국어 제목을 정하는 원본이라, 사전은 후보만 모아서 사람이 확인하게 넘긴다
- 출처마다 칸을 나눔: 영상물등급위원회(공식) · 위키데이터(시즌 · 시리즈 이름) · 원작 정발 제목(위키데이터, 다를 수 있음)
  · AniList 다른 이름 중 한글 · 작품 목록의 '확인 필요' 후보
- 시즌 별칭은 TV · TV 짧은 편 · ONA 시즌 것만 쓴다 (극장판 · OVA 제목은 시리즈 제목이 아님)
- 결과: .cache/reports/title_ko_candidates.csv (인기 순, 엑셀에서 바로 열리게 utf-8-sig)
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict

import psycopg

from ani_rag.config import PACKAGE_ROOT, load_settings

HANGUL = re.compile(r"[가-힣]")
COLUMNS = ["menu_work_id", "인기 순위", "로마자 제목", "원어 제목", "작품 목록 후보(확인 필요)",
           "영상물등급위원회 (공식)", "위키데이터 (시즌 · 시리즈)", "원작 정발 제목 (위키데이터, 다를 수 있음)",
           "AniList 다른 이름 (한글)", "온나다 (캐릭터 페이지 기준)"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings()
    out = PACKAGE_ROOT / ".cache" / "reports" / "title_ko_candidates.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("""select w.entity_id, w.menu_work_id, w.name_romaji, w.name_native,
                              max((p.value #>> '{}')::int) as pop
                       from dict.entity w
                       left join dict.entity e on e.parent_id = w.entity_id
                       left join dict.fact p on p.entity_id = e.entity_id and p.field = 'popularity'
                       where w.kind = 'work' and w.name_ko is null
                       group by 1, 2, 3, 4 order by pop desc nulls last""")
        works = cur.fetchall()
        cur.execute("""select coalesce(e.parent_id, e.entity_id) as work, a.alias, a.source_id, a.is_candidate
                       from dict.alias a join dict.entity e on e.entity_id = a.entity_id
                       left join dict.fact f on f.entity_id = e.entity_id and f.field = 'format'
                                            and f.source_id = 'anilist'
                       where e.kind = 'work'
                          or (e.kind = 'entry' and f.value #>> '{}' in ('TV', 'TV_SHORT', 'ONA'))""")
        found: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for work, alias, source, candidate in cur.fetchall():
            if not HANGUL.search(alias):
                continue
            col = {("onnada", True): "온나다 (캐릭터 페이지 기준)",
                   ("menu_catalog", True): "작품 목록 후보(확인 필요)",
                   ("kmrb", False): "영상물등급위원회 (공식)",
                   ("wikidata", False): "위키데이터 (시즌 · 시리즈)",
                   ("wikidata", True): "원작 정발 제목 (위키데이터, 다를 수 있음)",
                   ("anilist", False): "AniList 다른 이름 (한글)"}.get((source, candidate))
            if col and alias not in found[work][col]:
                found[work][col].append(alias)

    rows, with_any = [], 0
    for rank, (eid, menu_id, romaji, native, _) in enumerate(works, 1):
        cand = found.get(eid, {})
        with_any += bool(cand)
        rows.append({"menu_work_id": menu_id, "인기 순위": rank, "로마자 제목": romaji, "원어 제목": native,
                     **{c: " / ".join(cand.get(c, [])) for c in COLUMNS[4:]}})
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"확정 한국어 제목이 없는 작품 {len(works):,}편 중 후보가 하나라도 있는 작품 {with_any:,}편")
    for col in COLUMNS[4:]:
        print(f"  {col}: {sum(1 for r in rows if r[col]):,}편")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
