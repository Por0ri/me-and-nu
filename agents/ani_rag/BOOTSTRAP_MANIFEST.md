# Bootstrap manifest

- 작성일: 2026-11-06 (Asia/Seoul), 구현 점검/갱신: 2026-11-07
- 원본 프로젝트: `../ani_me;nu`
- 대상: `agents/ani_rag`
- 처리 원칙: 기존 파일은 읽기 전용으로 조사했다. 원본 파일을 이동하거나 복사하지 않았다. 이번 작업에서 새 독립 계약·모델을 구현했다.
- 기존 작업 트리에는 조사 시점에 다수의 수정·미추적 파일이 있었다. 이번 재점검에서 `ani_me;nu`에 기존 상태 항목 189개가 확인됐다. 출처 설정, 작품 레퍼런스, 크롤러/수집 코드 변경사항은 보존하고 수정하지 않았다. SQLite DB, WAL/SHM, 로그, 생성 결과는 열거나 복사하지 않았다.
- `agents/ani_rag/.env`는 존재하지만 내용을 읽지 않았다. 루트 `.gitignore`의 `**/.env` 규칙 적용과 Git 비추적 상태를 경로만 확인했다. 예시 파일은 새로 만들지 않았다.
- 이번 출처 조사에서 기존 `sources.json`과 `source_type_map.json`을 읽기 전용으로 조회했다. 지정된 53개 후보는 모두 원본 양쪽에 존재했다. 제공된 런타임 도구 목록에서 source-specific MCP 서버와 search/read 도구는 찾지 못했다. 일반 웹 검색 도구는 source-bound MCP 도구가 아니므로 모든 후보 연결 상태를 미확인으로 두었다.

## 파일 선별표

| 원본 경로 | 대상 경로 | 처리 | 필요한 이유 | 의존성/주의 |
|---|---|---|---|---|
| 없음 (신규 작성) | `README.md` | 생성 | 프로젝트 목적, 경계, 공유 레퍼런스 사용법 기록 | 구현·실행 지침이 아님 |
| 없음 (신규 작성) | `MCP_PILOT_PLAN.md` | 생성 | 2-server character pilot 후보와 실제 연결 전 승인 조건 기록 | 공식 도구 안내를 근거로 한 제안. MCP 연결이나 allowlist 승인이 아님 |
| 없음 (신규 작성) | `BOOTSTRAP_MANIFEST.md` | 생성 | 이 표와 판단 근거 보존 | 조사 당시 변경 상태를 반영 |
| `agents/ani_me;nu/pyproject.toml` | `pyproject.toml` | 참조만 | 기존 패키지가 Python 3.12와 hatchling을 사용함을 확인 | 기존 의존성 전체는 복제하지 않음. 빈 패키지용 최소 메타데이터만 새로 작성 |
| 없음 (신규 초안) | `config/sources.evergreen.json` | 생성 | RAG 전용 출처 설정 위치 마련 | 출처 목록은 비어 있음. 승인이나 기존 설정 복사가 아님 |
| `agents/ani_me;nu/config/sources.json`, `config/source_type_map.json` | `config/sources.candidates.json` | 메타데이터만 추출 | 7개 RAG 유형에 매핑된 53개 후보 인벤토리 작성 | 검토용 별도 schema이며 `settings.py` 실행 allowlist 아님. 전부 `mcp_server=unknown`, search/read=`unconfirmed`. 원본 설정은 수정하지 않음 |
| `agents/ani_me;nu/config/sources.json` | `config/sources.evergreen.json` | 참조만 | 기존 Source 설정 형식·출처 상태 조사 | 원본은 수정 중일 수 있고 기간 민감 출처와 혼합되어 있어 복제하지 않음 |
| `agents/ani_me;nu/config/source_type_map.json` | 해당 없음 | 참조만 | 기존 유형별 endpoint 매핑이 있음을 확인 | evergreen lane 매핑이 아님. `config/source_lane_map.json`은 존재하지 않음 |
| `catalog/menu_works.csv`, `catalog/menu_work_entries.csv` 및 `agents/ani_me;nu/data/reference/`의 보조 레퍼런스 | 해당 없음 | 정식 목록은 읽기, 보조 매핑도 읽기 | 두 에이전트가 동일한 작품 ID를 사용 | 정식 목록 복사본 없음. 신규 작품은 `agent_works.csv`에 임시 ID로 기록 |
| `agents/ani_me;nu/src/ani_menu_agent/agents/crawlers/{character,guide,people,music,humor,curation,review}.py` | 해당 없음 | 참조만 | RAG 대상별 도메인 규칙 확인 | 각 파일은 독립 실행 코드가 아니라 공통 base의 프로필 선언. 규칙만 새 모델/정책 경계에 반영하고 코드 복사는 하지 않음 |
| `agents/ani_me;nu/src/ani_menu_agent/agents/crawlers/{event,goods,news}.py` | 해당 없음 | 참조만 | 기존 기간 중요 유형의 경계 확인 | 이번 RAG 범위에서 이전하지 않음. 기존 역할 매핑과 수집 경로 보존 |
| `agents/ani_me;nu/src/ani_menu_agent/agents/crawlers/base.py` | `src/ani_rag/adapter.py` | 재구현(일부 경계) | source-scoped search/read protocol 독립 정의 | LangGraph 실행, discovery, 품질 게이트, 이미지 캡처, 저장은 이관하지 않음 |
| 없음 (신규 구현) | `src/ani_rag/models.py`, `src/ani_rag/settings.py`, `src/ani_rag/profiles.py`, `src/ani_rag/interfaces.py` | 생성 | 독립 근거 모델, 7개 유형 정책, allowlist 가드, 작품 연결/저장 포트 | Python 표준 라이브러리 기반. `ani_menu_agent` import, DB, 네트워크 접근 없음 |
| `agents/ani_me;nu/src/ani_menu_agent/discovery.py`, `sources/page_collector.py`, `sources/http_policy.py` | 해당 없음 | 보류 | HTTP/RSS/API 수집이 아닌 승인 MCP 도구 경로 검토 | 기존 discovery는 feed/API/HTML 네트워크 수집과 기존 모델·작품 카탈로그에 연결. page collector는 호출 시 네트워크 접근. 독립 MCP request/response Protocol은 `src/ani_rag/adapter.py`에 만들었으나 실제 MCP 호출 흐름은 보류 |
| `agents/ani_me;nu/src/ani_menu_agent/sources/models.py`, `sources/registry.py` | `src/ani_rag/settings.py` | RAG 구조로 재구현 | Source 설정의 작은 subset만 독립화 | 기존 Source 모델이 `ani_menu_agent.models.ContentType`에 의존. 별도 allowlist 로더는 독립이며 MCP에 연결하지 않음 |
| `agents/ani_me;nu/src/ani_menu_agent/extraction.py` | 해당 없음 | 보류 | 사실/작품 매핑 로직 후보 조사 | extraction은 설정·generation·정책·레지스트리, 기존 Source 모델과 기간 날짜 규칙에 연결 |
| `agents/ani_me;nu/src/ani_menu_agent/work_catalog.py`, `work_aliases.py` | `src/ani_rag/interfaces.py` | RAG 구조로 재구현(Protocol만) | 작품 연결 경계 정의 | 구현은 기존 `ani_menu_agent.models`, 출처 registry, alias 모듈과 CSV에 결합되어 보류. 새 resolver는 공유 카탈로그 조회만 정의하며 ID 발급 없음 |
| `agents/ani_me;nu/src/ani_menu_agent/storage.py`, `agents/content_generation/`, `agents/reviewer/`, `pipelines/` | 해당 없음 | 보류 | 공통 저장·downstream 경계 확인 | storage import는 이미지 렌더러, 설정, SQLite, PostgreSQL 및 저장 모델과 결합. `AgentRepository` 생성자는 DB 초기화를 유발하므로 복사·호출하지 않음. 생성·검수는 공유 downstream으로 남김 |

## 신규 파일 기록

신규 기반 및 이번 출처 조사 파일은 `README.md`, 본 manifest, `MCP_PILOT_PLAN.md`, 최소 `pyproject.toml`, 빈 `config/sources.evergreen.json`, 검토 전용 `config/sources.candidates.json`, 그리고 `src/ani_rag/`의 패키지·모델·어댑터·설정·프로필·인터페이스 파일이다. 기존 프로젝트의 코드나 레퍼런스 파일을 복사한 것은 없다. 원본 복사 날짜·해시는 해당하지 않는다. 공유 레퍼런스의 갱신 책임은 원본 프로젝트이며, 별도 스냅샷 생성 시 새 기록을 추가한다.

## 2026-11-08 생성·검수 서비스 포팅 기록

| 원본 경로 | 대상 경로 | 처리 | 필요한 이유 | 의존성/주의 |
|---|---|---|---|---|
| `ani_me;nu/src/ani_menu_agent/generation.py`, `agents/content_generation/{graph.py,art_director.py,detail_writer.py,render_node.py,renderer.py}` | `src/ani_rag/model_services.py` | 참조만 + RAG 방식으로 재구현 | 카드 카피·상세글 생성, 주장-근거 연결, 별도 검수용 산출물이라는 downstream 계약을 반영 | legacy 그래프·모델·저장소·레이아웃 런타임은 이식하지 않음. 구조화 모델 호출은 명시적 provider 주입 필요 |
| `ani_me;nu/src/ani_menu_agent/agents/reviewer/{agent.py,graph.py,prompt.py}`, `quality.py`, `review_rules.py` | `src/ani_rag/model_services.py`, `review.py`, `quality.py`, `workers.py` | 참조만 + 독립 정책으로 재구현 | 결정론적 게이트 뒤 별도 검수 및 pass/revise/evidence/manual-visual-review/archive 분기를 유지 | `AgentRepository`, legacy state/graph/이미지 시각 검수는 복사하지 않음. RAG reviewer는 현재 이미지 메타데이터만 확인하며 pass 후보도 자동 통과시키지 않음 |
| 없음 (신규) | `src/ani_rag/runtime.py` | 생성 | caller-owned DB connection, RAG repository, retriever, generation/review one-item worker를 명시적으로 조립 | SQL/DDL을 실행하거나 connection을 열지 않음. worker 실행도 시작하지 않음 |
| OpenAI 공식 Responses Structured Outputs 계약 | `pyproject.toml`, `src/ani_rag/model_services.py` | 의존성/어댑터 추가 | strict JSON Schema로 generation/review 응답을 제한 | API key/model을 호출자가 공급. `.env`를 읽지 않고 API 호출도 하지 않음 |
| 없음 (신규) | `docs/PROJECT_GUIDE.md`, `docs/GENERATION_REVIEW_PORT.md`, `docs/MCP_CONNECTION_SETUP.md` | 문서화 | 현재 구조, 처리 순서, 실제 기간 에이전트와의 차이, MCP 도구 목록 및 승인 선행 조건 설명 | endpoint 도구 목록만 확인. source 검색·읽기, allowlist 등록, worker 실행은 하지 않음 |

이번 변경에서도 기존 파일을 `ani_me;nu`에서 복사하거나 이동하지 않았다. 해당 프로젝트 generation/review는 읽기 전용 참고만 했으며, 그 작업 트리에 기존 변경 상태가 있으므로 보존했다.

## 2026-11-08 작품 ID 매칭 보강 및 최소 검수 설정

| 대상 경로 | 처리 | 변경 내용 | 의존성/주의 |
|---|---|---|---|
| `src/ani_rag/catalog.py`, `interfaces.py`, `models.py` | 독립 구현 | exact match 실패·모호 시 원문과 제한된 카탈로그 후보를 LLM에 전달하는 resolver, 근거 인용이 포함된 신규 작품 후보 계약 추가 | matcher 모델을 주입해야 실제 LLM 식별이 가능. low-confidence/원문 미포함 식별은 unresolved 유지 |
| `src/ani_rag/collector.py`, `structured_mcp.py` | 독립 구현 | URL형/구조화 응답 제목·본문·외부 ID를 식별 요청에 넣고 결과 상태/방법을 evidence metadata에 보존 | MCP 호출은 하지 않음. 기존 프로젝트 경로와 데이터베이스 접근 없음 |
| `src/ani_rag/storage/schema.sql`, `storage/sqlite_core.py`, `runtime.py` | RAG 전용 확장 | 작품 정보·근거 문서·generation backlog 저장. 신규 작품 ID는 Kitsu ID 또는 정규화 제목 기반 결정적 임시 ID | 스키마 미적용. RAG `menu_work_id`는 ERD `entity_id`와 별개. authoritative `topic_id`는 NULL 유지 |
| `src/ani_rag/agents/content_generation/renderer.py`, `model_services.py`, `workers.py`, `quality.py`, `agents/reviewer/prompts.py` | 최소 파일럿 동작 반영 | 이미지 미등록 시 text-only gradient PNG fallback, 생성 단계 검수 제거, reviewer pass/revise/reject 및 캐러셀 가독성 게이트로 범위 축소 | 이미지 수집·권리 확인은 미구현. 저작권·사실성 점수는 현 단계 reviewer 차단 기준에서 제외 |
| `README.md`, `IMPLEMENTATION_STATUS.md`, `docs/WORK_ID_MATCHING.md` | 문서 갱신 | 매칭/신규 ID 원자 등록 흐름과 검수 범위, 미해결 ERD identity를 기록 | 실행 완료로 표현하지 않음 |

이번 변경에서도 `ani_me;nu` 파일을 수정·삭제·이동하지 않았다. `.env`, DB, WAL/SHM, 로그, 생성 결과는 읽지 않았으며, worker/MCP/model/DB/CLI/migration/test는 실행하지 않았다.

## 7개 유형별 기존 프로필 처리 결정

| 원본 경로 | 처리 | 독립 프로젝트에서 반영한 업무 규칙 |
|---|---|---|
| `src/ani_menu_agent/agents/crawlers/character.py` | 참조만 | 작품을 먼저 명확히 연결하고 인물 정체성·프로필·생일·관계를 작품 근거와 함께 수집 |
| `.../guide.py` | 참조만 | 작품 맥락을 유지하며 이야기·회차·세계관·용어·시청 순서를 정리 |
| `.../people.py` | 참조만 | 성우·감독·작가·스튜디오·아티스트 크레딧을 수집하고 발행/유효 시점을 보존 |
| `.../music.py` | 참조만 | OST·주제가·앨범·트랙·아티스트 크레딧에 발매일을 별도 보존 |
| `.../humor.py` | 참조만 | 커뮤니티 유머/토론은 `community_signal`, 출처가 확인된 제작 비하인드는 `production_behind_scenes`로 분리 |
| `.../curation.py` | 참조만 | 순위·투표·추천은 사실이 아닌 편집/집계 결과로 두고 조사 기간·표본·집계 방법·원문 맥락 기록 |
| `.../review.py` | 참조만 | 리뷰는 객관적 사실로 합치지 않고 저자 귀속 상태·주장·인용 발췌를 유지 |
| `src/ani_menu_agent/agents/crawlers/base.py` | 재구현(일부 경계) | 독립 `adapter.py`에서 출처 ID가 필요한 검색/읽기 요청과 비동기 MCP 프로토콜만 새로 정의. LangGraph 흐름·수집·품질 게이트·저장은 재구현하지 않음 |
| `config/source_type_map.json` | 참조만 | 유형별 기존 endpoint 후보를 이해하는 참고 자료. source ID, 상태, 권한을 새 allowlist로 복사하지 않음 |

독립 구현 파일: `src/ani_rag/models.py`(RAG 유형, 근거 유형, 원문/발췌와 작품 연결 계약), `src/ani_rag/adapter.py`(MCP search/read 프로토콜과 요청/응답 형식), `src/ani_rag/settings.py`(RAG 전용 JSON allowlist 로더). 새 파일은 모두 자체 정의이며 `ani_menu_agent` 모듈을 import하지 않는다.

## 확인한 기존 구조

- 기존 패키지: `agents/ani_me;nu/pyproject.toml`, `src/ani_menu_agent`; Python `>=3.12,<3.13`, Hatchling 빌드, 런타임 의존성에 HTTP 수집·Pydantic·LangGraph·OpenAI·PostgreSQL·이미지 라이브러리 등이 포함됨.
- 출처 설정은 `config/sources.json`, 유형 매핑은 `config/source_type_map.json`에 있다. `config/source_lane_map.json`, `data/sources.json`, `data/source_type_map.json`은 확인 시 존재하지 않았다.
- 기존 파이프라인 문서는 evergreen를 MCP 기반으로 설명하지만, 실제 MCP/RAG 실행 코드는 없다.
- RAG가 맡기로 한 유형은 `character`, `guide`, `people`, `music`, `humor`, `curation`, `review`; 기간 중요 역할은 현재 `news`, `event`, `goods`에 가깝다. 각 문서의 발행일·유효 기간은 콘텐츠 유형과 별개로 기록한다.
- 기존 10개 크롤러는 서로 다른 크롤링 엔진이 아니라 `base.py`를 공유하는 역할 선언이다. 공식/참조 자료, discovery, 이미지 정책, 저장 및 생성 경로와 결합되어 있다.
- import만으로 실행이 되는지는 모듈 전체를 실행해 확인하지 않았다. import/생성자의 정적 의존 관계상 `cli.py` 및 `storage.py`는 DB 초기화 경로를 포함하고, crawler base는 저장소·이미지·작품 매칭에 결합된다. `page_collector`/`discovery`는 함수를 호출해야 네트워크 요청을 수행하는 수집 모듈이다. 이번 작업에서는 어떤 모듈도 import하거나 실행하지 않았다.

## 다음 컷오버에서 제거 검토 가능한 기존 경로

RAG 쪽의 실제 MCP 수집, 근거 저장/DB 계약, 7개 유형별 검수 및 downstream 적재, 운영 관측이 준비되고 기존 자료와 중복 없이 이행되는지 확인된 뒤 `ani_me;nu`의 해당 유형 프로필을 실행 경로에서 분리할 수 있다. 우선 제거 후보는 `character.py`, `guide.py`, `people.py`, `music.py`, `humor.py`, `curation.py`, `review.py`의 타입 라우팅이며, 이 작업에서는 하나도 수정·삭제하지 않았다. `base.py`는 event/goods/news 등을 포함한 기간 수집기와 공유하므로 RAG만을 이유로 제거할 수 없다. `source_type_map.json`도 공유 endpoint 유형표이므로 lane별 운영 설정과 이행이 확인되기 전에는 제거할 수 없다. 컷오버는 별도 승인 작업이다.

## 2026-11-07 세부 파일 결정표

아래 원본 경로는 `agents/ani_me;nu/` 기준이다. 오늘 재확인한 `ani_me;nu` 작업 트리에는 기존 상태 항목 189개가 있었다. 목록과 내용을 조사했지만 이 작업에서 원본을 수정하지 않았다.

| 원본 | 대상 | 결정 | 이유·의존성 |
|---|---|---|---|
| `src/ani_menu_agent/agents/crawlers/character.py` | `src/ani_rag/profiles.py` | 참조만 | 작품/캐릭터 구분과 작품 연결 전 승격 보류 규칙만 반영 |
| `src/ani_menu_agent/agents/crawlers/guide.py` | `src/ani_rag/profiles.py` | 참조만 | 작품·회차 맥락과 원문 위치 보존 규칙만 반영 |
| `src/ani_menu_agent/agents/crawlers/people.py` | `src/ani_rag/profiles.py`, `models.py` | 참조만 | 일반 프로필과 작품별 크레딧 분리, 발행/유효 시점 보존 |
| `src/ani_menu_agent/agents/crawlers/music.py` | `src/ani_rag/profiles.py`, `models.py` | 참조만 | 작품·곡·아티스트·앨범 구분 및 발매 시점 보존 |
| `src/ani_menu_agent/agents/crawlers/humor.py` | `src/ani_rag/profiles.py`, `models.py` | 참조만 | 커뮤니티 신호와 출처 확인 비하인드를 다른 근거 유형으로 둠 |
| `src/ani_menu_agent/agents/crawlers/curation.py` | `src/ani_rag/profiles.py`, `models.py` | 참조만 | 투표/랭킹을 사실이 아닌 집계로 두고 조사 기간·표본·방법·맥락 보존 |
| `src/ani_menu_agent/agents/crawlers/review.py` | `src/ani_rag/profiles.py`, `models.py` | 참조만 | 저자 귀속·주장·발행 시각·발췌를 객관적 사실과 분리 |
| `src/ani_menu_agent/agents/crawlers/base.py` | `src/ani_rag/adapter.py` | RAG 구조로 재구현(Protocol 경계만) | LangGraph, discovery, 기존 모델·저장소·작품 카탈로그·이미지 경로에 결합. 새 프로젝트는 source-scoped MCP 요청/응답만 정의 |
| `src/ani_menu_agent/models.py` | `src/ani_rag/models.py` | 참조만 | 생성/카드 모델 대신 RAG evidence에 필요한 필드만 독립 정의 |
| `src/ani_menu_agent/extraction.py` | 해당 없음 | 보류 | 설정·generation·정책·기존 Source 모델 및 기간 이벤트 추출과 결합 |
| `src/ani_menu_agent/source_policy.py` | `src/ani_rag/settings.py` | 참조만 | 기존 source 지원 검증은 참고. 승인·도구·유형 접근만 새로 검사 |
| `src/ani_menu_agent/work_catalog.py`, `work_aliases.py` | `src/ani_rag/interfaces.py` | RAG 구조로 재구현(Protocol만) | 기존 매칭은 모델·registry·CSV 경로에 결합. 새 resolver는 공유 카탈로그 조회만 정의하고 ID를 만들지 않음 |
| `src/ani_menu_agent/sources/models.py`, `sources/registry.py` | `src/ani_rag/settings.py` | RAG 구조로 재구현 | 기존 Source 모델이 `ani_menu_agent.models.ContentType`에 의존하므로 독립 allowlist 로더를 정의 |
| `src/ani_menu_agent/storage.py` | `src/ani_rag/interfaces.py` | RAG 구조로 재구현(Protocol만) | SQLite/PostgreSQL 초기화, 이미지·렌더·큐와 결합. 새 저장 포트는 연결이나 스키마 없음 |
| `config/sources.json`, `config/source_type_map.json` | `config/sources.evergreen.json` | 참조만 | 유형 매핑 후보 수: character 17, guide 12, people 20, music 19, humor 23, curation 5, review 3. MCP server 별칭과 tool 지원을 확인하지 못해 source를 복사/등록하지 않음 |
| `catalog/menu_works.csv`, `catalog/menu_work_entries.csv`, `ani_me;nu/data/reference/agent_works.csv` 및 보조 레퍼런스 | 공유 경로 참조, `interfaces.py` resolver port | 정식 목록은 읽기 전용. 신규 임시 ID는 `agent_works.csv`에 기록 | RAG와 기간형이 같은 작품 ID 정본 및 임시 번호 규칙을 사용. `work_theme_songs.csv`는 reference 전용 및 AI 학습 금지 안내가 있어 별도 확인 전 RAG 본문 근거로 복사/색인하지 않음 |
| `src/ani_menu_agent/cli.py`, `source_discovery.py`, 이미지·렌더 모듈, `agents/content_generation/`, `agents/reviewer/` | 해당 없음 | 보류 | 실행/DB/이미지 경로에 결합되거나 공통 downstream이므로 이 작업 범위에서 가져오지 않음 |

`interfaces.py`는 실행 구현이 아닌 `WorkCatalogResolver`와 `EvidenceRepository` Protocol이다. `profiles.py`는 기존 정규식 신호를 복사하지 않고 유형별 근거 종류와 보존 맥락을 재표현한다. `settings.require_source_access`는 등록된 source/tool/content-type/status만 확인하며 MCP 호출은 하지 않는다.

추가 확인: 프로젝트 루트의 `config/source_lane_map.json`은 없다. `.env`는 루트 `.gitignore`의 `**/.env` 규칙에 따라 ignore되며 Git 추적 대상이 아님을 경로만 확인했다. `.env` 내용은 읽거나 출력하지 않았다.

## 2026-11-07 출처 후보/MCP 점검

- `config/sources.candidates.json`은 요청된 53개 ID의 이름, URL, 기존 상태·역할·수집 방식, 7개 RAG 유형 매핑, 기존 메모를 원본 설정에서 읽어 기록한다. 우선순위는 기존 role/status 기반의 후보 검토 순서이며 승인 표식이 아니다. source ID는 원본에서 가져왔고 새 ID는 만들지 않았다.
- 각 후보의 MCP 필드는 `mcp_server: "unknown"`, `search_tool_status: "unconfirmed"`, `read_tool_status: "unconfirmed"`다. 런타임 tool registry에는 일반 웹 검색/열기 도구가 있었지만 후보 source ID에 등록된 source-specific MCP 서버/도구는 없었다. 이 일반 도구를 후보 서버명으로 위장하지 않았다.
- MCP 서버 연결, 도구 실행, 후보 URL 접근, 검색/읽기, 대량 수집은 수행하지 않았다. 따라서 이 점검은 환경에 실제 MCP 연결이 없는지 단정하는 것이 아니라, 현재 제공된 도구 목록에서 source별 연결을 확인하지 못했다는 기록이다.
- `sources.evergreen.json`은 그대로 빈 승인 설정이다. 후보 인벤토리는 해당 실행 설정에 자동 로드되지 않는다. 사용 전에 실제 MCP 서버와 tool 이름, 지원 출처 범위가 확인되면 별도 검토 후 source allowlist에 필요한 항목만 올린다.

## 2026-11-07 독립 실행 경계에 맞춘 기본 파일 복사

사용자가 `ani_rag`와 `ani_me;nu`를 서로 참조하지 않는 독립 프로젝트로 확정했으므로, 기존 manifest의 “공유 작품 레퍼런스 참조” 및 “공통 downstream” 결정은 이 addendum에서 갱신한다. 원본 프로젝트 파일은 이동·수정하지 않았다.

| 원본 | 대상 | 처리 | 범위/주의 |
|---|---|---|---|
| 작품 ID 목록과 매칭 보조 자료 | 저장소 루트 `catalog/` 및 `ani_me;nu/data/reference/` | 정본은 공유 경로에서 읽음 | `ani_rag/data/reference/`에 `menu_works.csv`·`menu_work_entries.csv` 복사본을 새로 만들지 않음. 각 에이전트는 정본 카탈로그를 수정하지 않음. 신규 임시 작품은 `agent_works.csv`에 둠. |
| 기존 `prompts.py`, `agents/content_generation/detail_writer.py`, `agents/reviewer/prompt.py`, `policy.py`, `review_rules.py` | `ani_rag/docs/COPIED_CONTENT_BASELINE.md` | 읽기 전용 텍스트 스냅샷 복사 | 생성 카피·상세글·번역·검수 규칙의 현재 기준 보존. 실행 모듈이 아니며 ani_rag 런타임에서 import하지 않음. 원본 경로와 각 파일 SHA-256 포함. |

이 복사로 ani_rag의 생성·검수 그래프가 구현된 것은 아니다. 기존 생성·검수 실행 코드의 의존성은 `nodes.py`, `storage.py`, 공통 모델/품질/이미지/카탈로그 코드에 걸쳐 있고, `storage.py`는 DB와 렌더 저장을 포함한다. 이를 통째로 복사하지 않았다. 다음 구현은 ani_rag 내부에 콘텐츠 생성·검수 패키지와 필요한 로컬 데이터 계약을 포팅하는 작업이며, 기간 프로젝트 패키지 import, 경로 폴백, DB/로그/워커 사용 없이 설계해야 한다.

이번 작업에서는 MCP 서버 연결·호출, 데이터베이스·로그 열람, 테스트 실행을 하지 않았다. 기존 worker는 중지 상태로 유지한다.

## 2026-11-07 independent pipeline implementation addendum

This addendum records this pass. All pre-existing `ani_rag` untracked files were present at task start. The root working tree had 617 existing status entries; none were reverted or cleaned. No `ani_me;nu` file was modified. Existing code and baseline were read as reference only; no production code was copied verbatim.

| Source | Target | Decision | Reason / caution |
|---|---|---|---|
| Existing local snapshot CSVs under `data/reference/` | `src/ani_rag/catalog.py` | Reimplement | Reads only the local snapshot; exact titles, external IDs, verified aliases. Character lookup is separate and ambiguous without a resolved work. No theme-song evidence, fuzzy inference, or new ID issuance. |
| Legacy seven crawler profiles and `agents/crawlers/base.py` | `src/ani_rag/profiles.py`, `collector.py`, `adapter.py` | Reference / reimplement | Preserve domain lanes and approved-source MCP boundary; no old imports or auto-discovery. |
| Legacy `policy.py`, `review_rules.py`, `prompts.py`, generation and reviewer prompts | `docs/COPIED_CONTENT_BASELINE.md`, `src/ani_rag/prompts.py`, `quality.py`, `review.py` | Baseline reference / selective reimplementation | The snapshot remains a baseline. RAG prompts and structural gates are native; event/goods-only rules were not copied. Existing four-failure reviewer limit is recorded as the initial RAG cap pending policy review. |
| Legacy content generation and reviewer graphs/nodes | `src/ani_rag/workers.py`, `queues.py` | Reimplement | Separate one-item queue steps with injected services. No graph, provider, queue backend, worker loop, or automatic start. |
| Legacy `agents/content_generation/renderer.py` | `src/ani_rag/rendering.py` | Reimplement | Pillow-only text-free art composition; frontend copy is separate. Does not download or select images and cannot establish rights. |
| Existing MCP source candidates/settings | `config/sources.candidates.json`, empty `config/sources.evergreen.json`, `src/ani_rag/settings.py` | Reference / reimplement | No candidate is promoted. Exact host checks are implemented; public endpoint tool names were inventoried, but no persistent MCP aliases, adapters, or approved source hosts are present. |
| Existing `ani_me;nu` source settings and type map | No copy | Reference only | The source file and content-type map are not an evergreen approval list; originals left unchanged. |
| Relational storage requirement | `src/ani_rag/storage/schema.sql`, repository protocols | New design / defer adapter | SQLite-compatible DDL is a package resource proposal only. No database was opened, created, or migrated; database and shared-queue choice remain unresolved. |
| Existing image and work reference data | `data/reference/SNAPSHOT_MANIFEST.md` | Existing snapshot reference | Snapshot date, source paths, hashes, and refresh responsibility are recorded there. No second snapshot was created. |

Files added/modified in this pass: `IMPLEMENTATION_STATUS.md`; `README.md`; `pyproject.toml`; `src/ani_rag/{catalog,collector,content,prompts,quality,queues,rendering,review,structured_mcp,workers}.py`; updates to `src/ani_rag/{adapter,models,settings}.py`; and `src/ani_rag/storage/{__init__.py,schema.sql}`. Static syntax/import-path inspection only; no tests, MCP, DB, CLI, or workers were run.
Additional protocol detail from the implementation pass: `src/ani_rag/structured_mcp.py` and `StructuredCollectionWorker` handle entity/property responses with qualifiers/references as structured evidence; `adapter.py` handles URL-based pages. A future server record must provide its real alias, exact observed tool names in `tool_bindings`, allowed exact hosts (where URL resources apply), and approved source/type scope. The current source registry remains empty; no guessed Firecrawl/Wikidata tool name was added.

## 2026-11-08 public MCP endpoint inventory correction

A one-off Streamable HTTP `initialize` + `tools/list` request confirmed tool names at the public Firecrawl keyless endpoint and the official Wikidata MCP endpoint (protocol version `2025-03-26`). Firecrawl returned `firecrawl_scrape`, `firecrawl_search`, `firecrawl_parse`; Wikidata returned `search_items`, `search_properties`, `get_statements`, `get_statement_values`, `get_instance_and_subclass_hierarchy`, `execute_sparql`. No `tools/call` was made. This corrects earlier statements that no live public endpoint inventory existed; it does not establish a persistent ani_rag runtime alias, tool transport, source host policy, or approval. `sources.evergreen.json` remains empty.

## 2026-11-07 MCP-to-review flow wiring addendum

This pass re-read the target tree and confirmed all target files were already untracked. No pre-existing target file was removed or moved. The old project was read only; its status remained 189 entries.

| Target path | Decision | Added/changed responsibility | Runtime limit |
|---|---|---|---|
| `src/ani_rag/adapter.py`, `collector.py` | Hardened URL MCP contract | Exact tool/server response attestation, host-scoped requests, redirect-chain/result checks, and evidence normalization. | No concrete transport. The adapter must enforce redirect scope before following redirects and prove the live tool inventory. |
| `src/ani_rag/structured_mcp.py` | Hardened structured MCP contract | Structured result identity envelope; statement item ID, property value, qualifiers and references are preserved without URL-page coercion. | No live server parser/client. |
| `src/ani_rag/retrieval.py` | New reimplementation | Deterministic chunks/locators, source-bearing retrieved chunks, lexical re-ranking, candidate repository and generation backlog contracts. | Candidate retrieval DB implementation is absent; no embeddings/vector store. |
| `src/ani_rag/interfaces.py`, `collector.py`, `structured_mcp.py` | Updated contract | Evidence upsert must atomically record a pending generation backlog request with the document. | No transaction/repository implementation. |
| `src/ani_rag/workers.py`, `content.py`, `review.py`, `quality.py` | Updated orchestration/contracts | Generation worker calls retrieval, carries retrieval/chunk IDs to review, and preserves retrieval query on revision; reviewer gets docs, chunks and image metadata. | One item at a time; no backend/model/loop. |
| `src/ani_rag/storage/schema.sql` | Schema design extension | Added generation work/backlog, chunk/retrieval/hit tables and claim-to-chunk links. | SQLite-compatible proposal only; not applied. |
| `README.md`, `IMPLEMENTATION_STATUS.md` | Reconciled documentation | Replaced readiness claims with per-module status and actual blockers. | This report is static inspection, not operational verification. |

No existing `ani_me;nu` source file or database was modified. No `.env` content, DB/WAL/SHM, log, or generation result was read. No GitHub operation, MCP call, worker, CLI, migration, or test was run. Static AST/import path checks are documented in the completion report.

## 2026-11-07 boundary hardening follow-up

| Target path | Change | Remaining boundary |
|---|---|---|
| `src/ani_rag/collector.py` | Enforce result count, response identity, exact source hosts, redirect attestation, and final-URL linkage to the searched URL. | A future transport must enforce host scope before following each redirect; response checks alone cannot prevent an out-of-scope request. |
| `src/ani_rag/structured_mcp.py` | Enforce result count, response identity, and requested property IDs. | No live tool inventory client or server-specific parser exists. |
| `src/ani_rag/catalog.py` | Refuse catalog roots outside `ani_rag/data/reference/`. | Snapshot refresh responsibility and workflow remain operational decisions. |
| `src/ani_rag/quality.py` | Require a resolved work ID on character and guide drafts. | Semantic correctness still needs the independent reviewer implementation. |
| `README.md`, `IMPLEMENTATION_STATUS.md` | Correct the packaged schema path and document the execution blockers. | The schema is design-only; reviewer evidence requests remain blocked until an operator chooses an approved source and bounded query. |

Static AST/JSON/TOML parsing succeeded. No tests, package imports, MCP calls, workers, CLI, or DB commands were run.

## 2026-11-07 SQLite repository implementation

| Target path | Decision | Responsibility | Runtime limit |
|---|---|---|---|
| `src/ani_rag/storage/sqlite_core.py` | New independent implementation | Implements source registration for explicitly approved records, atomic evidence/outbox writes, bounded relational candidate reads, backlog status, idempotent queue enqueue, atomic queue claim, and queue event persistence. | Accepts an already-open caller-owned SQLite connection; it does not open/create a database or execute schema. Connection provisioning and foreign-key enforcement are caller responsibilities. |
| `src/ani_rag/storage/sqlite_workflow.py` | New independent implementation | Persists retrieval traces/chunks, drafts/claims, review history, and explicitly registered image provenance. | Does not search/download images or verify rights. |
| `src/ani_rag/storage/schema.sql` | Design update | Adds preserved evidence claims, retrieval metadata, draft work/chunk links, claim order, and generation backlog error detail required by the adapters. | DDL remains unapplied. |
| `README.md`, `IMPLEMENTATION_STATUS.md` | Status correction | Distinguishes implemented SQLite core from missing runtime provisioning and missing draft/review/image adapters. | No database was opened or initialized for this work. |

## 2026-11-08 ERD dataset alignment

| Source | Target | Action | Reason / boundary |
|---|---|---|---|
| User-supplied ERD screenshots | `docs/ERD_DATASET_MAPPING.md` | Reference and map | Records table/field alignment, identity gaps, screenshot precedence, and current snapshot findings. |
| Existing RAG data contracts and temporary schema | `src/ani_rag/models.py`, `content.py`, `quality.py`, `storage/schema.sql`, `storage/sqlite_core.py`, `storage/sqlite_workflow.py` | Align in place | Added ERD source/document/content/card/source/tag/entity/relation/judgment fields and adapted the temporary SQLite persistence code. No schema was applied. |
| `ani_me;nu/data/reference/` | No copy; reference only | Read-only use | Keep the shared work snapshot as the source of `menu_work_id`; it is not the ERD `entity_id` catalog. No `ani_me;nu` file was changed. |
| Existing candidate sources / empty RAG allowlist | No source activation | Preserve state | No MCP server/tool/source verification was performed as part of dataset alignment. |

The screenshot contract supersedes inconsistent counts/field descriptions in the older `ani_me;nu/docs/ERD_CONTENT_REVIEW.md` for this review. Work/entity IDs, topic/subtopic IDs, and published content IDs are not synthesized. No DB/log/generated artifact, `.env` contents, or existing database was opened. No test, worker, CLI, MCP call, or migration was run.

## User-requested read-only recovery database inspection (2026-11-08)

The user explicitly asked for a dataset review of `ani_me;nu/data/generation_continuous_recovery_20261101.sqlite3`. SQLite was opened with `mode=ro` and `PRAGMA query_only=ON`. The review inspected table metadata, aggregate counts, key-field coverage, request/work-link joins, and JSON field names. JSON payload strings were parsed in memory for field-name summaries; generated prose was not printed or semantically reviewed. No image blob bytes, log contents, `.env` values, or database content was changed. No schema, worker, MCP, CLI, migration, or test was run. This entry supersedes earlier manifest statements that no database had been opened, only for this user-designated file and this read-only inspection.

Correction/addendum: the recovery DB's JSON payload field names were inspected in memory for mapping. This establishes that the existing `topic` value is a text label and the package includes source/fact/image/work-link structures. Generated prose values were not displayed or evaluated. See the ERD mapping document for the precise scope and counts.
