# load_model_db — 모델이 쓰는 작품 정보를 모델 전용 DB(anime-db)에 넣는다. 서비스 앱 DB 와는 별개
#
# anilist 스키마: fetch_anilist.py 가 만든 파일(media · media_recent · links · detail .jsonl)
#   1) anilist.media_raw: 받은 원본 (작품 번호 · 부분 · JSON). 다시 받지 않고도 표를 새로 만들 수 있게 남긴다
#   2) 원본에서 표를 새로 만든다: 작품 · 장르 · 태그 · 관계 · 커뮤니티 추천 · 스튜디오 · 제작진 · 캐릭터 · 성우
# menu 스키마: make_work_catalog.py 가 만든 me;nu 작품 번호 목록 (menu_works · menu_work_entries .csv)
#   한 번 붙은 번호는 안 바꾸는 규칙이라, DB 에 있던 작품의 번호가 새 파일에서 달라지면 넣지 않고 멈춘다
# 여러 번 돌려도 같은 결과 (원본은 덮어쓰고 표는 지우고 다시 만듦)
#
#   docker compose -f model/db/compose.yaml up -d       # 모델 DB 켜기 (Docker Desktop 필요)
#   python model/scripts/load_model_db.py

import csv
import io
import json
import os
import pathlib
import subprocess

CONTAINER = os.environ.get("ANIME_DB_CONTAINER", "anime-db")
DB = "anime_db"
DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
SRC = DATA / "anilist"
PARTS = [("media.jsonl", "base"), ("media_recent.jsonl", "base"), ("links.jsonl", "links"), ("detail.jsonl", "detail")]
WORK_COLS = ["menu_work_id", "title_ko", "title_ko_source", "title_ko_check", "title_romaji", "title_english",
             "title_native", "first_year", "last_year", "n_entries", "popularity"]
ENTRY_COLS = ["anilist_id", "mal_id", "menu_work_id", "format", "year", "title_ko", "title_romaji", "title_english",
              "title_native", "popularity"]

CATALOG_SCHEMA = """
create schema if not exists menu;
create table if not exists menu.work (
  menu_work_id int primary key, title_ko text, title_ko_source text, title_ko_check text,
  title_romaji text, title_english text, title_native text,
  first_year int, last_year int, n_entries int, popularity int
);
comment on table menu.work is 'me;nu 작품 번호. 시리즈 하나에 번호 하나, 한 번 붙은 번호는 안 바꿈 (make_work_catalog.py)';
create table if not exists menu.work_entry (
  anilist_id int primary key, mal_id int, menu_work_id int not null,
  format text, year int, title_ko text, title_romaji text, title_english text, title_native text, popularity int
);
comment on table menu.work_entry is 'AniList 작품(시즌·극장판) → me;nu 작품 번호';
create temp table work_in (like menu.work);
create temp table entry_in (like menu.work_entry);
"""

CATALOG_SWAP = """
do $$
declare moved int; gone int;
begin
  select count(*) into moved from menu.work_entry o join entry_in n using (anilist_id) where o.menu_work_id <> n.menu_work_id;
  if moved > 0 then
    raise exception '작품 번호가 바뀐 AniList 작품 %개 → 넣지 않음 (한 번 붙은 번호는 안 바꿈)', moved;
  end if;
  select count(*) into gone from menu.work o where not exists (select 1 from work_in n where n.menu_work_id = o.menu_work_id);
  if gone > 0 then
    raise exception '새 파일에 없는 작품 번호 %개 → 넣지 않음 (번호는 지우지 않음)', gone;
  end if;
end $$;
truncate menu.work_entry, menu.work;
insert into menu.work select * from work_in;
insert into menu.work_entry select * from entry_in;
"""

SCHEMA = """
set client_encoding to 'UTF8';
set client_min_messages to warning;
create schema if not exists anilist;
create table if not exists anilist.media_raw (
  anilist_id int not null,
  part text not null,
  payload jsonb not null,
  loaded_at timestamptz not null default now(),
  primary key (anilist_id, part)
);
comment on table anilist.media_raw is 'AniList 에서 받은 원본. part: base(제목·장르·태그) / links(관계·추천·인기) / detail(줄거리·스튜디오·제작진·캐릭터·성우)';
create temp table raw_in (anilist_id int, part text, payload jsonb);
copy raw_in from stdin with (format csv);
"""

TABLES = r"""\.
insert into anilist.media_raw (anilist_id, part, payload)
select anilist_id, part, payload from raw_in
on conflict (anilist_id, part) do update set payload = excluded.payload, loaded_at = now();

drop table if exists anilist.media, anilist.media_genre, anilist.media_tag, anilist.media_relation,
  anilist.media_recommendation, anilist.studio, anilist.media_studio, anilist.staff, anilist.media_staff,
  anilist.character, anilist.media_character, anilist.character_voice;

create table anilist.media as
select b.anilist_id, (b.payload->>'idMal')::int as mal_id,
       b.payload->'title'->>'romaji' as title_romaji, b.payload->'title'->>'english' as title_english,
       b.payload->'title'->>'native' as title_native, b.payload->>'format' as format,
       (b.payload->'startDate'->>'year')::int as start_year, (b.payload->>'isAdult')::boolean as is_adult,
       d.payload->>'description' as description, d.payload->>'source' as source,
       (d.payload->>'seasonYear')::int as season_year, (d.payload->>'episodes')::int as episodes,
       (d.payload->>'duration')::int as duration, d.payload->>'countryOfOrigin' as country,
       (l.payload->>'popularity')::int as popularity, (l.payload->>'favourites')::int as favourites,
       (l.payload->>'averageScore')::int as average_score
from anilist.media_raw b
left join anilist.media_raw d on d.anilist_id = b.anilist_id and d.part = 'detail'
left join anilist.media_raw l on l.anilist_id = b.anilist_id and l.part = 'links'
where b.part = 'base';
alter table anilist.media add primary key (anilist_id);
comment on table anilist.media is 'AniList 작품 (base + detail + links 합친 것)';

create table anilist.media_genre as
select anilist_id, g as genre
from anilist.media_raw, jsonb_array_elements_text(payload->'genres') g where part = 'base';

create table anilist.media_tag as
select anilist_id, t->>'name' as tag, (t->>'rank')::int as rank, t->>'category' as category,
       coalesce((t->>'isGeneralSpoiler')::boolean, false) or coalesce((t->>'isMediaSpoiler')::boolean, false) as is_spoiler,
       coalesce((t->>'isAdult')::boolean, false) as is_adult
from anilist.media_raw, jsonb_array_elements(payload->'tags') t where part = 'base';

create table anilist.media_relation as
select anilist_id, (e->'node'->>'id')::int as related_id, e->>'relationType' as relation_type, e->'node'->>'type' as related_type
from anilist.media_raw, jsonb_array_elements(payload->'relations'->'edges') e where part = 'links';

create table anilist.media_recommendation as
select anilist_id, (n->'mediaRecommendation'->>'id')::int as rec_id, (n->>'rating')::int as rating
from anilist.media_raw, jsonb_array_elements(payload->'recommendations'->'nodes') n
where part = 'links' and n->'mediaRecommendation'->>'id' is not null;

create table anilist.studio as
select distinct on (studio_id) studio_id, name, is_animation_studio from (
  select (e->'node'->>'id')::int as studio_id, e->'node'->>'name' as name, (e->'node'->>'isAnimationStudio')::boolean as is_animation_studio
  from anilist.media_raw, jsonb_array_elements(payload->'studios'->'edges') e where part = 'detail') s
order by studio_id;
alter table anilist.studio add primary key (studio_id);

create table anilist.media_studio as
select distinct anilist_id, (e->'node'->>'id')::int as studio_id, (e->>'isMain')::boolean as is_main
from anilist.media_raw, jsonb_array_elements(payload->'studios'->'edges') e where part = 'detail';

create table anilist.staff as
select distinct on (staff_id) staff_id, name_full, name_native from (
  select (e->'node'->>'id')::int as staff_id, e->'node'->'name'->>'full' as name_full, e->'node'->'name'->>'native' as name_native
  from anilist.media_raw, jsonb_array_elements(payload->'staff'->'edges') e where part = 'detail'
  union all
  select (v->>'id')::int, v->'name'->>'full', v->'name'->>'native'
  from anilist.media_raw, jsonb_array_elements(payload->'characters'->'edges') e, jsonb_array_elements(e->'voiceActors') v
  where part = 'detail') s
order by staff_id;
alter table anilist.staff add primary key (staff_id);
comment on table anilist.staff is '제작진과 성우 (AniList staff)';

create table anilist.media_staff as
select distinct anilist_id, (e->'node'->>'id')::int as staff_id, e->>'role' as role
from anilist.media_raw, jsonb_array_elements(payload->'staff'->'edges') e where part = 'detail';

create table anilist.character as
select distinct on (character_id) character_id, name_full, name_native from (
  select (e->'node'->>'id')::int as character_id, e->'node'->'name'->>'full' as name_full, e->'node'->'name'->>'native' as name_native
  from anilist.media_raw, jsonb_array_elements(payload->'characters'->'edges') e where part = 'detail') s
order by character_id;
alter table anilist.character add primary key (character_id);

create table anilist.media_character as
select distinct anilist_id, (e->'node'->>'id')::int as character_id, e->>'role' as role
from anilist.media_raw, jsonb_array_elements(payload->'characters'->'edges') e where part = 'detail';

create table anilist.character_voice as
select distinct anilist_id, (e->'node'->>'id')::int as character_id, (v->>'id')::int as staff_id
from anilist.media_raw, jsonb_array_elements(payload->'characters'->'edges') e, jsonb_array_elements(e->'voiceActors') v
where part = 'detail';
comment on table anilist.character_voice is '캐릭터의 일본 성우';

create index on anilist.media_studio (studio_id);
create index on anilist.media_staff (staff_id);
create index on anilist.character_voice (staff_id);
"""

REPORT = """
select '원본 ' || part as "표", count(*) as "줄" from anilist.media_raw group by part
union all select '작품', count(*) from anilist.media
union all select '줄거리 있는 작품', count(*) from anilist.media where coalesce(description, '') <> ''
union all select '스튜디오 연결', count(*) from anilist.media_studio
union all select '제작진 연결', count(*) from anilist.media_staff
union all select '캐릭터 연결', count(*) from anilist.media_character
union all select '성우 연결', count(*) from anilist.character_voice
union all select '사람 (제작진·성우)', count(*) from anilist.staff
union all select 'me;nu 작품 번호', count(*) from menu.work
union all select 'me;nu 작품 번호에 연결된 AniList 작품', count(*) from menu.work_entry;
"""


def raw_rows():
    # (작품 번호, 부분) 마다 마지막에 받은 것 하나 (media · media_recent 에 같은 작품이 있으면 하나로)
    rows = {}
    for fname, part in PARTS:
        path = SRC / fname
        if not path.exists():
            print(f"  {fname} 없음 → 건너뜀")
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                for m in json.loads(line)["media"]:
                    rows[(m["id"], part)] = m
    return rows


def copy_block(table, path, cols):
    # CSV 파일에서 cols 칸만 골라 COPY ... FROM STDIN 블록으로 (빈칸은 NULL)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            w.writerow([r[c] for c in cols])
    return f"copy {table} ({', '.join(cols)}) from stdin with (format csv);\n{buf.getvalue()}\\.\n"


def main():
    rows = raw_rows()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for (aid, part), m in rows.items():
        w.writerow([aid, part, json.dumps(m, ensure_ascii=False)])
    script = (SCHEMA + buf.getvalue() + TABLES
              + CATALOG_SCHEMA + copy_block("work_in", DATA / "menu_works.csv", WORK_COLS)
              + copy_block("entry_in", DATA / "menu_work_entries.csv", ENTRY_COLS) + CATALOG_SWAP + REPORT)
    cmd = ["docker", "exec", "-i", CONTAINER, "sh", "-c", f'psql -U "$POSTGRES_USER" -d {DB} -v ON_ERROR_STOP=1 -q']
    print(f"원본 {len(rows):,}개 넣는 중 ({len(script.encode('utf-8')) / 1e6:.1f}MB)", flush=True)
    subprocess.run(cmd, input=script.encode("utf-8"), check=True)


if __name__ == "__main__":
    main()
