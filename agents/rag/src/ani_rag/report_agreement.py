"""두 출처 일치율 — 같은 시즌/극장판의 같은 칸을 AniList 와 위키데이터가 다 갖고 있을 때 맞는 비율

  uv run python -m ani_rag.report_agreement        (agents/rag 폴더에서)

- 날짜(start_date): 두 출처에 다 있는 부분(연 · 월 · 일)이 같으면 일치. 일까지 둘 다 있는 것도 따로 셈
- 대상으로 연결된 칸(제작사 · 감독): 위키데이터 쪽 대상이 하나라도 AniList 쪽에 있으면 일치
  (위키데이터 쪽이 우리 대상에 연결되지 않은 줄은 비교에서 뺌)
- 그 밖의 칸: 값이 하나라도 같으면 일치
- 안 맞는 예시를 몇 개씩 보여 줌 (사람이 확인할 목록)
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

import psycopg

from ani_rag.config import load_settings

A, B = "anilist", "wikidata"
# (보여 줄 이름, AniList 칸, 위키데이터 칸, 비교 방식)
CHECKS = [
    ("방영 시작 연도", "start_year", "start_year", "value"),
    ("방영 시작 날짜", "start_date", "start_date", "date"),
    ("화수", "episodes", "episodes", "value"),
    ("제작사 (AniList 전체 스튜디오 중)", "studio", "studio", "entity"),
    ("제작사 (AniList 메인 스튜디오)", "main_studio", "studio", "entity"),
    ("감독", "director", "director", "entity"),
]


def compatible(a: dict, b: dict) -> bool:
    return all(a[k] == b[k] for k in ("year", "month", "day") if k in a and k in b)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings()
    fields = sorted({c[1] for c in CHECKS} | {c[2] for c in CHECKS})
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("""select f.entity_id, f.field, f.source_id, f.value, f.value_entity_id
                       from dict.fact f join dict.entity e on e.entity_id = f.entity_id and e.kind = 'entry'
                       where f.source_id = any(%s) and f.field = any(%s)""", ([A, B], fields))
        data: dict = defaultdict(lambda: defaultdict(list))
        for eid, field, src, value, veid in cur.fetchall():
            data[(field, src)][eid].append((value, veid))
        cur.execute("""select e.entity_id, coalesce(w.name_ko, w.name_romaji) || ' / ' || coalesce(e.name_romaji, '')
                       from dict.entity e join dict.entity w on w.entity_id = e.parent_id where e.kind = 'entry'""")
        label = dict(cur.fetchall())
        cur.execute("select entity_id, coalesce(name_ko, name_romaji, name_native) from dict.entity")
        name = dict(cur.fetchall())

    print("| 칸 | 두 출처에 다 있음 | 일치 | 일치율 |")
    print("|---|---|---|---|")
    examples = {}
    for title, fa, fb, how in CHECKS:
        left, right = data[(fa, A)], data[(fb, B)]
        if how == "entity":
            right = {e: [x for x in v if x[1]] for e, v in right.items()}
            right = {e: v for e, v in right.items() if v}
        both = [e for e in right if e in left]
        agree, exact, misses = 0, 0, []
        for e in both:
            if how == "entity":
                ok = bool({x[1] for x in left[e]} & {x[1] for x in right[e]})
            elif how == "date":
                ok = any(compatible(a, b) for a, _ in left[e] for b, _ in right[e])
                exact += any(a == b and "day" in a for a, _ in left[e] for b, _ in right[e])
            else:
                ok = bool({json.dumps(a) for a, _ in left[e]} & {json.dumps(b) for b, _ in right[e]})
            agree += ok
            if not ok:
                misses.append(e)
        rate = f"{agree / len(both) * 100:.1f}%" if both else "-"
        print(f"| {title} | {len(both):,} | {agree:,} | {rate} |")
        if how == "date":
            print(f"| └ 일까지 둘 다 있고 같음 | | {exact:,} | |")
        examples[title] = (misses[:6], fa, fb, how)

    print("\n안 맞는 예시 (AniList → 위키데이터)")
    for title, (misses, fa, fb, how) in examples.items():
        if not misses:
            continue
        print(f"- {title}")
        for e in misses:
            right = [x for x in data[(fb, B)][e] if x[1]] if how == "entity" else data[(fb, B)][e]
            print(f"  · {label.get(e, e)}: {show(data[(fa, A)][e], how, name)} → {show(right, how, name)}")


def show(rows: list[tuple], how: str, name: dict) -> str:
    return ", ".join(name.get(v, "?") if how == "entity" else json.dumps(val, ensure_ascii=False)
                     for val, v in rows)


if __name__ == "__main__":
    main()
