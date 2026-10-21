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
