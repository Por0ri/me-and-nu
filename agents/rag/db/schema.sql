-- me;nu 정보사전 표 — 여러 번 돌려도 같은 결과 (이미 있으면 건너뜀)
--
--   docker exec -i menu-dict sh -c 'psql -U "$POSTGRES_USER" -d menu_dict -v ON_ERROR_STOP=1' < agents/rag/db/schema.sql
--
-- 사실 한 줄 = 대상 · 칸 · 값 · 출처 · 근거 주소 · 받은 날짜.
-- 출처마다 한 줄씩 넣고, 출처끼리 값이 다르면 둘 다 남긴다 (어느 쪽이 맞는지는 일치 수 · 사람 확인으로 정함).

create extension if not exists pg_trgm;
create schema if not exists dict;

-- 출처: 약관 요점 · 출처 표시 의무 · 상업 전환 시 재확인 여부
create table if not exists dict.source (
  source_id text primary key,               -- 'anilist', 'wikidata', 'animethemes', 'onnada' ...
  name text not null,
  license text not null,                    -- 약관 · 라이선스 한 줄 요약
  attribution text,                         -- 출처 표시 의무 (없으면 null)
  recheck_if_commercial boolean not null,   -- 상업으로 바뀌면 다시 확인할 출처
  notes text
);
comment on table dict.source is '사전 재료 출처. 약관 요약은 노션 "RAG 에이전트 정리" 페이지와 같게 유지';

-- 대상: 작품 · 시즌/극장판 · 캐릭터 · 인물 · 곡 · 스튜디오
create table if not exists dict.entity (
  entity_id bigserial primary key,
  kind text not null check (kind in ('work', 'entry', 'character', 'person', 'song', 'studio')),
  menu_work_id int unique,                  -- kind = 'work' 일 때만: me;nu 작품 번호
  parent_id bigint references dict.entity,  -- kind = 'entry' 일 때만: 위 작품
  name_ko text,                             -- 확정된 한국어 이름만 (확인 전 후보는 dict.alias)
  name_native text,
  name_romaji text,
  check ((kind = 'work') = (menu_work_id is not null)),
  check ((kind = 'entry') = (parent_id is not null))
);
create index if not exists entity_parent on dict.entity (parent_id);
comment on table dict.entity is 'work = me;nu 작품(시리즈), entry = 시즌 · 극장판 (AniList 작품 하나). 시즌마다 제작사 · 화수가 달라서 나눔';

-- 외부 번호: 같은 출처라도 번호 종류(작품 · 캐릭터 · 인물 …)마다 따로 센다
create table if not exists dict.external_id (
  source_id text not null references dict.source,
  id_kind text not null,                    -- 'anime', 'character', 'staff', 'song', 'artist', 'studio', 'item' ...
  external_id text not null,
  entity_id bigint not null references dict.entity,
  primary key (source_id, id_kind, external_id)
);
create index if not exists external_id_entity on dict.external_id (entity_id);

-- 별칭: 기사 속 제목 · 이름을 대상에 연결할 때 쓴다 (비슷한 글자 찾기)
-- is_candidate = 이 글자가 이 대상을 가리키는지 아직 확인 안 됨. 작품 한국어 제목 확정은 entity.name_ko 로만 본다
create table if not exists dict.alias (
  entity_id bigint not null references dict.entity,
  alias text not null,
  lang text,                                -- 'ko', 'ja', 'en', 'romaji'
  source_id text not null references dict.source,
  is_candidate boolean not null default false,  -- 확인 전 후보 (예: 원작 정발 제목)
  primary key (entity_id, alias, source_id)
);
create index if not exists alias_trgm on dict.alias using gin (alias gin_trgm_ops);

-- 사실: 출처마다 한 줄
create table if not exists dict.fact (
  fact_id bigserial primary key,
  entity_id bigint not null references dict.entity,
  field text not null,                      -- 'studio', 'episodes', 'start_date', 'birthday', 'theme_song' ...
  value jsonb not null,                     -- 값 (글자 · 숫자 · 날짜 · 묶음)
  value_entity_id bigint references dict.entity,  -- 값이 다른 대상일 때 (예: 감독 → 인물, 캐릭터 → 성우)
  source_id text not null references dict.source,
  source_url text,                          -- 근거 주소 (챗봇 답에 같이 보여 줌)
  fetched_at timestamptz not null           -- 출처에서 받은 날짜
);
-- 같은 출처의 같은 사실은 한 줄만. 값이 길어도 되게 값 대신 지문(md5)으로 겹침을 검사
create unique index if not exists fact_once
  on dict.fact (entity_id, field, md5(value::text), coalesce(value_entity_id, 0), source_id);
create index if not exists fact_entity_field on dict.fact (entity_id, field);
create index if not exists fact_value_entity on dict.fact (value_entity_id);
create index if not exists fact_source on dict.fact (source_id);

-- 두 출처 일치: 같은 대상 · 칸 · 값을 몇 출처에서 확인했나 (2 이상 = 두 곳 확인)
create or replace view dict.fact_agreement as
select entity_id, field, value, value_entity_id,
       count(distinct source_id) as n_sources,
       array_agg(distinct source_id order by source_id) as sources
from dict.fact
group by entity_id, field, value, value_entity_id;
