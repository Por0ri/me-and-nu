# `menu_dict.dump` → ani_rag 검토 및 가져오기 계약 초안

작성일: 2026-11-09
상태: 초안; 실제 덤프 검토 및 AniList snapshot 사용 결정은 [`MENU_DICT_RAG_IMPORT.md`](MENU_DICT_RAG_IMPORT.md)에 갱신

## 2026-11-09 업데이트

이 문서 작성 뒤 전체 덤프를 임시 디렉터리의 `pgdumplib`로 읽기 전용 해석했다. PostgreSQL 서버에 복원하지 않았으며 실제 테이블·행 수와 출처/필드 분포를 확인했다. 팀이 이 공유 데이터의 AniList 대량 저장 및 AI/RAG 이용 허가를 받았다는 사용자의 확인에 따라 `config/menu_dict_import_policy.json`에서 AniList snapshot import를 허용한다. 이 결정은 AniList GraphQL API를 MCP 수집 출처로 승인하는 결정과는 별개다. 현재 실행 중인 RAG DB에는 아직 import하지 않았다. 상세 수치와 import 계약은 [`MENU_DICT_RAG_IMPORT.md`](MENU_DICT_RAG_IMPORT.md)를 정본으로 본다.

## 검토 범위

- 팀원이 준 `C:\Users\yunju\Downloads\menu_dict.dump`, `dictionary_inventory.html`, `README (6).md`를 읽기 전용으로 확인했다.
- `menu_dict.dump`는 35,540,327바이트의 PostgreSQL custom-format 백업(`PGDMP`)이다. SHA-256: `e2219a0ca8e1e7bcf52def135228eb1f421d8aa0aa885bc46e9ecdae775e66a5`.
- `pg_restore` CLI는 설치되어 있지 않다. `pgdumplib`를 OS 임시 폴더에 설치해 custom archive를 읽기 전용으로 해석했다. 데이터베이스 복원·연결·쓰기 없이 테이블 데이터 행을 집계했다.
- README에 적힌 Docker·`pg_restore` 복원 안내는 검토 대상 문서의 내용으로만 취급했다. DB/컨테이너/프로젝트 DB에는 접속하거나 복원하지 않았다.

## 데이터셋이 제공하는 것

동봉 문서 설명상 정보사전은 `entity`, `fact`, `alias`, `external_id`, `source` 테이블과 `fact_agreement` 보기를 가진다. 범위는 작품·시즌/극장판·캐릭터·인물·주제가·스튜디오다.

| 동봉 자료의 단위 | README/인벤토리 설명상 규모 | ani_rag에서의 용도 |
|---|---:|---|
| 작품 | 2,322 | 작품 카탈로그 매칭 및 가이드 소재 |
| 작품 엔트리(시즌/극장판 등) | 3,887 | 작품-시즌 관계와 외부 ID 연결 후보 |
| 캐릭터 | 53,411 | 캐릭터 이름·프로필·관계의 사실 후보 |
| 인물 | 24,360 | 성우·감독·가수 등의 인물 자료 |
| 주제가 | 9,425 | 작품별 OP/ED 및 아티스트 관계 |
| 스튜디오 | 1,076 | 제작사 정보 |
| 출처가 있는 사실 행 | 988,883 | 출처별 사실 저장 및 검증 후보 |
| 별칭 | 233,957 | 이름 매칭·검색 보강 |

각 사실 행은 `entity_id`, `field`, JSON `value`, 선택적 `value_entity_id`, `source_id`, `source_url`, `fetched_at`을 보존한다고 설명되어 있다. 서로 다른 출처의 값은 합치거나 지우지 않고 남기며, 일치 수는 별도 보기에서 계산한다. 이는 출처와 충돌을 유지해야 하는 RAG 사실 저장에 잘 맞는다.

백업의 TOC에 포함된 테이블 정의에서 확인한 컬럼은 다음과 같다.

- `dict.entity`: `entity_id`, `kind`, `menu_work_id`, `parent_id`, `name_ko`, `name_native`, `name_romaji`
- `dict.fact`: `fact_id`, `entity_id`, `field`, `value`(JSONB), `value_entity_id`, `source_id`, `source_url`, `fetched_at`
- `dict.alias`: `entity_id`, `alias`, `lang`, `source_id`, `is_candidate`
- `dict.external_id`: `source_id`, `id_kind`, `external_id`, `entity_id`
- `dict.source`: `source_id`, `name`, `license`, `attribution`, `recheck_if_commercial`, `notes`

이 확인은 컬럼 정의만 대상으로 했으며, TOC 문자열을 SQL로 실행하지 않았다. 제약조건·인덱스·실제 필드 값/출처 행은 아직 확인하지 않았다.

## 직접 적재가 안 되는 이유

현재 `ani_rag` 스키마에는 원문 문서(`rag_document`), 작품 카탈로그(`rag_work_catalog`), RAG 엔티티(`rag_entity`), 생성 초안의 주장(`rag_claim`)은 있지만, 이 사전의 출처별 구조화 사실 행을 그대로 저장하는 범용 테이블은 없다. `rag_claim`은 생성 초안에 속하므로 사전 사실을 넣는 테이블로 재사용하면 안 된다.

ID 네임스페이스도 별개다.

- 사전 `entity_id`는 사전 내부 키이며, RAG ERD의 `entity_id`와 동일하다고 볼 근거가 없다.
- 사전 `menu_work_id`를 현재 작품 ID에 그대로 덮어쓰면 안 된다. 현재 RAG 로컬 스냅샷 CSV에는 작품 2,351행과 엔트리 3,889행이 있고, 동봉 README는 작품 2,322편과 엔트리 3,887개를 말한다. 수치가 다르므로 숫자만으로 일치 여부를 추정할 수 없다.
- 사전 `kind`에는 `work`, `entry`, `character`, `person`, `song`, `studio`가 있지만 ERD용 `topic_id`/`subtopic_id`는 설명에 없다. 비어 있는 분류 ID를 생성하거나 추정해서 채우면 안 된다.

## 제안 매핑

| 사전 원본 | 보존할 원본 키/필드 | RAG 연결 방식 | 주의 |
|---|---|---|---|
| `dict.source` | 원본 `source_id`, 이름, 라이선스/표시 조건 | 별도 매핑 후 RAG 출처 레지스트리와 연결 | 출처를 승인 목록에 자동 추가하지 않는다. 출처별 조건 확인 전에는 해당 사실을 생성 근거로 사용하지 않는다. |
| `dict.entity` (`work`) | 원본 `entity_id`, `menu_work_id`, 제목 필드 | 교차표에서 확인된 RAG `menu_work_id`만 `rag_work_catalog`에 연결 | 정본 작품 ID를 변경하지 않는다. |
| `dict.entity` (`entry`) | 원본 `entity_id`, `parent_id`, 제목/형식 | 부모 작품과 외부 ID를 확인한 뒤 RAG 엔트리 관계로 연결 | 시즌·극장판을 제목 유사도만으로 병합하지 않는다. |
| `dict.entity` (`character/person/song/studio`) | 원본 `entity_id`, `kind`, 이름 | 원본 엔티티 ID를 별도 네임스페이스로 보존하고 승인된 crosswalk가 있을 때만 ERD `entity_id`에 연결 | `topic_id`가 없으면 RAG 엔티티 등록을 보류한다. |
| `dict.fact` | 원본 사실 ID, 엔티티 ID, field, JSON value, value entity ID, source ID/URL, fetched_at | 신규 `rag_dictionary_fact` 또는 동등한 전용 사실 저장 계약에 보존 | 생성 초안 주장과 분리한다. 원본 JSON과 출처를 유지하고 충돌 사실을 모두 남긴다. |
| `dict.alias` | 엔티티 ID, alias, 언어, 출처, `is_candidate` | 매칭 별칭 저장소로 보존 | `is_candidate=true`인 표기를 확정 이름으로 쓰지 않는다. |
| `dict.external_id` | 원본 source, id 종류, 외부 ID, 엔티티 ID | 교차표 후보를 만드는 보조키 | 해당 ID가 가리키는 작품/엔트리 단위를 확인하고 연결한다. |
| `dict.fact_agreement` | 보기 정의/집계 결과 | 가져오지 않고 사실 행에서 다시 계산 | 사전 사실 자체와 파생 집계를 구별한다. |

교차표는 최소한 `dictionary_entity_id`, `dictionary_menu_work_id`, 원본 `kind`, 외부 ID 종류/값, 정규화 제목, 부모 엔티티 ID, 후보 RAG ID, 매칭 방법, 상태(`matched`/`ambiguous`/`unresolved`), 검토 근거를 기록해야 한다. 자동 매칭은 외부 ID와 정확한 제목·형식·부모 관계가 함께 맞는 경우만 확정 후보로 만들고, 나머지는 검토 큐에 둔다.

## RAG 검색에 넣을 때의 경계

- 출처가 허용된 사실만 원본 근거로 보존하고, 원문 URL·확인 시각·출처별 라이선스/표시 조건을 함께 저장한다.
- 검색용 청킹/임베딩은 출처와 필드가 허용된 사실에서 파생하고, 관계형 사실 행을 정본으로 유지한다.
- `_ref` 소개문은 참고 자료로만 보고 문장을 복사하거나 그대로 임베딩하지 않는다. README의 요약 지침에 따라 새 문장으로 재작성한다.
- `recommendation`은 사용자 추천 신호로 보존하되 객관적 유사도나 사실로 사용하지 않는다.
- `spoiler` 또는 `adult` 표시가 있는 태그는 콘텐츠 생성·검색에 넣지 않는다.
- README는 전체 사실의 약 73%가 AniList에서 왔다고 설명한다. `dict.source`의 실제 행과 출처 조건을 확인하기 전에는 대량으로 검색 인덱스에 넣거나 생성 근거로 쓰지 않는다.
- 문서상 곡 발매일/음반 종류, 회차별 줄거리, 명대사, 제작 비하인드, 팬 반응은 공유본에 없다. ANN·MAL·AniDB·Kitsu·Livechart는 일부 외부 번호 연결만 있다고 하므로 그 번호만으로 해당 출처의 사실을 인용하지 않는다.
- README는 공유본을 팀 밖으로 내보내지 말라고 적고 있다. 사용 범위는 내부 검토로 제한하고 공개 저장소/링크로 복사하지 않는다.

## 가져오기 전 필요한 검증

전체 덤프의 source/entity/fact 행 수, 필드 분포, 원문 URL host는 읽기 전용으로 확인했다. 팀 제공 권한 확인이 반영되어 import policy에 AniList snapshot이 포함됐다. 라이브 적재 전에 남은 작업:

1. archive entity/external ID를 현재 RAG의 작품·엔트리 카탈로그와 대조해 ID 범위 차이 및 모호한 연결을 보고한다. 현재 import adapter는 archive 안의 명시적 work/entry 관계만 연결한다.
2. 생성 queue에 자동으로 대량 요청이 쌓이지 않도록, 어떤 menu_work_id/type을 생성 대상으로 넣을지 별도 범위를 정한다. importer 자체는 generation backlog를 만들지 않는다.
3. 공유 RAG DB를 사용하는 crawler/generator/reviewer를 멈춘 뒤, 이미 schema가 적용된 `ani_rag` DB에 import 도구를 실행한다. importer는 스키마를 적용하지 않으며 기존 period DB는 받지 않는다.

importer와 출처 필터는 [`MENU_DICT_RAG_IMPORT.md`](MENU_DICT_RAG_IMPORT.md)에 기록했다. 전체 데이터는 RAG DB에 아직 적재하지 않았다.

## 2026-11-09 CSV 공유본 후속 확인

팀원이 제공한 `menu_dict_csv_20261109.zip`을 읽기 전용으로 확인했고, 압축을 `data/reference/menu_dict_20261109/`에 별도 버전으로 보존했다. 원본 ZIP SHA-256과 각 파일 해시는 해당 폴더의 `SNAPSHOT_MANIFEST.md`에 기록했다. 기존 `data/reference/` 파일은 교체하지 않았다.

- CSV에서 실제 확인된 것은 `work` 2,322행, `entry` 3,887행, 외부 ID 28,969행, 별칭 31,164행, 사실 표본 754행이다. entry의 `parent_id`는 모두 ZIP 내 work 엔티티를 가리킨다.
- `entity.csv`의 `entity_id`와 `menu_work_id`는 서로 다른 네임스페이스다. 예를 들어 `menu_work_id=1`의 work 행은 `entity_id=5`다.
- RAG 기존 스냅샷은 ZIP보다 작품 ID가 29개 더 많다. 공통 작품에서도 한국어 제목의 채움 여부가 497건 다르고, 양쪽에 값이 있는 75건은 표기가 다르다. 따라서 ZIP은 보조 crosswalk로만 사용한다.
- `alias.csv`의 `is_candidate=t` 1,467행은 새 읽기 전용 resolver가 제외한다. 확정 별칭도 여러 work/entry 엔티티에 걸릴 수 있어 모호성은 유지한다. AniList 등 외부 ID는 source·id kind·값이 모두 정확히 일치할 때 조회한다.
- `fact_samples.csv`는 754개 표본이며 25개 값이 잘려 있다. 공유본의 `entity.csv`는 work/entry만 포함하므로 사실 표본 중 610개가 이 엔티티 목록에서 확인되지 않는다. 이는 전체 사실 테이블의 대체물이 아니며 RAG 근거로 적재하지 않았다.
- `source.csv`는 18개 출처의 요약 메모다. 약관 원문·검증 시각·승인 상태가 없으므로 `sources.evergreen.json`을 변경하거나 출처를 승인하지 않았다.
- `src/ani_rag/menu_dict_reference.py`는 이 버전의 exact external ID/확정 alias를 조회하는 읽기 전용 모듈이다. 현재 `LocalWorkCatalog`와 수집 worker에는 연결되지 않았다.

## 토픽·세부 토픽 마스터 조사 결과

`ani_rag`의 코드·설정·문서·레퍼런스와 `ani_me;nu`의 코드·설정·문서·레퍼런스를 검색했다. `ani_me;nu/src/ani_menu_agent/erd_storage.py`에는 `topic_id`와 `subtopic_id` 컬럼 정의가 있지만, 이를 채울 마스터 테이블/seed 또는 숫자 ID 교차표는 발견하지 못했다. 기존 ERD 검토 문서도 마스터와 매핑이 제공되지 않았다고 기록한다. SQLite/DB 파일은 열지 않았다.

새 ZIP에도 `topic_id`나 `subtopic_id` 필드가 없다. 따라서 사전 `entity_id`를 ERD `entity_id`로 승격하거나, `content_type`/`kind`를 숫자 토픽 ID로 바꾸지 않는다. `content.topic_id`와 `content_tag.subtopic_id`를 필수로 채우는 ERD 행을 만들려면 다음 자료가 필요하다.

1. 권위 있는 토픽 마스터: `topic_id`, 토픽 코드/이름, 사용 상태
2. 권위 있는 서브토픽 마스터: `subtopic_id`, 부모 `topic_id`, 코드/이름, 사용 상태
3. 가능하면 작품·인물·시즌 사전 `entity_id`와 실제 ERD `entity_id` 사이의 공식 교차표 또는 두 ID가 동일한 네임스페이스라는 책임자의 확인

이 자료가 확보되기 전까지 생성 초안은 `topic_label`/`content_type` 및 확인된 작품 ID를 보존할 수 있지만, ERD의 필수 토픽/엔티티 외래 키까지 충족했다고 표시하지 않는다. 토픽 ID 없이 가능한 부분인 menu_dict exact crosswalk는 별도 보존 상태로 유지하며, 사실·출처 승인이나 DB 적재는 하지 않았다.

### Backend seed 후보 확인

추가로 backend의 토픽 관련 파일을 읽기 전용으로 확인했다. 이는 기존 검색 결과의 “프로젝트 안에 토픽 자료가 전혀 없다”는 표현을 좁혀야 한다는 뜻이다. **토픽 이름/코드 후보 시드는 있지만 실제 숫자 ID의 정본 카탈로그는 찾지 못했다.**

- `backend/app/models/catalog.py`에서 `Topic.topic_id`와 `Subtopic.subtopic_id`는 `autoincrement=True`인 DB 기본키다. 숫자 값은 DB에 들어간 행과 시퀀스 상태에 따라 정해지므로 소스 코드만으로 확정할 수 없다.
- `backend/app/seeds/agent_interest_topics.json`은 `basis=provisional_content_review`이며 로컬 시연용 임시 관심사 분류라고 파일 자체가 설명한다. topic/subtopic 이름 후보일 뿐, ID 마스터가 아니다.
- `backend/app/seeds/agent_content_subtopics.json`은 49개 예전 콘텐츠별 매핑이다. `topic_code`와 텍스트 `subtopics`를 저장하며 숫자 ID를 갖지 않는다. 작품명을 subtopic으로 둔 항목도 있어 전체 분류 마스터로 간주하지 않는다.
- `backend/app/seeds/agent_contents.py`와 `backend/app/seeds/movie_catalog.py`는 DB에서 topic code/subtopic 이름을 조회하고 없으면 행을 생성한다. 이 동작은 정적 ID 목록이 아니며 이번 검토에서 실행하지 않았다.

따라서 실제 운영 DB를 열지 않고 확정할 수 있는 것은 topic code/표시 이름 후보까지다. 숫자 ID가 필요한 실행 계약을 완성하려면 DB 담당자가 승인된 DB에서 다음 필드를 포함하는 **읽기 전용 내보내기**를 제공해야 한다: `topic.topic_id`, `topic.topic_code`, `topic.topic_name`, `topic.is_active`; `subtopic.subtopic_id`, `subtopic.topic_id`, `subtopic.parent_subtopic_id`, `subtopic.subtopic_name`, `subtopic.depth_level`. 요청용 SELECT는 `docs/TOPIC_SUBTOPIC_READONLY_EXPORT.sql`에 준비했다(실행하지 않음). 별도로 RAG `content_type`과 `topic_code`의 매핑 책임자도 확인해야 한다. 이 export는 스냅샷에 넣기 전 출처 경로·내보낸 날짜·해시·갱신 책임을 기록한다.
