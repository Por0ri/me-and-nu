# ani_rag

## Dataset alignment

The dataset itself must match the supplied ERD when saved; no separate conversion job is intended. See [`docs/ERD_DATASET_MAPPING.md`](docs/ERD_DATASET_MAPPING.md) for the field mapping and unresolved topic/entity identity decisions. The staging schema now models reusable evidence formats, evidence-backed entity↔work facts, birthday/source facts, and image↔entity links for cross-work curation and per-character art selection. It remains a design artifact; these new relations have not been applied to the live RAG database.

The source registry has seven approved, non-collecting sources and one non-executable Frieren draft. ANN is limited to work-ID-seeded paths under `/encyclopedia/`; news, reviews, interviews, features, and sidebar cards are excluded. Encyclopedia facts may support `character`, `guide`, `people`, or `music` content when the page directly supports that format. No topic, subtopic, or ERD entity ID is inferred from source candidates or `menu_work_id`; authoritative catalogs must supply those identifiers.

## Agent roles

The three roles have separate packages under `src/ani_rag/agents/`:

- `mcp_crawler/`: bounded URL search/read and structured entity/property collection.
- `content_generation/`: me;nu voice and card-copy prompts, a separate detail writer, image-aware art direction, page PNG rendering, and frontend overlay layout.
- `reviewer/`: independent carousel order and copy readability review.

The runtime sequence follows the period lane's downstream output shape: card copy plan → visual direction and image/copy match check → one text-free PNG per page plus separate HTML copy/layout data → standalone detail article → save draft → independent readability review → pass/revise/reject routing. Generation does not run the reviewer. A draft is blocked unless it has at least three registered, work-matched source images and every page passes the visual image/copy match check. It uses RAG evidence/chunk IDs and RAG persistence; it does not import `ani_menu_agent`.

`entity_linking.py` can attach character IDs to image candidates when a host supplies the authoritative entity catalog. It requires a unique work-scoped catalog match and a supporting source chunk; otherwise the image remains unlinked to a character. `curation.py` proposes source-cited groupings for director filmographies, voice-actor character credits, and shared birthdays from already-resolved facts. It does not infer missing relations or claim a complete ranking. Curation plans are currently proposals: automatic fact extraction, persistence of cross-type eligibility, and enqueueing those plans into generation are not wired yet.

`compose_runtime()` wires the three role agents over caller-supplied MCP adapters, models, and a separately provisioned RAG database connection. It does not connect, configure sources, or start workers during import. The per-page PNG and frontend copy/layout manifest are persisted together in the RAG staging schema. The detail helper produces article → sources → AI-use notice in that order.

## 독립 상시 작업자

세 역할은 각각 `compose_crawler_agent()`, `compose_content_generation_agent()`, `compose_reviewer_agent()`로 따로 조립할 수 있고, 각 에이전트의 `run_forever(stop_event=...)`를 별도 프로세스나 작업으로 시작한다. 큐가 비어 있을 때 기본 2초마다 DB를 확인하며, stop event를 설정하면 해당 작업자만 멈춘다. 다시 시작할 때는 새 stop event로 그 작업자의 루프를 다시 호출한다.

- MCP 크롤러: 승인된 출처에 대해 명시적으로 전달한 `RecurringCollectionPlan`만 반복 큐잉한다. URL 검색·읽기와 구조화 질의 요청을 처리하고 저장소에 근거를 적재한다.
- 콘텐츠 생성: DB의 generation backlog를 큐로 조정한 뒤 검색 가능한 근거를 읽어 초안을 만들고, 완료된 초안을 검수 큐에 넣는다.
- 검수: 검수 큐에서 완성 초안을 가져와 독립 검수한 뒤 상태를 기록한다.

작품 매칭은 저장소 루트 `catalog/menu_works.csv`와 `catalog/menu_work_entries.csv`를 정본으로 읽고, `ani_me;nu/data/reference/`의 검토 별칭·외부 ID·임시 작품 목록을 함께 확인한다. 실패하면 `compose_runtime()`에 주입한 구조화 모델(기본은 생성 모델)이 원문 제목·본문과 기존 작품 후보를 보고 식별한다. 모델은 ID를 만들지 않는다. 새 작품 제안은 원문 작품명과 정확 인용 근거가 있어야 하며, 저장 경계에서 Kitsu ID 기반 또는 정규화 제목 CRC32 기반 임시 ID를 부여해 `agent_works.csv`에 기록한다. 정식 `catalog/`에는 쓰지 않는다. 현재 RAG 작업 카탈로그의 `menu_work_id`는 ERD `entity_id`와 같다고 간주하지 않으며, `topic_id` 연결은 권위 있는 매핑이 확인될 때까지 비워 둔다. 개별 crawler compose에는 `work_match_model`을 전달해야 한다.

초기 검수는 캐러셀 페이지 구성과 글의 가독성만 통과 기준으로 삼는다. 저작권/이미지 권리/사실성 점수는 현재 통과 차단 조건이 아니다. 등록된 출처 7개는 승인 상태지만 모두 `is_collecting=false`이며, 반복 계획이 없어 자동 수집하지 않는다. MCP 어댑터와 모델 호출 경로 일부는 구현돼 있어도 별도 RAG DB·스키마, 실행 설정, 프로세스 감독기가 준비되지 않아 운영 실행 단계는 아니다.

각 독립 프로세스는 같은 전용 RAG DB 파일을 가리키는 별도의 SQLite 연결을 받아야 한다. 이미지가 작품에 연결된 후보 3개 미만이면 생성을 보류하고, 충분한 후보가 있어도 카드별 이미지와 카피의 시각적 연결을 vision 모델이 확인하지 못하면 초안을 막는다. 이 변경은 실행 중인 worker에 자동 반영되지 않으며 worker를 재시작하지 않았다. CLI나 자동 프로세스 감독기는 아직 없다. 이 문서화/코드 조립은 루프를 실행하지 않는다.

For the source-by-source port record, see [`docs/PORT_MANIFEST.md`](docs/PORT_MANIFEST.md). For a plain-language explanation of the folders, data IDs, and one-item processing flow, see [`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md). The parity boundary and remaining gates are in [`docs/GENERATION_REVIEW_PORT.md`](docs/GENERATION_REVIEW_PORT.md).

The recommended Firecrawl/Wikidata endpoints, live tool-list check, and source-activation gate are documented in [`docs/MCP_CONNECTION_SETUP.md`](docs/MCP_CONNECTION_SETUP.md) and [`MCP_PILOT_PLAN.md`](MCP_PILOT_PLAN.md).

The existing seven-type source shortlist and each candidate's evidence boundary are summarized in [`docs/RAG_SOURCE_SHORTLIST.md`](docs/RAG_SOURCE_SHORTLIST.md). It is derived from the legacy registry and RAG candidate inventory; it does not approve or activate sources.

`compose_runtime()` wires URL collection, structured entity/property collection, retrieval, generation, and independent review as injectable one-item workers. It does not connect to MCP or start workers. The enqueue helpers create bounded queue requests only when a caller invokes them with an already composed runtime; they have not been called here. There is no separate ERD conversion step: the saved dataset itself must carry the ERD fields and relationships. Numeric topic/subtopic/entity IDs and source/document IDs must come from their authoritative catalogs when content is prepared and saved.

`ani_rag`는 character, guide, people, music, humor, curation, review 자료를 출처 기반으로 수집하고 RAG 근거로 활용하는 독립 프로젝트다. 기간·최신성이 중요한 news/event/goods lane은 `ani_me;nu`에 둔다.

## 구현 상태

- `src/ani_rag/` 런타임 import에는 `ani_menu_agent`나 원본 프로젝트 경로 폴백이 없다.
- MCP: project aliases `firecrawl` and `wikidata` are configured in `config/mcp_servers.json`; live tools were confirmed for the reviewed sources. Firecrawl supports bounded search and single-page reads; Wikidata structured collection is exposed through an injected adapter Protocol. The approved allowlist covers ANN, Wikidata, Kitsu, FlyingDog music releases, Anime Trending poll charts, Anime Corner reviews, and Anime Corner interviews. All seven remain `is_collecting=false`; `frieren_official` remains draft. Anime Trending is limited to poll/chart evidence; sample size is unavailable and an observed date mismatch must be preserved. Anime Corner reviews keep author/date/opinion/citations separate; the interview source uses only attributed people statements and firsthand production anecdotes for humor.
- 로컬 작품 resolver는 공유 `catalog/`의 정식 작품·항목 목록과 `ani_me;nu/data/reference/`의 보조 매핑을 읽는다. exact title, 등록 external ID, verified alias로 확인하고 모호한 결과는 `ambiguous`로 둔다. parent/franchise를 근거로 ID를 추론하지 않는다.
- Evidence/storage and queue contracts are implemented, and SQLite adapters use only a caller-owned connection. Explicit image acquisition is connected for approved source image candidates from Kitsu, ANN, and Wikidata P18/P154; exact media-host allowlists, file validation, hashes, work links, and per-image failure metadata are preserved. Image acquisition requires a configured local asset directory and is not run automatically.
- Page-image rendering follows the period agent's same-source blurred background, source-derived palette blend, full-art contain placement, and feathered edge blend. The generation vision step checks the selected image against each card's subject and blocks uncertain matches. No worker was restarted to load these changes.
- generation worker가 카드 카피/상세글/이미지 배치/텍스트 없는 페이지별 PNG를 만들고, copy/layout manifest와 PNG를 별도 RAG staging 테이블에 저장한 뒤 독립 검수 worker에 전달한다. 검수는 임시 HTML copy-overlay 미리보기를 vision-capable model에 보낼 수 있다. 사용자 화면/API는 아직 연결되지 않았다.

파일별 확인 결과와 실행 차단 조건은 [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md), 포팅 결정을 [`BOOTSTRAP_MANIFEST.md`](BOOTSTRAP_MANIFEST.md)에서 본다. `docs/COPIED_CONTENT_BASELINE.md`는 기존 prompt/policy 참고 스냅샷이지 실행 코드가 아니다.

팀원 제공 `menu_dict.dump`의 전체 facts 적용 범위와 출처별 제외 기준은 [`docs/MENU_DICT_RAG_IMPORT.md`](docs/MENU_DICT_RAG_IMPORT.md)에 기록했다. 승인된 사실만 별도 import 도구로 기존 RAG DB에 넣을 수 있으며, DB schema를 만들거나 생성 backlog를 자동으로 채우지 않는다.

## 현재 실행 한계

출처 등록부에는 ANN, Wikidata, Kitsu, FlyingDog, Anime Trending, Anime Corner의 review/interview 등 7개 출처가 승인 상태지만 모두 `is_collecting=false`이고 반복 수집 계획도 없다. 이미지 후보 획득·검증·로컬 작품 연결 코드가 있으나 이 작업에서는 실행하지 않았다. 이미지 경로를 포함한 DB 저장은 별도 RAG SQLite 스키마를 준비한 caller-owned connection이 필요하다. 모델 자격증명, MCP 수집 운영 설정, DB/스키마 적용, 자동 실행 supervisor/CLI는 아직 운영 준비가 아니다.

`.env`, 기존 ani_me;nu DB/WAL, 로그, 기존 생성 결과는 열지 않았다. 별도 파일럿 DB에만 스키마를 적용하고 Firecrawl을 통해 Kitsu 항목 1개를 읽었다. 지속 worker, CLI, 모델 생성, 검수 worker는 실행하지 않았고 테스트도 추가·실행하지 않았다.

### 단일 이미지 수집 파일럿

2026-11-09에 별도 파일 [`data/ani_rag_pilot.sqlite3`](data/ani_rag_pilot.sqlite3)에 스키마를 적용하고, Kitsu에서 로컬 작품 ID 1의 항목 1개를 수집했다. 원문 1건, 작품 연결 1건, 이미지 asset 364건을 저장했고 실제 SHA-256 파일은 362개(동일 바이트 파일은 해시 경로를 공유)다. 파일 전체가 존재하고 DB 해시와 일치한다. 생성 backlog 한 건만 대기 중이며 생성·검수 워커는 시작하지 않았다. 출처 반복 수집은 계속 꺼져 있다.


### Bounded ANN adapter check (2026-11-08)

A live adapter check used local work ID 1 to search ANN and returned three separately identified entries; one page was read successfully with its ANN ID and source metadata preserved. The source remains narrowly approved and `is_collecting=false`. This check did not run a worker, call an LLM, or write to a database.


Wikidata is approved as a character identity/relationship lead. The structured collection boundary accepts P674 relationships and P18/P154 image statements; image filenames are converted to Commons `Special:FilePath` URLs, with Commons and upload hosts separately allowlisted. The injected structured MCP adapter and live response still must supply those properties. Do not treat Wikidata alone as primary fact confirmation.


Kitsu is approved only for work-title-filtered `guide`/`character` catalog reference lookup. `curation`, ratings and synopsis reuse are excluded. The Firecrawl adapter parses the filtered JSON:API response and extracts all distinct returned cover/character image variants; the crawler can download/register them for matched work IDs. No recurring collection is enabled.


Image files are stored as local work-linked assets with source URL/page, native ID, role, hash, dimensions and byte size. The generation worker selects from assets registered for evidence work IDs. Kitsu, ANN encyclopedia artwork, and requested Wikidata P18/P154 candidates are supported; missing media hosts or individual download failures are recorded and do not discard the text evidence. No bulk image run has occurred.
