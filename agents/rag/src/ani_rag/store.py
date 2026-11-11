"""사전 쓰기 공용 — 사실 · 별칭은 모았다가 한 번에 넣는다 (여러 번 돌려도 같은 결과)

- 사실: (대상, 칸, 값, 값 대상, 출처) 가 같으면 한 줄. 받은 날짜는 더 늦은 것으로
- write_facts(source, fields): 이번에 모은 사실로 그 출처의 그 칸들을 맞춘다 (없어진 줄은 지움).
  칸마다 맡는 코드는 하나만 둔다. 두 코드가 같은 출처 · 같은 칸을 쓰면 서로 지운다
- 별칭은 더하기만 한다
"""

from __future__ import annotations

import json
import re

import psycopg

TEMP_TABLES = """
create temp table tmp_work (menu_work_id int, name_ko text, name_native text, name_romaji text)
  on commit drop;
create temp table tmp_ent (external_id text, parent_id bigint, name_native text, name_romaji text)
  on commit drop;
create temp table tmp_ext (external_id text, entity_id bigint) on commit drop;
create temp table tmp_fact (entity_id bigint, field text, value jsonb, value_entity_id bigint,
  source_id text, source_url text, fetched_at timestamptz) on commit drop;
create temp table tmp_alias (entity_id bigint, alias text, lang text, source_id text,
  is_candidate boolean) on commit drop;
"""


def norm(text: str | None) -> str:
    """이름 비교용: 대소문자 · 기호 · 띄어쓰기 무시 (MADHOUSE = Madhouse, A-1 Pictures = A1 Pictures)"""
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def unique_pairs(pairs: list[tuple[str, int]]) -> tuple[dict[str, int], int]:
    """번호 → 대상. 한 번호가 우리 대상 둘 이상에 붙으면 어느 쪽인지 몰라서 뺀다 (뺀 수도 돌려줌)"""
    seen: dict[str, set[int]] = {}
    for ext, eid in pairs:
        seen.setdefault(ext, set()).add(eid)
    return {ext: next(iter(e)) for ext, e in seen.items() if len(e) == 1}, sum(1 for e in seen.values() if len(e) > 1)


class Store:
    def __init__(self, cur: psycopg.Cursor):
        self.cur = cur
        self.facts: dict[tuple, tuple] = {}
        self.aliases: dict[tuple, tuple] = {}
        cur.execute(TEMP_TABLES)

    # ---- 모으기
    def fact(self, entity_id, field, value, fetched_at, url, source, value_entity_id=None):
        if value is None or entity_id is None:
            return
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        key = (entity_id, field, text, value_entity_id or 0, source)
        old = self.facts.get(key)
        if old is None or fetched_at > old[6]:
            self.facts[key] = (entity_id, field, text, value_entity_id, source, url, fetched_at)

    def alias(self, entity_id, alias, lang, source, candidate=False):
        """같은 글자가 여러 칸에서 오면 하나로 합친다. 한국어 표시는 남기고, 하나라도 확정이면 확정
        (예: 한국 작품은 원어 제목 = 한국어 제목이라, 확인 필요 후보라도 원어 제목으로 확정됨)"""
        alias = (alias or "").strip()
        if not alias or entity_id is None:
            return
        key = (entity_id, alias, source)
        old = self.aliases.get(key)
        if old is None:
            self.aliases[key] = (entity_id, alias, lang, source, candidate)
        else:
            merged_lang = "ko" if "ko" in (old[2], lang) else (old[2] or lang)
            self.aliases[key] = (entity_id, alias, merged_lang, source, old[4] and candidate)

    # ---- 대상 · 번호
    def upsert_works(self, rows: list[tuple]) -> tuple[dict[int, int], int]:
        cur = self.cur
        cur.execute("truncate tmp_work")
        with cur.copy("copy tmp_work (menu_work_id, name_ko, name_native, name_romaji) from stdin") as cp:
            for row in rows:
                cp.write_row(row)
        cur.execute("""
            insert into dict.entity (kind, menu_work_id, name_ko, name_native, name_romaji)
            select 'work', menu_work_id, name_ko, name_native, name_romaji from tmp_work
            on conflict (menu_work_id) do update
              set name_ko = excluded.name_ko, name_native = excluded.name_native,
                  name_romaji = excluded.name_romaji
              where (dict.entity.name_ko, dict.entity.name_native, dict.entity.name_romaji)
                    is distinct from (excluded.name_ko, excluded.name_native, excluded.name_romaji)""")
        changed = cur.rowcount
        cur.execute("select menu_work_id, entity_id from dict.entity where kind = 'work'")
        return dict(cur.fetchall()), changed

    def upsert_external(self, source: str, id_kind: str, kind: str,
                        rows: dict[str, tuple]) -> tuple[dict[str, int], int, int]:
        """출처 번호로 대상을 찾고, 없으면 만든다. rows: {번호: (위 대상, 원어 이름, 로마자 이름)}"""
        cur = self.cur
        p = {"src": source, "k": id_kind, "kind": kind}
        cur.execute("truncate tmp_ent")
        with cur.copy("copy tmp_ent (external_id, parent_id, name_native, name_romaji) from stdin") as cp:
            for ext, (parent, native, romaji) in rows.items():
                cp.write_row((ext, parent, native, romaji))
        cur.execute("""
            create temp table made on commit drop as
            select nextval(pg_get_serial_sequence('dict.entity', 'entity_id')) as entity_id, t.*
            from tmp_ent t
            where not exists (select 1 from dict.external_id x where x.source_id = %(src)s
                              and x.id_kind = %(k)s and x.external_id = t.external_id)""", p)
        cur.execute("""insert into dict.entity (entity_id, kind, parent_id, name_native, name_romaji)
                       select entity_id, %(kind)s, parent_id, name_native, name_romaji from made""", p)
        made = cur.rowcount
        cur.execute("""insert into dict.external_id (source_id, id_kind, external_id, entity_id)
                       select %(src)s, %(k)s, external_id, entity_id from made""", p)
        cur.execute("drop table made")
        cur.execute("""
            update dict.entity e
            set parent_id = t.parent_id, name_native = t.name_native, name_romaji = t.name_romaji
            from tmp_ent t join dict.external_id x
              on x.source_id = %(src)s and x.id_kind = %(k)s and x.external_id = t.external_id
            where e.entity_id = x.entity_id
              and (e.parent_id, e.name_native, e.name_romaji)
                  is distinct from (t.parent_id, t.name_native, t.name_romaji)""", p)
        changed = cur.rowcount
        return self.external_map(source, id_kind), made, changed

    def external_map(self, source: str, id_kind: str) -> dict[str, int]:
        self.cur.execute("""select external_id, entity_id from dict.external_id
                            where source_id = %s and id_kind = %s""", (source, id_kind))
        return dict(self.cur.fetchall())

    def add_external_ids(self, source: str, id_kind: str, pairs: dict[str, int]) -> tuple[int, int]:
        """이미 있는 대상에 다른 출처 번호를 붙인다. (새로 붙인 수, 이미 다른 대상에 붙어 있어서 못 붙인 수)"""
        cur = self.cur
        p = {"s": source, "k": id_kind}
        cur.execute("truncate tmp_ext")
        with cur.copy("copy tmp_ext (external_id, entity_id) from stdin") as cp:
            for ext, eid in pairs.items():
                cp.write_row((ext, eid))
        cur.execute("""insert into dict.external_id (source_id, id_kind, external_id, entity_id)
                       select %(s)s, %(k)s, external_id, entity_id from tmp_ext
                       on conflict do nothing""", p)
        added = cur.rowcount
        cur.execute("""select count(*) from tmp_ext t join dict.external_id x
                       on x.source_id = %(s)s and x.id_kind = %(k)s and x.external_id = t.external_id
                       where x.entity_id <> t.entity_id""", p)
        return added, cur.fetchone()[0]

    # ---- 넣기
    def write_facts(self, source: str, fields: tuple[str, ...]) -> tuple[int, int]:
        """(넣거나 고친 줄, 지운 줄). 이 출처의 이 칸들은 이번에 모은 사실로 맞춘다"""
        cur = self.cur
        cur.execute("truncate tmp_fact")
        with cur.copy("copy tmp_fact (entity_id, field, value, value_entity_id, source_id, source_url, "
                      "fetched_at) from stdin") as cp:
            for row in self.facts.values():
                cp.write_row(row)
        cur.execute("analyze tmp_fact")  # 임시 표 크기를 DB 에 알려 줌 (안 하면 지우기 단계가 아주 느려짐)
        cur.execute("""
            insert into dict.fact (entity_id, field, value, value_entity_id, source_id, source_url, fetched_at)
            select entity_id, field, value, value_entity_id, source_id, source_url, fetched_at from tmp_fact
            on conflict (entity_id, field, md5(value::text), coalesce(value_entity_id, 0), source_id)
            do update set source_url = excluded.source_url, fetched_at = excluded.fetched_at
            where (dict.fact.source_url, dict.fact.fetched_at)
                  is distinct from (excluded.source_url, excluded.fetched_at)""")
        upserted = cur.rowcount
        cur.execute("""
            delete from dict.fact f
            where f.source_id = %(src)s and f.field = any(%(fields)s)
              and not exists (select 1 from tmp_fact t
                              where t.entity_id = f.entity_id and t.field = f.field and t.value = f.value
                                and coalesce(t.value_entity_id, 0) = coalesce(f.value_entity_id, 0)
                                and t.source_id = f.source_id)""",
                    {"src": source, "fields": list(fields)})
        return upserted, cur.rowcount

    def write_aliases(self, sync_source: str | None = None) -> int:
        """별칭 넣기. sync_source 를 주면 그 출처의 별칭은 이번에 모은 것으로 맞춘다 (없어진 줄은 지움) —
        맞추기로 만든 별칭(예: 영상물등급위원회)처럼 다시 돌릴 때 결과가 바뀔 수 있는 출처에 씀"""
        cur = self.cur
        cur.execute("truncate tmp_alias")
        with cur.copy("copy tmp_alias (entity_id, alias, lang, source_id, is_candidate) from stdin") as cp:
            for row in self.aliases.values():
                cp.write_row(row)
        cur.execute("analyze tmp_alias")
        cur.execute("""
            insert into dict.alias (entity_id, alias, lang, source_id, is_candidate)
            select entity_id, alias, lang, source_id, is_candidate from tmp_alias
            on conflict (entity_id, alias, source_id) do update
              set lang = excluded.lang, is_candidate = excluded.is_candidate
              where (dict.alias.lang, dict.alias.is_candidate)
                    is distinct from (excluded.lang, excluded.is_candidate)""")
        changed = cur.rowcount
        if sync_source:
            cur.execute("""delete from dict.alias a where a.source_id = %s
                           and not exists (select 1 from tmp_alias t where t.entity_id = a.entity_id
                                           and t.alias = a.alias and t.source_id = a.source_id)""", (sync_source,))
            changed += cur.rowcount
        return changed

    def count(self, sql: str, params=None) -> int:
        self.cur.execute(sql, params)
        return self.cur.fetchone()[0]
