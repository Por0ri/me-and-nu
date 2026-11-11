"""사실 검증 현황 — 출처끼리 얼마나 맞는지 + 칸마다 '여러 곳 일치 / 서로 다름 / 한 곳뿐' 이 얼마인지

  uv run python -m ani_rag.report_verification        (agents/rag 폴더에서)

- 대상 칸: 시작 연도 · 시작 날짜 · 종료 날짜 · 화수 · 감독 (시즌), 생년월일 (인물), 생일 (캐릭터)
- 날짜는 양쪽에 다 있는 부분(연 · 월 · 일)이 같으면 일치, 값이 여럿이면 하나라도 맞으면 일치
- 감독은 같은 인물로 연결된 경우만 비교한다
- 상태: 여러 곳 일치 = 두 출처 이상이 같은 값 / 서로 다름 = 두 출처 이상인데 맞는 쌍이 없음 / 한 곳뿐
- 결과는 화면과 .cache/reports/verification.md 에 쓴다. DB 는 바꾸지 않는다
"""

from __future__ import annotations

import itertools
import sys
from collections import Counter, defaultdict

import psycopg

from ani_rag.config import PACKAGE_ROOT, load_settings

# (보여 줄 이름, 칸, 대상 종류, 비교 방식)
CHECKS = [
    ("방영 시작 연도", "start_year", "entry", "value"),
    ("방영 시작 날짜", "start_date", "entry", "date"),
    ("방영 종료 날짜", "end_date", "entry", "date"),
    ("화수", "episodes", "entry", "value"),
    ("감독", "director", "entry", "entity"),
    ("인물 생년월일", "birth_date", "person", "date"),
    ("캐릭터 생일", "birthday", "character", "value"),
]


def same(a, b, how: str) -> bool:
    if how == "date":
        shared = [k for k in ("year", "month", "day") if k in a and k in b]
        return bool(shared) and all(a[k] == b[k] for k in shared)
    return a == b


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings()
    lines = []
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        for title, field, kind, how in CHECKS:
            cur.execute("""select f.entity_id, f.source_id, f.value, f.value_entity_id
                           from dict.fact f join dict.entity e on e.entity_id = f.entity_id and e.kind = %s
                           where f.field = %s""", (kind, field))
            per: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
            for eid, source, value, veid in cur.fetchall():
                if how == "entity":
                    if veid:
                        per[eid][source].append(veid)
                else:
                    per[eid][source].append(value)
            status: Counter = Counter()
            pairs: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
            for sources in per.values():
                names = sorted(sources)
                if len(names) == 1:
                    status["한 곳뿐"] += 1
                    continue
                any_agree = False
                for x, y in itertools.combinations(names, 2):
                    ok = any(same(a, b, how) for a in sources[x] for b in sources[y])
                    pairs[(x, y)][0] += 1
                    pairs[(x, y)][1] += ok
                    any_agree |= ok
                status["여러 곳 일치" if any_agree else "서로 다름"] += 1
            total = sum(status.values())
            lines.append(f"\n### {title} (대상 {total:,})")
            lines.append(" · ".join(f"{k} {v:,} ({v / total * 100:.0f}%)" for k, v in status.most_common()))
            lines.append("| 출처 쌍 | 둘 다 있음 | 일치율 |")
            lines.append("|---|---|---|")
            for (x, y), (both, ok) in sorted(pairs.items(), key=lambda kv: -kv[1][0]):
                lines.append(f"| {x} ↔ {y} | {both:,} | {ok / both * 100:.1f}% |")
    text = "\n".join(lines)
    print(text)
    out = PACKAGE_ROOT / ".cache" / "reports" / "verification.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
