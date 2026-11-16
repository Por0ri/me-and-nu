# 작품 ID 확인과 신규 작품 등록

`ani_rag`는 작품 ID 확인에 `data/reference/` 로컬 스냅샷만 사용한다. 원본 `ani_me;nu` 경로나 DB에 접속하지 않는다.

## 매칭 순서

1. 수집 원문의 제목과 출처가 제공한 외부 ID를 작품 카탈로그의 정확 일치 규칙으로 조회한다. 확인된 별칭만 exact match에 쓴다.
2. 정확히 하나의 ID가 확인되면 해당 ID를 evidence의 작품 링크로 쓴다.
3. 미일치 또는 복수 후보이면 `LLMWorkCatalogResolver`가 원문 제목·본문, 원문 URL·출처 ID, 로컬 스냅샷과 RAG 자체 작품 카탈로그의 제한된 후보를 구조화 모델에 전달한다.
4. 기존 작품 선택은 전달한 후보 ID 중 하나만 허용한다. 새 작품 제안은 높은 확신, 원문에 실제 포함된 작품명, 원문과 정확히 일치하는 근거 인용이 모두 있어야 한다.
5. 모델 응답이 후보 밖 ID를 고르거나, 작품명/인용을 원문에서 확인할 수 없거나, 확신이 낮으면 미매칭/모호 상태를 유지한다. 식별 모델 호출이 실패해도 이미 읽은 원문은 오류 유형을 metadata에 남기고 저장한다. 자동으로 마지막 번호를 붙이지 않는다.

## 로컬 작품 ID를 검색 씨앗으로 쓰기

- `LocalWorkCatalog.work_search_seeds()`는 `menu_works.csv`의 canonical title과 확인된 별칭을 `menu_work_id` 오름차순으로 반환한다. 하위 `menu_work_entries.csv` 16개 행을 16개 검색 job으로 펼치지 않는다.
- `CollectionRequest.for_work_seed(...)`는 한 작품 ID에 대한 단일 검색 요청을 만들고 `seed_menu_work_id`를 함께 보존한다. 호출자는 씨앗을 한 번에 하나씩 순차 처리하고, 호출 제한이 정해지기 전에는 병렬 대량 검색을 하지 않는다.
- 씨앗 ID는 검색 맥락이자 LLM 후보이지 자동 연결 명령이 아니다. 원문 항목 제목·본문과 로컬 카탈로그 항목들이 함께 뒷받침할 때만 기존 ID를 선택한다.
- LLM fallback은 기존 작품 ID와 별도로 원문 항목의 `entry_format`(TV/극장판/OVA 등), `entry_relation`(본편/시즌/속편/스핀오프 등), 출처에 실제 나온 연도, 관계 근거 발췌를 반환한다. 약한 연결은 `unknown`으로 남긴다. 수집기는 이 정보를 ERD `rag_document.metadata_json`으로 전달하고, 원문 문서는 합치지 않는다.
- ANN URL의 `id` 쿼리 값은 `source_native_id`로 보존하고 `menu_work_id`와 혼동하지 않는다. 예를 들어 ANN 항목 ID 14950과 22277은 서로 다른 원문 자료이며, 둘 다 레퍼런스가 뒷받침할 때 `menu_work_id=1`에 연결될 수 있다.

### 검증 예시: `menu_work_id=1` (2026-11-08)

로컬 snapshot은 ID 1 아래 `Attack on Titan` TV(2013), `Attack on Titan Season 3` TV(2018), `Attack on Titan Final Season` TV(2020)와 별도 영화/OVA/특별편을 각각 기록한다. ANN의 세 페이지 `Attack on Titan (TV)` (`id=14950`), `Attack on Titan (TV 3/2018)` (`id=19819`), `Attack on Titan The Final Season (TV)` (`id=22277`)은 세 개의 독립 원문 문서로 남기면서, 상위 ID 1에 연결하는 판단이 snapshot 구조와 맞는다. Final Season 페이지를 snapshot의 특정 하위 항목 하나와 정확히 대응시켰다고 주장하지 않는다.

원문 URL의 숫자 ANN ID는 `external_id`/`source_native_id`로, 로컬 umbrella work ID는 별도 `menu_work_id`로 보존한다. 현재 adapter 계약은 URL에서 ANN ID를 추출하고, seeded search 요청은 `seed_menu_work_id`를 identity resolver에 전달한다. 매칭 모델은 형식과 관계를 별도 결과로 내며, collector가 `work_entry_format`, `work_entry_relation`, `work_entry_year`, 근거 발췌를 문서 metadata로 보존한다. `unknown` 분류여도 동일 work ID가 고확신으로 확인된 경우 문서 링크를 둘 수 있지만, 근거 없는 신규 ID나 자동 topic/entity ID는 만들지 않는다.

이 계약만으로 ANN이 승인되거나 작업이 실행되는 것은 아니다. 현재 원문 MCP 응답에는 리디렉션 검증 정보가 없고, 실제 LLM 모델 연결·저장소·반복 supervisor는 별도 준비가 필요하다.

## 신규 작품의 원자 등록

검증된 새 작품 제안은 `EvidenceDocument.new_work_registration`으로 저장 경계에 전달한다. SQLite RAG adapter는 하나의 트랜잭션 안에서 다음을 수행한다.

1. RAG 전용 작품 카탈로그에서 같은 이름·별칭·외부 ID가 이미 등록됐는지 재확인한다.
2. 새 항목이면 `max(로컬 reference 최대 menu_work_id, RAG 작품 카탈로그 최대 menu_work_id, RAG에 이미 연결된 최대 menu_work_id) + 1`을 지정한다.
3. 작품명·별칭·외부 ID·원문 URL·정확 근거 인용을 `rag_work_catalog`에 기록한다.
4. 같은 작품 ID가 연결된 원문·excerpt·work link와 generation backlog를 저장한다.

실패가 발생하면 트랜잭션 전체가 롤백되므로 작품 ID만 남기지 않는다. 이 로직은 코드로 작성돼 있지만 실제 DB 스키마에 적용하거나 실행하지 않았다.

## ERD 경계

새 `rag_work_catalog.menu_work_id`는 현재 작품 매칭에서 쓰는 `menu_work_id`다. ERD의 `entity.entity_id`와 같은 번호라고 간주하지 않는다. `topic_id`는 작품 제목에서 만들거나 추정하지 않으며, 권위 있는 topic/entity 매핑이 공급될 때까지 `rag_work_catalog.topic_id`에 NULL로 둔다. 따라서 이 작업은 신규 작품의 RAG ID와 원문 데이터를 함께 보존하지만, ERD `entity_id`/`topic_id`를 요구하는 downstream projection까지 해결한 것은 아니다.

`compose_runtime()`은 별도 matcher를 넘기지 않으면 생성 모델을 식별 fallback에도 주입한다. crawler만 독립 조립하는 경우 `work_match_model`을 전달해야 LLM fallback이 활성화된다. 이 기능은 MCP 연결이나 모델 호출이 가능하다는 뜻은 아니다.
