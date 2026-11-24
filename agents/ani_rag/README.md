# ani_rag

## 서버에서 준비할 데이터

정식 작품 ID는 저장소 루트의 `catalog/`를 사용한다. 기존 근거와 색인을 이어 쓰려면 팀이 전달한 RAG DB, 벡터 DB, 이미지 저장 폴더를 `scripts/manage_workers.ps1`의 `-Database`, `-VectorDatabase`, `-ImageDirectory`에 연결한다.

캐릭터·주제가 조회에 쓰는 `work_characters.csv`와 `work_theme_songs.csv`, 인물·작품의 외부 ID를 대조하는 `menu_dict_20261109/`는 팀 내부 전달본을 이 패키지의 `data/reference/`에 둔다. 선택적으로 ANN 일괄 보강 스크립트를 실행할 때는 `--worklist`에 팀에서 전달한 작업 목록 CSV를 지정한다.

## 현재 상태 (2026-11-23)

임시 SQLite 복사본에서 MCP 웹 후보를 RAG 근거와 대조해 초안을 만들고 별도 검수까지 통과하는 한 건 흐름을 확인했다. 기존 RAG 근거의 전체 벡터 색인도 완료됐고, 실제 DB는 백업 후 추가 마이그레이션을 적용했다. 최신 준비 상태와 남은 운영 연결은 [`docs/RAG_READY_CHECK.md`](docs/RAG_READY_CHECK.md), 색인·백업 정보는 [`docs/RAG_VECTOR_STATUS.md`](docs/RAG_VECTOR_STATUS.md)를 본다.

자동 운영 계획은 저장소 루트의 정식 작품과 확인된 임시 작품을 ID 오름차순으로 읽고, 7일 순환을 목표로 계획 시각을 고르게 나눈다. 이 순서는 다음에 탐색할 작품을 정한다. 웹 후보 평가는 원문이 다루는 작품과 주장이 RAG 근거에 맞는지 본다. 오래된 성우 인터뷰나 제작 자료도 활용할 수 있다. 회차·상영시간처럼 판본에 따라 값이 달라지는 질문은 해당 시즌과 판본의 근거를 찾는다.

Wikidata 캐릭터에 프랜차이즈만 적혀 있으면 캐릭터 고유 번호와 저장된 시즌·극장판 출연 자료를 함께 확인해 정식 작품에 연결한다. 웹 기사에서는 독자에게 전할 핵심 내용이 같은 작품의 독립 근거로 충분히 뒷받침되는지 평가한다. 직접 인터뷰는 발화자의 작품 참여를 확인하고 원문에서 발언을 특정할 수 있을 때 그 사람의 말로 인용할 수 있다. 검수에서는 발화자, 원문 주소, 실제 인용 구절을 다시 확인한다.

수집·생성·검수와 벡터 색인기는 별도 프로세스로 실행한다. 재시작 후 밀린 계획은 한 번에 하나씩 큐에 넣는다. Firecrawl 요청은 같은 MCP 세션에서 순차 실행하고, 별도 Wikidata 요청 하나만 겹쳐 처리한다. API 크레딧·인증·호출 제한 오류가 나면 해당 프로세스의 Firecrawl 호출을 멈춘다. 벡터 색인기는 기본 5분마다 새 참고자료를 반영한다. Windows에서는 `scripts/manage_workers.ps1 -Action status`로 실제 실행 상태를 확인한다. `-Action start`는 프로젝트의 `.venv`가 있으면 우선 사용하고, 다른 RAG용 환경은 `-PythonExe`로 지정한다.

The full existing reference corpus has been embedded into a separate sidecar index. Current counts and the incremental refresh process are summarized in [`docs/RAG_VECTOR_STATUS.md`](docs/RAG_VECTOR_STATUS.md) and [`docs/INCREMENTAL_RAG_EXPANSION.md`](docs/INCREMENTAL_RAG_EXPANSION.md). Vector indexing did not modify the source database; additive schema migrations were later applied to the live temporary SQLite after making an online backup.

## Broad-web cost control

후보 평가·생성·검수 큐의 대기, 실행, 재시도 작업 합계가 4건에 도달하면 새 웹 검색(다음 결과 페이지 포함)과 정기 URL 탐색을 쉰다. 이미 찾은 후보는 계속 처리한다. 실제 생성 큐에 수정 작업이 2건 이상 있으면 새 후보 평가도 큐에 둔다. 생성 워커는 실행 가능한 수정부터 가져온다. 개별 보완용 URL 조회와 Wikidata 작업은 계속할 수 있다.

수정이 1건 이하로 줄면 후보 평가를 다시 진행한다. 새 검색은 전체가 2건 이하이고 수정도 1건 이하일 때 재개한다. 재시작 시 전체 3건이면 같은 기준까지 기존 작업을 먼저 처리한다. 한 번 시작한 검색의 결과는 모두 보존하므로 전체가 잠시 4건을 넘을 수 있다. 검색 보류 기준은 `ANI_RAG_WEB_DISCOVERY_BACKLOG_LIMIT`로 조정할 수 있다. 과거 초안에 남은 수정 표시는 집계하지 않는다.

검색 결과에서 작품명이 맞지 않거나 탐색·검색·목록·피드 페이지로 보이는 URL은 원문을 읽기 전에 거른다. 큐에 이미 들어간 후보도 읽기 직전에 같은 검사를 거친다.

로컬 SQLite에서는 이 컴퓨터의 마지막 부팅 전에 시작과 갱신이 모두 끊긴 `running` 작업을 대기 수에서 제외한다. 보류·재개 시점의 전체 작업 수와 수정 작업 수는 크롤러 로그에 남는다.

## 작품 ID

기간형과 같은 저장소 루트의 `catalog/menu_works.csv`와 `catalog/menu_work_entries.csv`에서 작품과 시즌을 읽는다. 두 에이전트는 원문에서 식별된 새 작품의 임시 ID와 제목을 `catalog/agent_works.csv`에 함께 저장한다. 모호하거나 번호가 충돌한 작품은 `catalog/discovered_works.csv`에 남긴다. 자세한 판정 기준과 경로 설정은 [작품 ID 연결 규칙](docs/WORK_ID_MATCHING.md)을 본다.

## Dataset alignment

The dataset itself must match the supplied ERD when saved; no separate conversion job is intended. See [`docs/ERD_DATASET_MAPPING.md`](docs/ERD_DATASET_MAPPING.md) for the field mapping and unresolved topic/entity identity decisions. The staging schema now models reusable evidence formats, evidence-backed entity↔work facts, birthday/source facts, and image↔entity links for cross-work curation and per-character art selection. It remains a design artifact; these new relations have not been applied to the live RAG database.

The source registry has seven approved source records with `is_collecting=true` and one non-executable Frieren draft, which remains disabled until its redirect path is verified. `automatic_plans.json` schedules only those approved records; collection begins only when the crawler worker is started. ANN is limited to work-ID-seeded paths under `/encyclopedia/`; news, reviews, interviews, features, and sidebar cards are excluded. Encyclopedia facts may support `character`, `guide`, `people`, or `music` content when the page directly supports that format. No topic, subtopic, or ERD entity ID is inferred from source candidates or `menu_work_id`; authoritative catalogs must supply those identifiers.

ANN Encyclopedia is the first-choice reusable reference source for those four RAG content types. Use a title-specific official source to fill a fact gap or verify a consequential/disputed claim; official domains still need separate review and approval. This selection order is documented in [`docs/ANN_CORE_SOURCE_POLICY.md`](docs/ANN_CORE_SOURCE_POLICY.md) and is not an automatic source-fallback worker. ANN is enabled in the registry, but collection still requires an explicit bounded plan and a running worker.

The bounded broad-web candidate path is documented in [`docs/WEB_DISCOVERY_TO_RAG.md`](docs/WEB_DISCOVERY_TO_RAG.md). Search results only discover a candidate page; a single page is read on its exact HTTPS host, the candidate’s identity is checked against the work catalog, and generation is enqueued only if relevant evidence from ANN or the imported AniList snapshot clears the configured relevance floor. Candidate page text is not stored or used as factual support. The same ANN/AniList retrieval scope carries into generation and review revisions. Without a vector sidecar connection, relevance uses lexical overlap. If a completed vector sidecar and matching query-embedding adapter are explicitly supplied to runtime composition, the same gate uses cosine similarity instead. Neither score is a factuality score, and the numeric threshold is not calibrated to be interchangeable across the two modes. This is a bounded search-and-read path, not a full-web crawler; it is wired but has not been run as a recurring worker.

## Agent roles

The three roles have separate packages under `src/ani_rag/agents/`:

- `mcp_crawler/`: bounded URL search/read and structured entity/property collection.
- `content_generation/`: me;nu voice and card-copy prompts, a separate detail writer, image-aware art direction, page PNG rendering, and frontend overlay layout.
- `reviewer/`: independent carousel order and copy readability review.

The runtime sequence follows the period lane's downstream output shape: retrieve and plan copy → gather local images plus one bounded Firecrawl Images search (up to eight results) → assess each page's image against its copy → one text-free PNG per page plus separate HTML copy/layout data → standalone detail article → save draft → independent score-based review → pass/revise routing. Generation does not run the reviewer. Search results are provisional and staged temporarily; registered images keep their source and matched work IDs. A draft still needs at least three eligible, readable, work-linked images. A weak page-level visual match lowers the independent review score instead of stopping generation. Retrieval filters by the requested profile's accepted evidence types, not the document's original collection lane, so a relevant ANN `reference_fact` can support guide, character, people, or music formats while review/community/curation lanes keep their stricter evidence requirements. It uses RAG evidence/chunk IDs and RAG persistence; it does not import `ani_menu_agent`.

Generation also has a relevance gate: the default minimum is `0.20`. Chunks below it are excluded from generation context; if no chunk meets it, generation is blocked and the queue records the best score and required minimum. A queue payload may override `min_retrieval_score` from `0.0` to `1.0`; review-triggered revisions preserve the same threshold. By default the score is lexical overlap. Explicitly configured vector retrieval returns cosine similarity; tune the threshold separately before switching modes because the score scales differ.

`entity_linking.py` can attach character IDs to image candidates when a host supplies the authoritative entity catalog. It requires a unique work-scoped catalog match and a supporting source chunk; otherwise the image remains unlinked to a character. `curation.py` proposes source-cited groupings for director filmographies, voice-actor character credits, and shared birthdays from already-resolved facts. It does not infer missing relations or claim a complete ranking. Curation plans are currently proposals: automatic fact extraction, persistence of cross-type eligibility, and enqueueing those plans into generation are not wired yet.

`compose_runtime()` wires the three role agents over caller-supplied MCP adapters, models, and a separately provisioned RAG database connection. It does not connect, configure sources, or start workers during import. The per-page PNG and frontend copy/layout manifest are persisted together in the RAG staging schema. The detail helper produces article → sources → AI-use notice in that order.

## 독립 상시 작업자

세 역할은 각각 `compose_crawler_agent()`, `compose_content_generation_agent()`, `compose_reviewer_agent()`로 따로 조립할 수 있고, 각 에이전트의 `run_forever(stop_event=...)`를 별도 프로세스나 작업으로 시작한다. 큐가 비어 있을 때 기본 2초마다 DB를 확인하며, stop event를 설정하면 해당 작업자만 멈춘다. 다시 시작할 때는 새 stop event로 그 작업자의 루프를 다시 호출한다.

- MCP 크롤러: `config/automatic_plans.json`의 계획을 시간차를 두고 반복 큐잉한다. URL 검색·읽기와 구조화 질의 요청을 처리하고 저장소에 근거를 적재한다.
- 콘텐츠 생성: DB의 generation backlog를 큐로 조정한 뒤 검색 가능한 근거를 읽어 초안을 만들고, 완료된 초안을 검수 큐에 넣는다.
- 검수: 검수 큐에서 완성 초안을 가져와 독립 검수한 뒤 상태를 기록한다.

The crawler checks official and temporary works, season titles, verified aliases and external IDs. A confirmed new work may receive a temporary ID. Ambiguous candidates stay out of the generation queue. Pass `work_match_model` when composing the crawler.

검수는 생성과 분리되어 동작하며, 자료·근거·작품 연결 검사를 포함한다. 등록된 승인 출처 7개는 반복 계획에 포함되어 있다. RAG DB 스키마, 워커 실행 진입점, 자동 계획, 증분 벡터 갱신, Windows 작업자 관리 스크립트가 준비됐다. 워커는 명령으로 시작해야 하며 현재 실행 중이라고 가정하지 않는다.

각 독립 프로세스는 같은 전용 RAG DB 파일을 가리키는 별도의 SQLite 연결을 받아야 한다. 생성 worker는 로컬 후보와 Firecrawl 이미지 검색 결과를 함께 사용한다. 검색 결과는 임시 후보이며 카드별 이미지와 카피의 시각적 연결을 vision 모델이 확인한 뒤 선택된 이미지만 작품 ID에 연결해 저장한다. 검색에 실패하거나 적합한 이미지가 3개 미만이면 생성을 보류한다. 이 코드 변경은 실행 중인 worker에 자동 반영되지 않으며 worker를 재시작하지 않았다. CLI나 자동 프로세스 감독기는 아직 없다. 이 문서화/코드 조립은 루프를 실행하지 않는다.

For the source-by-source port record, see [`docs/PORT_MANIFEST.md`](docs/PORT_MANIFEST.md). For a plain-language explanation of the folders, data IDs, and one-item processing flow, see [`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md). The parity boundary and remaining gates are in [`docs/GENERATION_REVIEW_PORT.md`](docs/GENERATION_REVIEW_PORT.md).

The recommended Firecrawl/Wikidata endpoints, live tool-list check, and source-activation gate are documented in [`docs/MCP_CONNECTION_SETUP.md`](docs/MCP_CONNECTION_SETUP.md) and [`MCP_PILOT_PLAN.md`](MCP_PILOT_PLAN.md).

The existing seven-type source shortlist and each candidate's evidence boundary are summarized in [`docs/RAG_SOURCE_SHORTLIST.md`](docs/RAG_SOURCE_SHORTLIST.md). It is derived from the legacy registry and RAG candidate inventory; it does not approve or activate sources.

`compose_runtime()` wires URL collection, structured entity/property collection, retrieval, generation, and independent review as injectable one-item workers. It does not connect to MCP or start workers. The enqueue helpers create bounded queue requests only when a caller invokes them with an already composed runtime; they have not been called here. There is no separate ERD conversion step: the saved dataset itself must carry the ERD fields and relationships. Numeric topic/subtopic/entity IDs and source/document IDs must come from their authoritative catalogs when content is prepared and saved.

`ani_rag`는 character, guide, people, music, humor, curation, review 자료를 출처 기반으로 수집하고 RAG 근거로 활용하는 독립 프로젝트다. 기간·최신성이 중요한 news/event/goods lane은 `ani_me;nu`에 둔다.

## 구현 상태

- `src/ani_rag/` 런타임 import에는 `ani_menu_agent`나 원본 프로젝트 경로 폴백이 없다.
- MCP: project aliases `firecrawl` and `wikidata` are configured in `config/mcp_servers.json`; live tools were confirmed. Firecrawl supports bounded search and single-page reads; `WikidataStructuredMCPAdapter` calls the configured structured tools. The approved allowlist covers ANN, Wikidata, Kitsu, FlyingDog music releases, Anime Trending poll charts, Anime Corner reviews, and Anime Corner interviews. Seven sources are included in recurring plans in `config/automatic_plans.json`; `frieren_official` remains draft.
- 로컬 작품 resolver는 `data/reference/`만 읽는다. exact title, 등록 external ID, verified alias로 확인하고 모호한 결과는 `ambiguous`로 둔다. parent/franchise를 근거로 ID를 추론하지 않는다.
- Evidence/storage and queue contracts are implemented, and SQLite adapters use only a caller-owned connection. Explicit image acquisition is connected for approved source image candidates from Kitsu, ANN, and Wikidata P18/P154; exact media-host allowlists, file validation, hashes, work links, and per-image failure metadata are preserved. Image acquisition requires a configured local asset directory and is not run automatically.
- Page-image rendering follows the period agent's same-source blurred background, source-derived palette blend, full-art contain placement, feathered edge blend, and copy-position-aware soft scrim/blur. Each page remains one full-bleed feed card with separately stored HTML copy/layout. Named-character portraits may be prioritized, but all confirmed images for the selected work remain available. A readable representative or related-character image does not need to depict the card's exact scene, age, action, episode, season, or sentence; scene mismatch does not reduce its image emphasis. Crisp-art placement adapts to orientation: portrait art fills nearly the whole card behind the lower copy scrim, square art uses most of the canvas, and landscape art is fitted by width without cropping. Generation still requires three distinct readable images linked to the selected work and blocks a clearly different work identity or unusable file. Score-based review updates are loaded by newly started workers.
- Generation dispatch now balances the pending backlog by work ID and content type, and prefers less-overlapping query topics within the same work when alternatives are available. FIFO order breaks equivalent choices. Image candidates used in any of the previous three drafts for the same work are temporarily held back when at least three fresh, distinct images remain. If the pool is small, the least-recently-used images fill only the minimum candidate set. Identical image bytes are deduplicated and cannot be selected twice in one carousel.
- Music, people, episode, and other formats can use appealing distinct images from the confirmed work when a dedicated asset type is unavailable. A matching album/staff/character image is preferred when useful, never a mandatory image category. The image still needs a valid work link, and the copy must not claim the image depicts a fact it does not show.
- Before generation, the writer receives up to 30 recent titles, summaries, and angles for the same work and is told to avoid their core subject across content types. A second near-duplicate check compares title, summary, and editorial angle before render/review and again inside the draft save transaction; a match is blocked rather than saved. This is a lexical similarity guard plus a model instruction, not a semantic embedding guarantee, so an unusually paraphrased duplicate may still need manual review.
- generation worker가 카드 카피/상세글/이미지 배치/텍스트 없는 페이지별 PNG를 만들고, copy/layout manifest와 PNG를 별도 RAG staging 테이블에 저장한 뒤 독립 검수 worker에 전달한다. 검수는 임시 HTML copy-overlay 미리보기를 vision-capable model에 보낼 수 있다. 사용자 화면/API는 아직 연결되지 않았다.

파일별 확인 결과와 실행 차단 조건은 [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md), 포팅 결정을 [`BOOTSTRAP_MANIFEST.md`](BOOTSTRAP_MANIFEST.md)에서 본다. `docs/COPIED_CONTENT_BASELINE.md`는 기존 prompt/policy 참고 스냅샷이지 실행 코드가 아니다.

팀원 제공 `menu_dict.dump`의 전체 facts 적용 범위와 출처별 제외 기준은 [`docs/MENU_DICT_RAG_IMPORT.md`](docs/MENU_DICT_RAG_IMPORT.md)에 기록했다. 승인된 사실만 별도 import 도구로 기존 RAG DB에 넣을 수 있으며, DB schema를 만들거나 생성 backlog를 자동으로 채우지 않는다.

## 현재 실행 한계

출처 등록부의 7개 승인 출처에는 명시적으로 큐에 넣은 요청을 보낼 수 있지만 반복 수집 계획은 아직 0개다. 실제 임시 RAG SQLite 스키마는 백업 후 적용돼 있다. 이미지 후보 획득·검증과 로컬 작품 연결 코드가 있고, 이미지 디렉터리와 모델/API 자격증명은 실행 환경에서 제공해야 한다. 새 참고자료의 벡터 반영은 현재 증분 색인 스크립트를 별도로 실행해야 한다.

`.env`, 기존 ani_me;nu DB/WAL, 로그, 기존 생성 결과는 열지 않았다. 별도 파일럿 DB에만 스키마를 적용하고 Firecrawl을 통해 Kitsu 항목 1개를 읽었다. 지속 worker, CLI, 모델 생성, 검수 worker는 실행하지 않았고 테스트도 추가·실행하지 않았다.

### 단일 이미지 수집 파일럿

2026-11-09에 별도 파일 [`data/ani_rag_pilot.sqlite3`](data/ani_rag_pilot.sqlite3)에 스키마를 적용하고, Kitsu에서 로컬 작품 ID 1의 항목 1개를 수집했다. 원문 1건, 작품 연결 1건, 이미지 asset 364건을 저장했고 실제 SHA-256 파일은 362개(동일 바이트 파일은 해시 경로를 공유)다. 파일 전체가 존재하고 DB 해시와 일치한다. 생성 backlog 한 건만 대기 중이며 생성·검수 워커는 시작하지 않았다. 출처 반복 수집은 계속 꺼져 있다.


### Bounded ANN adapter check (2026-11-08)

A live adapter check used local work ID 1 to search ANN and returned three separately identified entries; one page was read successfully with its ANN ID and source metadata preserved. The source remains narrowly approved and `is_collecting=false`. This check did not run a worker, call an LLM, or write to a database.


Wikidata is approved as a character identity/relationship lead. The structured collection boundary accepts P674 relationships and P18/P154 image statements; image filenames are converted to Commons `Special:FilePath` URLs, with Commons and upload hosts separately allowlisted. The injected structured MCP adapter and live response still must supply those properties. Do not treat Wikidata alone as primary fact confirmation.


Kitsu is approved only for work-title-filtered `guide`/`character` catalog reference lookup. `curation`, ratings and synopsis reuse are excluded. The Firecrawl adapter parses the filtered JSON:API response and extracts all distinct returned cover/character image variants; the crawler can download/register them for matched work IDs. Its approved `guide`/`character` lookup is included in the configured recurring schedule.


Image files are stored as local work-linked assets with source URL/page, native ID, role, hash, dimensions and byte size. The generation worker selects from assets registered for evidence work IDs. Kitsu, ANN encyclopedia artwork, and requested Wikidata P18/P154 candidates are supported; missing media hosts or individual download failures are recorded and do not discard the text evidence. No bulk image run has occurred.
