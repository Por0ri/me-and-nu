# ERD 콘텐츠 데이터셋 검토

## 작품 카탈로그

- `menu_works.csv`는 작품 단위 기준표다. `menu_work_id`는 2,322개 행에서 유일하며, 여러 애니메이션 엔트리는 하나의 작품 ID로 묶인다.
- `menu_work_entries.csv`는 3,887개 작품/시즌/극장판 엔트리를 작품 기준표에 매핑한다. 같은 `menu_work_id`가 여러 행에 반복되는 것은 정상이며, 모든 행의 ID가 `menu_works.csv`에 존재한다.
- 생성 패키지는 `menu_work_ids: [대표작품ID, 관련작품ID, ...]` 순서로 기록한다. 확인 가능한 카탈로그 제목만 연결하며, 미확인 작품은 ID를 추측하지 않고 비워 둔다.
- CSV 원본은 `data/reference/`에 보관한다. 신규 작품 ID 추가·변경은 서비스의 작품 사전 소유자가 수행하고, 에이전트는 그 사전을 갱신해 참조한다. ID는 작품명이나 CSV 행 순서로 런타임에서 새로 발급하지 않는다.

## ERD와의 매핑

첨부 SQL은 `BIGSERIAL`, `JSONB`, `BYTEA`, `TIMESTAMPTZ` 등 PostgreSQL 타입을 사용하지만 식별자를 백틱으로 감싸고 있다. PostgreSQL 실행용 DDL에서는 백틱을 제거하거나 PostgreSQL의 큰따옴표 식별자로 바꿔야 한다. 아래 평가는 테이블 구조를 기준으로 한 것이며 첨부 SQL을 실행하지 않았다.

| 생성/저장 데이터 | ERD 대상 | 검토 |
|---|---|---|
| 작품별 콘텐츠 목록 | `content_entity_link` | 적합. 콘텐츠 본문 테이블에 ID 배열을 중복 저장하지 않고 관계 행으로 적재한다. |
| 대표 작품 | `content_entity_link.is_primary` | 적합하나 콘텐츠당 대표 링크가 정확히 하나임을 보장하는 부분 유니크 인덱스가 필요하다. |
| 대표 우선 작품 순서 | 현재 ERD에 없음 | `sort_order SMALLINT NOT NULL` 추가 권장. 복합 콘텐츠에서 UI/이관 순서를 보존한다. |
| 카드뉴스 페이지별 카피·이미지 | 현재 `content`에는 카드 배열 테이블이 없음 | `content_card(content_id, page_no, title, body, image_id, layout_json, ...)` 분리 권장. `(content_id, page_no)` 유니크/PK로 순서 및 중복 방지. |
| 실제 PNG 바이너리 | 현재 ERD `content.thumbnail_url`만 확인됨 | PostgreSQL 저장이 원칙이면 `content_media`에 `BYTEA`, MIME, 크기, 해시, 역할, 카드 순번을 두거나 별도 `content_card`의 `image_data BYTEA`로 보관. 현재 ERD만으로는 페이지 PNG 본문을 넣을 칼럼이 부족하다. |

### 권장 제약

1. `content_entity_link`에 `sort_order SMALLINT NOT NULL DEFAULT 1` 추가.
2. `(content_id, entity_id)` 복합 PK/UNIQUE 및 양쪽 FK 추가.
3. PostgreSQL 부분 유니크 인덱스: `UNIQUE (content_id) WHERE is_primary`.
4. 필요하면 `sort_order > 0` CHECK와 `(content_id, sort_order)` UNIQUE 추가.
5. `link_method`는 `manual`, `source`, `agent_verified` 등 허용값을 정의하고, ID 연결은 작품 사전 ID와 검증된 alias 매칭만 사용.

## 현재 에이전트 저장 방식

생성 패키지 JSON에 `menu_work_ids`를 넣고, 임시 SQLite의 `content_work_links`에 `request_id`, `menu_work_id`, `sort_order`, `is_primary`로 정규화해 저장한다. 서비스 DB로 이관할 때 각 행의 `menu_work_id`를 작품 사전과 대조해 `content_entity_link.entity_id`에 매핑하고, 첫 행만 `is_primary=true`로 적재한다. 임시 DB의 `request_id`는 서비스 `content_id`와 다른 식별자이므로 그대로 서비스 PK로 사용하지 않는다.

실제 이미지가 없는 카드의 처리 규칙은 생성 단계에서 강제한다. 관련 이미지가 있는 카드는 각각 서로 다른 원본을 사용하고, 마지막 카드만 자료가 부족할 때 1장 한정으로 절제된 텍스트 엔딩 배경을 허용한다. 표지/중간 카드 이미지가 부족하면 재수집으로 돌리며, 장식 도형은 원본 이미지 대체물로 허용하지 않는다.

## 임시 SQLite 데이터와 ERD의 실제 매핑

현재 에이전트의 임시 SQLite는 에이전트 내부 실행 저장소다. 서비스 ERD와 동일한 테이블 구조라고 간주하지 않는다. 임시 저장소에서 서비스 ERD로 옮길 때는 아래 매핑을 사용하고, 원본 JSON 기록은 변환 검증이 끝나기 전까지 보존한다.

| ERD 대상 | 현재 임시 SQLite·모델의 출처 | 이관 기준 및 빠진 점 |
|---|---|---|
| `source_site` | `config/sources.json`의 `Source`와 `agent_records`의 `source`/`trend_source` payload | `Source.id`는 이관용 source key로 보존한다. `url`, `feed_url`, `category`, `collection`, `role`, `status`, `region`, asset host는 대응 가능하다. ERD의 `topic_id`는 topic 마스터/ID 매핑을 받은 뒤 연결한다. 저작권 상태·license basis는 설정에 실제 값이 있을 때만 옮기며 추정하지 않는다. |
| `raw_document` | `persist_page_capture()`가 저장하는 `official_page_capture` payload와 원문 캡처 | 캡처 원문·URL·제목·발행/수집 시각·본문 해시를 사용한다. `SourceEvidence`는 출처 메타데이터이고 `Fact`는 추출된 주장이지 원문 전체가 아니므로, 둘을 원문 본문으로 위장하지 않는다. 캡처 본문이 없는 과거 자료는 본문 미확인으로 남긴다. |
| `event_detail` | `ContentPackage.event_*`와 확인된 행사 팩트의 `Fact.details` | 날짜·시간·지역·장소·주소·입장/가격·예약·특전 등 원문에 있는 값만 채운다. 한 콘텐츠의 여러 행사/프로그램이 별도 일정이면 이벤트 행을 분리하고, 각각의 근거를 유지한다. 현재 SQLite에는 일부 행사 필드만 별도 컬럼으로 있고 나머지는 JSON에 있을 수 있다. |
| `content` | `content_drafts`/`feed_queue`의 `package_json`, 상태·유효/보존 시각·품질 JSON | `request_id`를 서비스 `content_id`로 재사용하지 않는다. 이관용 crosswalk를 둔다. 카드 상세글, summary/keyword, 변경 공지, AI 판단 근거는 실제 값이 있을 때만 매핑한다. `embedding`은 임시 저장소에 없으므로 생성하지 않는다. |
| `content_card` | `content_cards`와 카드별 `ContentPackage.cards` | `(request_id, page)`를 콘텐츠 crosswalk와 페이지 번호로 매핑한다. `title_lines_json`, `body_json`, role, layout/overlay, image ID, 원본 이미지·출처 페이지 URL, 권리 상태, 크레딧을 분리한다. 렌더 PNG 바이너리는 `image_blobs`에서 해시 키로 찾아 카드 이미지 레코드와 연결한다. |
| `content_tag` | `content_type` 및 현재 활성 유형 레지스트리 | 유형은 콘텐츠의 대표 유형으로 보존한다. ERD `subtopic_id`에 대응하는 세부 토픽 사전과 ID 매핑은 현재 저장소에 없으므로 임의 숫자를 부여하지 않는다. 세부 태그 사전 확정 전에는 미매핑으로 둔다. |
| `entity` | `data/reference/menu_works.csv`, `menu_work_entries.csv`, 별칭·캐릭터·주제가 CSV | 작품·캐릭터·인물·음악 entity를 적재할 수 있다. CSV의 `menu_work_id`는 로컬 작품 사전 ID이고 ERD의 `entity_id`와 같다고 가정하지 않는다. 둘 사이 crosswalk를 저장하고, `external_id`/attributes에 원본 카탈로그 식별자를 보존한다. |
| `content_entity_link` | `content_work_links` 및 `ContentPackage.menu_work_ids` | `request_id`→`content_id`와 `menu_work_id`→`entity_id` crosswalk를 거쳐 변환한다. `sort_order`와 `is_primary`를 보존한다. 대표 링크는 콘텐츠당 최대 1개여야 한다. |
| `entity_relation` | 작품 시즌·프랜차이즈·상위 작품·캐릭터/주제가 레퍼런스 CSV | 관계 종류와 원본 파일/행 근거를 보존한다. 프랜차이즈 공통성만으로 별도 작품 ID를 합치지 않는다. 관계형 데이터가 없는 추론은 적재하지 않는다. |
| `content_source` | `ContentPackage.sources`, `Fact.source_ids`, `agent_records`의 source payload | 각 콘텐츠가 사용한 원문 출처를 별도 행으로 저장하고 대표 출처 여부를 보존한다. `source_site_id`와 특정 문서의 `raw_document_id`를 구분한다. 현재 source/page ID는 source-site ID와 동일하지 않을 수 있다. |
| `judgment_log` | `content_drafts.quality_report_json`, `rejected_archive`, pipeline job·agent usage 기록 | 판정·사유·시각은 실제 기록에서 옮긴다. agent/model/prompt 버전, 시도 번호, confidence, 비용이 없는 과거 기록은 `NULL` 또는 JSON 원문으로 보존하고 만들어내지 않는다. 새 검수부터 필요한 필드를 구조화해 기록한다. |

### 구현 원칙

1. 기간 에이전트의 현재 생성·수집 쿼리가 사용하는 `agent_records`, `content_drafts`, `content_cards`, `content_work_links`, `feed_queue`, `image_blobs`를 즉시 삭제하거나 이름 변경하지 않는다. 이 테이블들은 임시 실행 저장소로 유지할 수 있다.
2. ERD 적재용 구조는 현재 SQLite에서 사용할 수 있는 타입으로 만들되 ERD의 관계와 의미를 유지한다. 기존 JSON payload는 이관 결과 대조와 누락 복구가 끝날 때까지 보존한다.
3. 모든 변환은 source/document/content/entity crosswalk를 기록한다. 특히 `request_id`/`menu_work_id`를 서비스 `content_id`/`entity_id`와 직접 동일시하지 않는다.
4. 중복 실행 가능한 idempotent 변환, 행별 검증 보고서, 누락·충돌 격리 목록을 마련한다. 자동으로 매칭되지 않는 항목은 임의로 연결하지 않는다.
5. 스키마·변환 코드를 추가하더라도 기존 DB에는 명시적 백업·확인 절차 없이 실행하지 않는다. 이번 매핑 문서 검토에서는 DB·로그·워커를 열거나 변경하지 않는다.

현재 화면에서 `topic` 마스터와 전체 ERD 제약/관계선이 모두 보이지 않으므로 `topic_id` 연결 규칙은 별도 확인 대상이다. ERD 원본 DDL 또는 전체 diagram을 확보하면 키·nullable·유니크 제약을 마지막으로 대조한다.


## 2026-11-08 SQLite ERD ?? ??

- `src/ani_menu_agent/erd_storage.py`? ERD ???? SQLite ?? ???? ?? ???? ????. `AgentRepository` ?? ? ERD ???? ???, ? ????? ??? ?? ?? ???? ????? ??? ERD ????? ????.
- ?? `data/ani_menu_local.sqlite3`? ?? SQLite ?? ? ??? backfill??. legacy ?? ???? ???. ?? 91?, source_site 23?, raw_document 91?, event_detail 27?, ??? entity 21?, content 91?, content_card 331?, content_entity_link 91?, content_source 91?, judgment_log 90???. ?? ?? 0??? ??? ?? 0?, `integrity_check=ok`?.
- `raw_document.raw_text`? ?? `official_page_capture`?? ??? 90/91?? ???. ? ?? ??? ?? ??? ?? `NULL`? ??? ??? ??? ??? ???.
- `content_request_map`? ERD? ?? ?? crosswalk? `request_id`? `content_id`? ????. ?? `image_blobs`? SQLite ?? ??? ???? ???? ???? `content_card.card_image_url`?? `imageblob://<sha256>` ??? ??.
- ?? ?????? ERD? `topic_id` ? `subtopic_id` ?? ID? ???? ? ?? `topic_id`? ??? `NULL`?? `content_tag` ?? ??? ???. ID? ??? ???? ???. ?? `entity_relation`? ?? ???? ?? ????? ????? ??? ???? ?? 0???.
- ?? ???? ?? legacy ?? 1?? `judgment_log`? ?? ???? ?? 90???. ?? 90?? ?? ?? ???? ????.
- `generation_continuous_recovery_20261101.sqlite3`? ?? ??? ?? ??? ?? DB? schema/backfill? ???? ???.

- ? ????? ??? state? ?? usage ???? ???? ??/?? ????? ??? ????. ?? ???? ?? ???? ??? ?? `prompt_version`? ?? `NULL`??. ?? 90?? ?? usage ??? ???? ?? ??/?? ?? ???? ???.
