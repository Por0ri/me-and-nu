# ani_rag 구현 상태와 연결 점검

## 2026-11-09 현재 코드 대조: 카드 이미지 및 콘텐츠 경로

이 섹션은 아래 날짜별 기록의 예전 구현 상태를 보완하는 현재 파일 기준 기록이다. 아래 기록 중 일부는 작성 당시의 상태를 설명하므로 현재 구현 상태로 읽지 않는다.

- `agents/content_generation/renderer.py`는 카드별 텍스트 없는 PNG와 별도 HTML 카피/레이아웃 데이터를 만든다. 이미지 전체를 보존해 배치하고, 같은 이미지의 확대 블러를 배경으로 사용하며, 이미지에서 계산한 어두운 팔레트와 부드러운 가장자리 페더를 적용한다. 기존 기간 에이전트의 blur/contain 계열 표현을 RAG 전용 타입으로 포팅했다.
- `model_services.py`는 카드마다 서로 다른 등록 이미지 후보를 선택하도록 구조화 출력 검증을 하고, `layout_director.py`가 페이지 카피와 실제 이미지 미리보기를 vision 모델에 보내 관련성을 확인한다. 불확실한 연결, 읽을 수 없는 이미지, 작품 ID 미매칭은 실패 처리되어 생성 큐에서 차단된다. 이 검사도 이미지 속 인물 신원을 자동 확정하는 보장은 아니다.
- `GenerationWorker`는 작품 연결 이미지 후보가 3개 미만이면 대기/차단하고, 카드 PNG와 HTML용 카피·레이아웃을 저장한 뒤 별도 검수 큐로 보낸다. 검수 worker는 저장된 페이지 이미지와 임시 카피 오버레이를 vision 모델에 보낼 수 있다.
- 이미지 entity 연결을 위한 데이터 계약 및 SQLite 저장 경계는 있으나, 현재 image candidate 수집기들이 모든 이미지에 근거가 있는 character/entity ID를 자동 연결해 주지는 않는다. 감독·성우·생일 등 여러 작품을 묶는 curation fan-out/publisher도 미구현이다.
- `entity_linking.py` now offers an optional injected authoritative entity-catalog match on URL and structured collection paths. It attaches character links only for a unique exact external-ID or work-scoped alias match plus a source chunk; ambiguous/unresolved cases stay unlinked. No entity catalog is bundled or read automatically.
- `curation.py` now proposes deduplicated, provenance-complete director-work, voice-actor-character, and shared-birthday groupings from caller-supplied resolved facts. It preserves evidence/chunk IDs, source URLs, work/entity IDs, item count, and source publication window. It does not extract these facts from documents, persist the plans, map documents to an additional content type, or enqueue generation; those adapters remain the next integration step.
- ANN/Wikidata/Kitsu 등 승인 출처 7개는 설정상 모두 비활성(`is_collecting=false`)이다. schema 초안의 추가 ERD 관계는 실제 DB에 적용되지 않았다. MCP 호출, DB, 모델, worker는 이번 확인에서 실행하지 않았다.

## 2026-11-08 작품 ID 씨앗 검색과 ANN 관계 분류

- 로컬 snapshot의 `menu_work_id=1`(진격의 거인)으로 Firecrawl MCP 검색을 1회, 검색 결과 원문 3개를 각각 읽었다. ANN entry IDs 14950 (`Attack on Titan (TV)`), 19819 (`Attack on Titan (TV 3/2018)`), 22277 (`Attack on Titan The Final Season (TV)`)는 서로 다른 원문 자료이며 local snapshot에 각 시즌/형식이 ID 1 아래 따로 등록돼 있어 같은 umbrella ID 연결이 타당하다.
- 페이지 본문은 각각 약 16.5만~42.5만 자였다. ANN `id` URL 파라미터는 각 문서의 `source_native_id`로 보존한다. 세 응답 모두 redirect chain이 비어 있어 `redirects_verified=false`; `ann_encyclopedia` 승인 설정은 추가하지 않았다.
- Firecrawl 공식 Scrape 응답 스키마는 `metadata.sourceURL`과 `metadata.url`을 제공하지만 redirect-chain 필드나 두 URL의 redirect 증명 의미를 문서화하지 않는다. 그래서 현재 응답만으로 전체 redirect 경로를 승인 조건에 맞게 입증할 수 없다 ([Scrape API schema](https://docs.firecrawl.dev/api-reference/endpoint/scrape)).
- `LocalWorkCatalog.work_search_seeds()`는 canonical title 및 verified aliases로 ID 오름차순 검색 씨앗을 제공하고, `CollectionRequest.for_work_seed()`는 한 ID에 대한 단일 요청과 `seed_menu_work_id`를 구성한다.
- `LLMWorkCatalogResolver`는 seeded ID를 후보로 포함하고, 별도 `entry_format`/`entry_relation`/근거 발췌를 반환하도록 계약을 확장했다. `collect_from_source()`는 이 결과를 기존 `rag_document.metadata_json`에 기록한다. 원문 문서와 ANN ID는 서로 합치지 않는다.
- ANN URL의 숫자 `id`를 source-native external ID로 추출하도록 Firecrawl adapter를 보강했다. 현재 exact title resolver는 `(TV)` 접미사가 붙은 ANN 제목을 직접 맞추지 못하며, seed context는 자동 확정이 아니라 LLM 후보 근거로만 전달한다.
- 제한 검색 결과 3개는 이번 MCP 요청에서 지정한 `limit=3`이다. ANN UI의 검색 결과 페이지네이션을 검증한 것은 아니다. MCP 응답도 rate limit을 확정할 만큼 충분한 요청을 하지 않았다.
- Python 소스 5개는 구문 수준으로 확인했다. 사용자가 요청한 1회 스모크 테스트는 임시 stub model로 로컬 snapshot ID 1, 검색 seed 요청, ANN ID 14950/19819/22277의 ID 매칭 및 `tv`·`main/season`·연도 분류 계약을 검증했고 통과했다. 자동화 테스트 파일은 추가하지 않았다.
- 실제 LLM API 호출, repository/DB write, worker, CLI, migration은 실행하지 않았다. `sources.evergreen.json` 및 기존 `ani_me;nu` 파일은 수정하지 않았다.

## 2026-11-08 생성·검수·실행 구조 업데이트

현재 사용자가 볼 수 있는 쉬운 설명은 [`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md)에, 기간 프로젝트 대비 포팅 범위는 [`docs/GENERATION_REVIEW_PORT.md`](docs/GENERATION_REVIEW_PORT.md)에 정리했다. `README.md`는 최신 구현 상태로 고쳤다.

- `model_services.py`: retrieval-evidence-bound 구조화 초안 생성, 독립 review, OpenAI Responses transport를 구현했다. API key/model을 주입해야 하며 호출하지 않았다.
- `runtime.py`: 이미 열린 별도 RAG DB connection에 SQLite adapter, generation/review worker, queue reconciler를 조립한다. 연결을 열거나 schema/DDL을 실행하지 않는다.
- `workers.py`: 카드별 서로 다른 작품 연결 이미지가 최소 3개 있어야 초안을 만들고, PNG 및 HTML overlay 데이터를 저장한 뒤 검수 큐로 넘긴다. 이미지가 부족하면 보류한다. 생성 단계에서는 독립 검수를 실행하지 않는다.
- MCP: project-local `firecrawl` and `wikidata` aliases are configured in `config/mcp_servers.json`; both live inventories were returned by `StreamableHTTPMCPClient`. `FirecrawlMCPAdapter` completed one official-page search and one Frieren character-page read. ANN now has a narrowly scoped approved registry entry; automatic collection remains disabled. Exact no-redirect URL equality was confirmed on one direct read, while general redirect-chain support is accepted only when a complete in-host chain is supplied.
- Public endpoint inventory: 2026-11-08 direct calls through the project transport confirmed Firecrawl and Wikidata tools. The Firecrawl adapter ran one host-restricted search and read one bounded page; no Wikidata statement call occurred.
- 이미지(2026-11-08 당시 기록): 그 시점에는 image search/download가 연결되지 않았다. 2026-11-09 현재 코드 대조 결과는 문서 맨 위의 현행 섹션을 따른다.
- 검수 기준: 캐러셀 3~10장, 표지/마무리 순서, 카드 제목·본문의 분량과 비어 있지 않은지 확인한다. 저작권·이미지 권리·사실성 점수는 현재 통과 차단 조건이 아니다.
- 작품 매칭: `catalog.py`의 로컬 exact match가 실패하면 `LLMWorkCatalogResolver`가 원문과 제한된 후보를 모델에 보낸다. 기존 후보 ID 선택 또는 정확 인용문과 원문 내 작품명이 있는 새 작품 제안만 허용하며, 불확실하면 미매칭으로 둔다.
- 신규 작품 저장: `SQLiteRagStore`가 Kitsu ID 기반 또는 정규화 제목 CRC32 기반의 안정적인 임시 ID를 계산한다. 정식 `catalog/`는 읽기 전용이며, 작품 행은 공유 `ani_me;nu/data/reference/agent_works.csv`에 기록한다. RAG DB에는 작품명·별칭·외부 ID·원문 근거 연결, evidence 및 generation backlog를 저장한다. 이 `menu_work_id`는 ERD `entity_id`와 별개이며, authoritative `topic_id` 연결은 아직 비어 있다.

정적 AST 검사에서 `src/ani_rag`의 Python 파일 33개를 파싱했고 구문 오류와 `ani_menu_agent` import는 0개였다. 테스트, API, MCP, DB, CLI, worker는 실행하지 않았다. DB/WAL/log/generated outputs와 `.env`는 읽지 않았고 `ani_me;nu`도 수정하지 않았다.

2026-11-07에 코드와 문서를 다시 대조했다. `ani_rag` 대상 파일은 시작 시 모두 미추적 파일 상태였고, 기존 `ani_me;nu` 상태 항목은 189개로 확인됐다. 이번 작업에서 `ani_me;nu`는 읽기 전용으로만 살펴봤다. `.env`, DB, WAL/SHM, 로그, 생성 결과는 열지 않았다.

## 모듈별 실제 상태

| 모듈 | 분류 | 실제 동작 / 빠진 부분 |
|---|---|---|
| `adapter.py` | Protocol/계약 | URL 검색·읽기 요청, 응답 출처/도구 이름, 허용 호스트, redirect 검증 요구를 정의한다. MCP 전송·도구 응답 parser는 없다. |
| `mcp_transport.py`, `firecrawl_adapter.py` | MCP transport + 제한된 구현 | project-local Streamable HTTP client가 initialize/tools/list/tools/call을 처리한다. Firecrawl adapter는 `includeDomains`와 exact-host result filter를 사용한 제한 검색, 단일 scrape를 지원한다. Redirect chain이 없으면 `redirects_verified=false`를 반환한다. Wikidata structured parsing은 미구현이다. |
| `catalog.py` | 로컬 exact match + LLM 보조 구현 | exact title/external ID/verified alias를 먼저 확인한다. 실패하면 원문을 포함해 제한된 기존 후보를 LLM에 보낸다. 모델이 ID를 직접 만들 수 없고, 기존 후보 이외 ID 선택과 원문 근거가 불충분한 새 작품 제안은 거부한다. |
| `collector.py` | 일부 포팅, 전송·저장 미구현 | 승인 출처만 검색/읽고 응답 source/server/tool attestation, URL host, redirect 결과를 확인한 뒤 EvidenceDocument를 만든다. 실제 MCP와 repository는 주입해야 한다. |
| `structured_mcp.py` | 응답 계약/정규화 경계 | entity/property 결과에서 item ID, property, value, qualifier, reference를 구조화 근거로 보존한다. Wikidata 전용 transport/response parser는 없다. |
| `models.py`, `profiles.py` | 독립 구현 완료(데이터 계약) | 7개 유형, 원문/출처/시각/언어, 근거 유형, 리뷰·큐레이션 context, work-link 상태와 출처 근거를 갖춘 미확정 신규 작품 후보를 검증한다. |
| `interfaces.py` | Protocol만 있음 | work resolver와 evidence upsert+generation backlog 원자 기록 계약. |
| `settings.py` | 로컬 설정 검증 구현 | 승인 상태, 유형, exact host, search/read 바인딩을 검사한다. 승인 출처에는 host와 각 도구 바인딩이 필수다. 실제 서버 목록 조회는 adapter Protocol에 위임한다. |
| `retrieval.py` | 검색 일부 구현, DB 후보 조회 adapter 연결 가능 | 문서를 source locator를 가진 chunk로 나누고 lexical overlap으로 후보를 재정렬한다. SQLite adapter가 정형 후보를 읽지만 FTS/vector 검색과 임베딩은 없다. |
| `content.py`, `prompts.py`, `model_services.py` | 독립 구현(생성·검수 호출) | 구조화 LLM 출력으로 카드와 상세글을 생성하고 claim에 evidence/chunk ID를 연결한다. OpenAI Responses 어댑터가 있지만 key/model을 caller가 넣어야 하며 실제 호출/usage 기록은 하지 않았다. |
| `quality.py` | 코드 기반 최소 검수 | reviewer에서 캐러셀 장수, 첫 표지/마지막 마무리, 제목·본문 길이와 공백 여부만 가독성 이슈로 돌린다. 콘텐츠 생성 중에는 호출하지 않는다. |
| `rendering.py`, `agents/content_generation/renderer.py` | 일부 구현 | 로컬 이미지 전체를 담은 텍스트 없는 PNG에 same-image blur 배경, 이미지 기반 팔레트, 페더 가장자리를 적용하고 HTML overlay 데이터를 별도로 반환한다. 이미지 획득은 `image_ingest.py`; 페이지별 image/copy 시각 판정은 vision 기반 `layout_director.py`가 담당한다. 저작권/권리 사실 확인은 하지 않는다. |
| `review.py`, `model_services.py` | 독립 구현(최소 검수/라우팅) | reviewer 모델이 pass/revise/reject를 고르고, 결정론적 가독성 이슈는 revise 지시로 보완한다. 점수표, 사실성, 이미지 권리는 현재 통과 차단 기준에서 제외한다. |
| `queues.py` | 상태 전이 구현, 저장 adapter 있음 | 세 큐 상태와 retry 제한을 정의한다. SQLite core가 claim, idempotent enqueue, 상태 이력 저장을 구현한다. |
| `workers.py`, `runtime.py` | one-item orchestration + 독립 연속 loop 구성 | 세 역할의 `run_forever(stop_event=...)`와 개별 compose factory가 있다. SQLite 저장소와 MCP/모델 서비스를 주입한다. CLI와 프로세스 감독기는 없다. |
| `src/ani_rag/storage/schema.sql` | 스키마 초안만 있음 | 원문·excerpt·work link·신규 RAG `menu_work_id` catalog·chunk·retrieval trace·claim/card/image·generation backlog·queue·review 이력을 설계한다. 실행/적용하지 않았다. |
| `src/ani_rag/storage/sqlite_core.py` | 독립 adapter 구현, 실행 안 함 | 주입된 connection에서 승인 source 등록, 새 작품 카탈로그 ID 발급, 신규 작품 레코드·근거·generation backlog의 원자 저장, 후보 문서 조회, backlog와 queue를 제공한다. DB 열기·DDL 실행은 하지 않는다. |
| `src/ani_rag/storage/sqlite_workflow.py` | 독립 adapter 구현, 실행 안 함 | retrieval trace·chunk·draft·claim·review history 저장과 재구성, 출처/권리 정보를 갖춘 이미지 metadata 조회를 제공한다. 이미지 검색·다운로드나 권리 사실 확인은 하지 않는다. |
| `__init__.py` | 패키지 골격 | 자동 실행 코드가 없다. |

## 요청한 11개 확인 질문

1. URL형/구조화 MCP 코드는 **응답 계약과 정규화 흐름만** 있다. 외부 서버와 통신하는 프로젝트 parser/transport는 없다. 공개 endpoint의 도구 목록은 별도 1회 조회로 확인했으나, 실제 검색/읽기를 처리하는 구현은 여전히 없다.
2. `mcp_transport.py` now provides a project-local Streamable HTTP client; both configured aliases returned live tool inventories.
3. `settings.py`와 collector는 설정 도메인, 결과 host, server/tool 이름을 비교한다. Firecrawl adapter는 검색에 허용 도메인을 전달하고 반환 URL도 exact-host filter로 걸러낸다. 읽기는 Firecrawl이 redirect chain을 반환하지 않으면 `redirects_verified=false`로 남기므로 collector가 저장을 거부한다.
4. `catalog.py`는 snapshot CSV를 직접 읽는다. `menu_works`, entries의 제목, external entries, `verified` aliases를 쓴다. external ID나 title이 여러 work ID로 이어지면 `ambiguous`; 결과가 없으면 `unresolved`다. `menu_work_parent_overrides`와 franchise 관계는 ID 확정에 쓰지 않는다. parent/franchise만으로 work ID를 추론하지 않기 때문이다.
5. `SQLiteRagStore`가 `EvidenceRepository`, `QueueRepository`, generation backlog, candidate retrieval 계약을 구현한다. `SQLiteDraftRepository`, `SQLiteReviewHistoryRepository`, `SQLiteImageReader`도 저장 Protocol을 구현한다. `LLMWorkCatalogResolver`는 로컬 exact match가 실패할 때만 주입 모델을 호출한다.
6. Generation worker는 retrieval request로 `EvidenceRetriever`를 호출한다. SQLite core가 source/type/work-link/date 기준 후보 문서를 읽고 backlog와 queue를 저장하며, 워크플로 adapter가 retrieval/draft/review/image metadata를 저장한다. 아직 DB를 연결해 실행하지 않았다.
7. chunking과 lexical re-ranking은 구현했다. SQLite core는 관계형 후보 문서만 조회한다. FTS/vector 검색, 임베딩 생성/저장, vector index는 없다. 원문 문서가 정본이고 retrieval chunk ID를 초안과 claim에 남긴다.
8. `GenerationService`와 `IndependentReviewer`는 `model_services.py`의 실제 구현을 갖는다. OpenAI Responses adapter가 strict JSON Schema 응답을 요청하고, 로컬 parser가 ID/link와 review 상태를 확인한다. 실제 모델 호출/usage 기록은 없고, API 장애 retry는 worker의 일반 큐 재시도에 맡긴다.
9. `agents/content_generation/renderer.py`는 등록 이미지마다 카드별 텍스트 없는 PNG를 만들고 HTML overlay용 copy/layout manifest를 별도로 둔다. 새 초안은 이미지가 필수이며, 작품에 연결된 후보가 3개 미만이면 worker가 보류한다. 이미지 검색/다운로드 코드는 별도 ingest 모듈에 있고, 이미지 권리 사실 확인은 하지 않는다.
10. `run_once()`는 one-item 단계로 유지되고 세 role agent가 `run_forever(stop_event=...)`로 queue-empty 대기/polling을 제공한다. stop event로 개별 루프를 중단하고 새 event로 재시작할 수 있다. CLI와 프로세스 감독기는 없다.
11. README와 현황 문서를 아래처럼 갱신해 “검색 로직 일부 있음”과 “저장소/실제 end-to-end 없음”을 나눴다. MCP 계획은 alias/tool 이름이 제안일 뿐임을 유지한다.

## 기존 프로젝트를 읽기 전용으로 대조한 결과

- 기존 crawler `base.py`는 LangGraph, discovery, 이미지 curate, `ani_menu_agent` 모델/source registry/storage/catalog에 결합돼 있어 복사하지 않았다. 7개 프로필은 도메인 규칙만 참고했다.
- `generation.py`의 `generate_copy_deck()` 경로는 `AgentRepository()`와 `ChatOpenAI`를 사용한다. reviewer는 별도 graph지만 `reviewer/agent.py`에서 `AgentRepository()`와 legacy state/model을 사용한다. 호출하지 않았다.
- 기존 정책의 `max_review_failures`는 4다. RAG 초기 revision cap은 이 수치를 참고값으로 유지하며 별도 확인이 필요하다.
- 기존 Pillow renderer는 `ani_menu_agent.models` 타입을 직접 import한다. 그대로 복사하지 않았다.
- 기존 레퍼런스의 snapshot 날짜·해시는 `data/reference/SNAPSHOT_MANIFEST.md`에 있다. character/song CSV는 identity lookup 보조일 뿐 콘텐츠 근거로 쓰지 않는다.

## 현재 코드가 표현하는 흐름과 끊긴 곳

```text
출처 승인 + 실제 MCP adapter
  → URL/structured 수집 → local ID exact match, 실패하면 source-bound LLM identity decision → 신규 작품이면 ID·작품 레코드·원문·generation backlog를 한 transaction에 기록
  → GenerationQueueReconciler가 stable dedupe key로 generation queue에 전달
  → GenerationWorker가 relational FTS/optional vector 후보를 retrieval port로 조회
  → chunk + lexical rerank → generation provider → claim/evidence/chunk/image 게이트
  → ReviewWorker → 독립 reviewer → ready / generation revise / blocked evidence request / archive
```

위는 인터페이스와 SQLite adapter로 표현한 흐름이다. RAG DB connection/schema는 준비되지 않아 실제 실행하지 않았다. DB 포트 계약은 원문 저장과 backlog row를 같은 transaction에 기록하고, dispatcher는 enqueue 성공 후 backlog를 확인 처리하도록 정했다. 따라서 DB 어댑터 구현 시 중단 복구와 중복 enqueue는 stable dedupe key로 멱등 처리해야 한다.

## 실행 차단 조건과 미결 결정

- `sources.evergreen.json` has three narrowly approved sources: ANN for work-ID-seeded `guide`/`people`, Wikidata as a `character` identity/relationship lead, and Kitsu for filtered `guide`/`character` catalog lookup. Both have `is_collecting=false`; no recurring plan or worker is enabled. ANN approval relies on one verified no-redirect page; Wikidata?s structured runtime adapter is not implemented.
- DB/queue 구현과 운영 제품은 미선택이다. 현재 SQL은 SQLite 호환 설계 초안이며 어떤 DB에도 적용하지 않았다. 공통 downstream DB/queue와 연결 방식도 결정되지 않았다.
- generation/review 및 작품 식별 structured model service는 구현됐지만 자격증명/모델 설정과 실제 호출은 없다. 이미지 후보 수집/다운로드 코드와 PNG 렌더링은 구현돼 있으나 이번 작업에서 실행하지 않았다. 권리 사실 확인은 없고, rendered artifact 저장 경로는 미적용 RAG schema에 연결돼 있다.
- review는 캐러셀 가독성만 차단 기준으로 삼는다. 모델/서비스 장애 대응과 재시도 후 수동 처리 정책은 남아 있다.
- 따라서 지금은 실제 자료를 모아 초안을 생성·검수하는 end-to-end 실행이 불가능하다. 출처 JSON에 항목을 추가하는 것으로 해결되지 않는다.

## 2026-11-08 generation/review service port

`src/ani_rag/model_services.py` now implements structured, evidence-bound generation, a separate model reviewer, and an OpenAI Responses API transport. The worker interfaces are still one-item `run_once()` steps and receive services through dependency injection. See [`docs/GENERATION_REVIEW_PORT.md`](docs/GENERATION_REVIEW_PORT.md) for the comparison with the period agent.

This fills the previously missing GenerationService/IndependentReviewer implementations. It does not make the system runnable unattended: no credentials/model were configured or called; ANN, Wikidata and Kitsu approvals are narrowly scoped and no recurring collection plan exists; Wikidata and Kitsu structured collection still need concrete adapters; a DB connection and repository/queue composition must be supplied; registered image candidates are required; visual preview review depends on renderer output and vision-capable model support. PNG persistence is wired to the RAG staging schema but remains unapplied. No MCP, worker, DB, CLI, migration, or test was run.

## 2026-11-08 dataset ERD alignment

The supplied ERD screenshot is now reflected in the RAG data contract. The table-by-table mapping and unresolved identity decisions are in [`docs/ERD_DATASET_MAPPING.md`](docs/ERD_DATASET_MAPPING.md).

- The temporary relational schema now carries source-site metadata, raw payload and parse state, draft topic/summary/keywords/sponsor/notice/validity fields, content-source/tag/entity links, card image provenance and rights fields, ERD-shaped entity/relation tables, and judgment logs.
- SQLite persistence references were aligned to the revised document title/URL/fetch-time and card page/title columns. Evidence is linked to draft sources, and review results are recorded in the judgment log.
- No `topic_id`, `subtopic_id`, or `entity_id` is inferred without an authoritative catalog and explicit mapping. `menu_work_id` is not treated as `entity_id`. Missing numeric taxonomy IDs do not block evidence or draft generation; they must be resolved when projecting to ERD rows that require those foreign keys.
- The source allowlist has narrow ANN, Wikidata, and Kitsu approvals plus one non-executable Frieren draft. No database application, worker, CLI, or migration was performed in the source-review follow-up.
- Current local snapshot counts are 2,351 works and 3,889 entries. Older review counts of 2,322 and 3,887 are stale; details are recorded in the mapping document.

## Read-only recovery DB comparison (2026-11-08)

The user-designated recovery SQLite database was inspected read-only for schema, aggregate dataset shape, JSON field names, and key joins. It has 10 content type codes, 146 drafts, 717 cards, 146 work-link rows and 28,433 generic agent records. All draft/card, draft/work-link and feed/draft request joins matched; all draft content types were registered. `menu_work_id` coverage against the local work snapshot was 96/98 distinct IDs; 2403 and 2416 remain unresolved. The DB contains no ERD-normalized source, raw-document, entity, content-source, tag or judgment-log tables and declares no foreign keys. No prose/image bytes were printed or semantically reviewed; the DB was not modified.

RAG now retains the legacy free-text topic label as `topic_label` separately from nullable ERD `topic_id`. Existing content type and menu work IDs remain preserved; no numeric IDs are fabricated.

## 2026-11-08 one-item MVP wiring and ERD projection

- `runtime.py::compose_runtime()` now composes the existing collection, structured collection, generation, review, and queue-reconciliation workers from caller-supplied dependencies. It does not open storage, apply the SQLite schema, connect to MCP, call a model, or start work.
The project has a concrete bounded Firecrawl read adapter. Wikidata and Kitsu live tools/endpoints were reviewed, but their concrete structured runtime adapters remain unimplemented. ANN is approved for bounded work-ID-seeded `guide`/`people`; Wikidata for `character` identity/relationship leads; Kitsu for filtered `guide`/`character` catalog lookup. No recurring plan is configured.
- No separate ERD conversion stage is planned. The dataset must be aligned when saved. The current `rag_*` staging schema contains many ERD fields and relationships but still uses draft IDs and permits missing topic/subtopic/entity links; it does not yet guarantee a complete ERD-shaped content record.
- The one-item and opt-in continuous paths are represented in code, but they are not runnable until collection is explicitly enabled with a recurring plan and concrete MCP adapters exist, a separate RAG SQLite database has been provisioned with the schema, structured model clients are configured, and at least three registered rights-documented image candidates are available. The reviewer can inspect temporary page previews when a vision-capable model is injected; missing previews are non-blocking under the minimum review policy.
- ERD materialization is still blocked by the absence of authoritative topic/subtopic/entity crosswalks and a canonical `content_id` allocation rule. Those values must be supplied before projection; local work IDs are a separate namespace.

Only `ani_rag` code and documentation were changed in this step. No `.env`, DB/WAL, log, generated output, MCP endpoint, model API, worker, CLI, migration, or test was accessed or run. `ani_me;nu` remains read-only.

## 2026-11-08 downstream parity port

The user clarified that the collection/evidence lane may differ, but the downstream me;nu creation and independent-review behavior must be shared. The earlier RAG-only generic prompt was insufficient; the current files now port the established voice and workflow while preserving RAG evidence IDs.

- Role folders: `src/ani_rag/agents/mcp_crawler/`, `content_generation/`, and `reviewer/` each contain an agent entry point and role documentation. Shared storage/queue/retrieval primitives remain outside those role packages.
- Generation: copy deck and the “자세히 보기” article are separate structured model calls. The deck records editorial angle and distinct cover/ending modes; each factual claim must cite retrieved evidence and chunks. Prompts carry the existing Korean anime-fan friend voice and all seven RAG type distinctions.
- Layout/rendering: `layout_director.py` makes one image-aware background/emphasis decision per page when local artwork and a vision-capable model are available. Copy remains in the lower HTML region. `render_text_free_cards()` creates a separate 1308×1632 PNG plate for each page, preserves the full source image, and returns the copy plus normalized image/text boxes separately. The worker persists each PNG and manifest to `rag_rendered_card` before review.
- Detail output: the body remains distinct from cards. `assemble_detail_article()` orders article, source section, and the current `ani_me;nu` AI-use notice: “이 콘텐츠는 원문과 확인된 자료를 바탕으로 AI를 활용해 생성·정리했습니다.” The application/frontend still must read and display this assembled detail field.
- Review: the independent reviewer checks carousel order and copy readability, using temporary per-page copy-overlay previews when available. Only readability issues trigger revision instructions in the current pilot. Copyright, image rights, and factual-support scores do not block; generation does not invoke review.
- Port provenance and exact source file hashes are listed in [`docs/PORT_MANIFEST.md`](docs/PORT_MANIFEST.md). Existing `ani_me;nu` source files were read-only and no `ani_menu_agent` imports were added.

a bounded Firecrawl read adapter exists but rejects unverified redirects

## 2026-11-08 independent continuous role loops

- Each role has an independent opt-in `run_forever(stop_event=...)` polling entry point. Empty queues wait for two seconds by default; setting the event stops only that loop. Nothing starts during import or composition.
- `compose_crawler_agent()`, `compose_content_generation_agent()`, and `compose_reviewer_agent()` create one role at a time so separate processes can use separate connections to the same dedicated RAG database.
The registry has narrowly approved ANN, Wikidata, and Kitsu sources plus one draft; recurring plans remain empty and both approvals have `is_collecting=false`, so no collection will occur until it is explicitly enabled and planned.
- The content-generation loop reconciles DB generation backlog rows into the generation queue, processes drafts, and queues them for review. The reviewer loop independently consumes completed drafts.
- The loops were added but not started. No DB, MCP, model, CLI, migration, or test was run.


## 2026-11-08 ANN source approval follow-up

`ann_encyclopedia` is approved in `config/sources.evergreen.json` for bounded, work-ID-seeded reference lookup under the exact host `www.animenewsnetwork.com`, content types `guide` and `people`. Each ANN entry remains its own raw source document and its URL `id` is retained as `source_native_id`. The source is not scheduled: `is_collecting=false`, no recurring plan is configured, and no worker was started.

The Firecrawl scrape schema documents `metadata.sourceURL` and `metadata.url` but not a redirect chain ([Scrape API schema](https://docs.firecrawl.dev/api-reference/endpoint/scrape)). The adapter accepts exact requested/sourceURL/url equality as a verified no-redirect case; otherwise it requires a complete in-host redirect chain. A single direct read of ANN id 14950 satisfied the no-redirect rule. This does not establish redirect safety for every ANN URL, Firecrawl?s rate limits, or unattended collection readiness.


## 2026-11-08 bounded ANN adapter acceptance check

A live test through the project adapter confirmed the Firecrawl tool inventory, one local-catalog-seeded search for work ID 1 (`Attack on Titan`), three ANN results with distinct native IDs (14950, 22277, 19819), and one successful read of id 14950. The read returned a body, evidence excerpt, attribution metadata, exact allowed host, preserved native ID, and verified no-redirect URL equality. The body was 424,511 characters, so downstream chunking/storage bounds are still needed before sustained collection. ANN remains approved only for work-ID-seeded `guide`/`people` reference lookup. No database write, model call, recurring plan, or worker execution occurred.


## 2026-11-08 bounded Wikidata source review

Live tool inventory exposed `search_items`, `search_properties`, `get_statements`, and `get_statement_values`. Using local work ID 1's title `Attack on Titan`, item search returned anime-series QID Q22126305. Property search identified P674 (`characters`); statement details returned character QIDs, qualifiers, and references. The inspected reference URLs were imported from a Wikipedia cast-and-characters page. Wikidata is therefore approved only as a character identity/relationship lead, with corroboration required.

The allowlist records `search_items` and `get_statement_values`, but `StructuredMCPAdapter` remains a Protocol with no concrete Wikidata transport/parser integration. This is a policy/source approval, not a runnable collector. The source is non-collecting, has no recurring plan, and no worker or database write occurred.


## 2026-11-09 bounded Kitsu source review

A local work-title-seeded Kitsu JSON:API request through the Firecrawl scrape tool returned Attack on Titan anime ID 7442 and included character resources/relationships. Firecrawl web search was not reliable for this API and returned an irrelevant result. Kitsu is approved only for filtered `guide`/`character` catalog reference lookup; curation/ratings, synopsis reproduction, and image use are excluded. The response included a MAL Rewrite attribution in synopsis text, and a broad include exceeded 220 KB. API docs specify pagination with a maximum page limit of 20, but no safe recurring rate was confirmed. No recurring plan or worker is enabled. The concrete Kitsu JSON:API adapter/parser remains unimplemented.


## 2026-11-09 image collection requirement

The user confirmed that available images from approved sources should be collected and linked to local work IDs for generation. `ImageAsset` and the unapplied RAG schema preserve source page/native ID, role, local path, hash, media type, size, dimensions, and work link. The explicit downloader checks HTTPS and exact source-specific media hosts, bounds bytes/pixels, validates image content, and tries candidates serially. No image was downloaded in this change; no worker, database write, or migration was run.


## 2026-11-09 source image paths connected

- Kitsu Firecrawl reads parse returned JSON:API and extract every distinct included cover/character image-size URL. ANN image extraction retains only encyclopedia artwork URLs whose CDN path contains the exact read entry ID; unrelated page icons/widgets are filtered out.
- Structured Wikidata collection extracts requested P18/P154 values, converts Commons filenames to `Special:FilePath`, then follows only HTTPS redirects to the separately allowlisted `commons.wikimedia.org` / `upload.wikimedia.org` hosts. The structured worker and runtime now receive the same image repository and asset directory as URL collection.
- The explicit image ingestor tries each candidate. A failed image is recorded with its URL and exception type on the raw evidence payload, while successful images are registered with SHA-256 and the confirmed local work ID. Duplicate asset and link inserts are idempotent.
- All three source records remain `is_collecting=false`; this change does not start MCP, a worker, or bulk downloads. Wikidata character image coverage depends on P18 being present in the returned statements; the bounded prior sample did not establish that every entity has an image.


## 2026-11-09 one-work Kitsu collection pilot

- Applied the SQLite-compatible schema only to the newly created `data/ani_rag_pilot.sqlite3`; the existing `ani_me;nu` database was not opened. Registered Kitsu in that pilot database and collected one Kitsu anime record selected from local work ID 1 (`Attack on Titan`).
- Firecrawl returned one Kitsu record (native ID `7442`) and 364 distinct image URLs. The local catalog matched the title to work ID 1. All 364 candidates were fetched sequentially with a 250 ms inter-request pause; one first attempt timed out and succeeded on a single retry.
- The pilot DB has one raw document, one matched work link, 364 image asset rows and 364 work links. Content hashing produced 362 distinct local image files (identical bytes share a hash-named path). All linked files exist and match their stored SHA-256 hashes. The document records 364 registered images and no remaining failed candidates.
- The collector also created one pending generation backlog row. No generation/review worker or model was run; the review queue is empty. Source configuration still has `is_collecting=false`, so this was a single explicit request, not a recurring run. `data/image_assets/` and the pilot DB are local pilot artifacts.
- The pilot exposed a prompt import error from unescaped literal braces in two f-strings in `agents/content_generation/prompts.py`; those braces are escaped so the package imports. No tests were added or run.


## 2026-11-09 FlyingDog music source review

- Firecrawl tool inventory exposed `firecrawl_search` and `firecrawl_scrape`. A host-filtered search on `www.jvcmusic.co.jp` returned six results; the public new-release page and a linked Walküre / TV anime Macross Delta release detail both read successfully with verified same-host URLs.
- The release detail preserved the explicit Macross Delta tie-in, Walküre artist, release/catalog code `VTCL-35395`, release date 2027-03-24, track titles and same-host jacket images. The release listing showed other multi-work entries too. The adapter now extracts FlyingDog catalog IDs from detail/list URL paths.
- `flyingdog_music` is added as an approved primary music source with exact host and media host `www.jvcmusic.co.jp`. It remains `is_collecting=false`; no recurring frequency/rate ceiling has been established. No image was downloaded, no database was changed, and no worker ran in this source review.
- Search result publication time was not supplied as metadata; the release date and article/news dates are preserved in the original body. Search snippets alone are not promoted as evidence.


## 2026-11-09 timed pilot follow-up

The ten-minute Kitsu pilot used an in-memory assistant response bridge; it made no model API calls. One MCP search/read completed and returned an already stored canonical document. Generation and independent review completed for one new four-card draft; the second scheduled collection was still in flight at the time limit and remains blocked for an explicit retry.

The pilot exposed repeated image downloads as a likely collection cost: the current ingester downloads and hashes a candidate even when the exact source URL already has a valid local file. The image repository now checks exact source ID/URL and file existence, reuses the existing asset, and links it to the confirmed work ID. Collection results now carry stage timings and counts; the URL worker emits a content-free timing summary through the configured logger.

Recurring work-seed requests can now specify one stable source query, and the queue payload preserves `seed_menu_work_id` through collection. Use a stable source-specific title for recurring plans. Different retrieval queries remain allowed to create distinct editorial jobs; the two pilot backlog items used different query strings, so they were not treated as the same generation request.

No authoritative numeric topic/subtopic mapping was found in the RAG reference snapshot or `source_type_map.json`. The snapshot manifest explicitly records this gap, and the RAG staging schema currently permits nullable `topic_id`. Drafts therefore retain `topic_id=NULL`; no topic, subtopic, or entity IDs were guessed. If the target ERD requires those fields, an authoritative crosswalk and content-ID allocation policy are still required before claiming full ERD compatibility.
