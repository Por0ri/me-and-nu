# me_nu

AI 에이전트와 추천 알고리즘을 활용하는 초개인화 큐레이팅 플랫폼입니다.

이 문서의 아래 전체 API 표는 [Notion API V1.1 명세서](https://app.notion.com/p/94604c45a961835bbd1601fc3ab15ac0)의 목표 목록입니다. 현재 구현된 로컬 범위는 다음 절에 따로 표시했습니다. 실제 공개 API 경로에는 끝의 `/`를 붙이지 않습니다. 세부 요청·응답의 기준은 각 Notion API 상세 페이지입니다.

## 현재 구현된 V1 로컬 범위

2026-10-28 현재 구현 ERD는 38개 테이블의 [current_schema.sql](../docs/erd/current_schema.sql)입니다. DB 연결 없이 `python scripts/export_current_schema.py`로 재생성하고 `--check`로 일치 여부를 검사합니다. [온보딩 V2 계약·migration](../docs/ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md), [캐러셀 DB·API 확장](../docs/CAROUSEL_DB_API_INTEGRATION_2026-10-28.md), [과거 DB 확인 기록](../docs/ERD_API_ALIGNMENT_2026-10-27.md)을 구분하세요. 현재 OpenAPI는 공개 V1 36개·상태 확인 3개이며 개발용 API를 켜면 6개가 추가되어 총 45개입니다.

| 영역 | Endpoint |
| --- | --- |
| 개발용 인증 | `POST /api/v1/dev/auth/register`, `POST /api/v1/dev/auth/login` |
| 개발용 Agent 결과 | `GET /api/v1/dev/agent-runs`, `GET /api/v1/dev/agent-runs/{agentRunId}` |
| 개발용 Agent 발행 | `POST /api/v1/dev/agent-runs/{agentRunId}/publish`, `POST /api/v1/dev/agent-runs/{agentRunId}/publish-preview` |
| 세션 | `GET /api/v1/auth/session`, `POST /api/v1/auth/logout` |
| 소셜 OAuth (API-001·002) | `POST /api/v1/auth/login/{provider}`, `GET /api/v1/auth/login/{provider}/callback` — 카카오·네이버. 앱 키와 `oauth_attempt` 마이그레이션 필요, 기본 비활성. [설정·백엔드 단독 검증](../docs/OAUTH_T02_1_SETUP.md) |
| 정책·온보딩 | `GET /api/v1/policies`, `GET /api/v1/onboarding/works`, `POST /api/v1/onboarding/preference-options`, `POST /api/v1/onboarding/complete` |
| 내 정보 | `GET/PATCH /api/v1/users/me`, `GET/PATCH /api/v1/users/me/consents` |
| Topic·Subtopic | `GET /api/v1/topics`, `GET /api/v1/topics/{topicId}/subtopics`, `GET /api/v1/subtopics/{subtopicId}` |
| 내 관심사 | `GET/POST /api/v1/me/topics`, `GET /api/v1/me/topics/{topicId}/subtopics`, `PUT/DELETE /api/v1/me/topics/{topicId}/subtopics/{subtopicId}` |
| 피드·콘텐츠 | `GET /api/v1/topics/{topicId}/feed`, `GET /api/v1/contents/{contentId}?topicId=...`, `GET /api/v1/contents/{contentId}/slides/{page}/image?topicId=...` |
| 반응·저장 | `PUT/DELETE /api/v1/contents/{contentId}/preference`, `/like`, `/bookmark` |
| 저장 목록 (API-041) | `GET /api/v1/users/me/bookmarks?topicId=...&limit=...&cursor=...` |
| AI 대화 (API-048·052·053) | `POST /api/v1/chat/sessions`, `POST /api/v1/chat/sessions/{sessionId}/messages`, `GET /api/v1/chat/jobs/{jobId}` — `backend/.env`의 `LLM_KEY` 필요. 질문은 앞뒤 공백 제외 최대 500자이며 현재 분야 공개 콘텐츠 안에서만 근거를 찾아 답합니다. |
| 알림 (API-107·108·109) | `GET /api/v1/users/me/notifications`, `GET /api/v1/users/me/notifications/unread-count`, `PATCH /api/v1/users/me/notifications/{notificationId}` — 운영공지(`system_notice`)만 목록·미읽음 수·읽음 처리에 포함합니다. 환영 공지를 생성하며, 기존 다른 유형 알림은 보존하되 공개 API에서 제외합니다. 실제 운영공지 공급은 후속 작업입니다. |

사용자와 콘텐츠 데이터는 PostgreSQL을 사용합니다. 세션 저장소는 `SESSION_BACKEND=redis`로 Redis를 선택할 수 있으며 미설정 시 기존 PostgreSQL 세션을 사용합니다. 개발용 가입·로그인과 Agent 결과 조회는 `ENABLE_DEV_API=true`에서만 등록됩니다. Agent 결과 조회는 로컬 요청과 인증된 개발 사용자로 제한되며 Swagger의 **개발용 Agent 결과**에 표시됩니다. `ENABLE_DEV_API=true`와 `ENABLE_DEV_AUTH_BYPASS=true`를 함께 설정하면 로컬 루프백 요청을 준비된 데모 사용자로 처리합니다. 이때 보호 API 테스트에 로그인·세션 쿠키·CSRF 헤더가 필요하지 않습니다. 우회를 `false`로 되돌리면 일반 세션 인증이 적용됩니다. 영화 Agent 실행 결과가 승인되고 사실 확인 항목이 없으면 저장 직후 공개 Content 피드에 자동 발행됩니다. 탈락하거나 사실 확인이 필요한 초안은 자동 발행되지 않습니다. 일반 인증에서는 `GET /auth/session`이 비로그인에도 익명 CSRF 토큰을 발급하며, 변경 요청은 `X-CSRF-Token`을 보냅니다.

### 환경 변수와 실행

`backend/.env.example`의 `DATABASE_URL`, `FRONTEND_ORIGINS`, `ENABLE_DEV_API`, `ENABLE_DEV_AUTH_BYPASS`, `SESSION_COOKIE_NAME`, `SESSION_EXPIRE_MINUTES`, `SESSION_COOKIE_SECURE`를 참고하세요. 비밀값은 `.env.example`에 넣지 않습니다. 루트 `compose.yaml`의 PostgreSQL과 같은 접속 정보를 사용합니다. Swagger에서 로그인 없이 테스트하려면 `backend/.env`에 두 개발용 설정을 모두 `true`로 지정하고 서버를 `localhost`에만 바인딩하세요.

```powershell
# 저장소 루트에서 docker compose up -d postgres redis를 먼저 실행
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade 150a601074af
.\.venv\Scripts\python.exe -m app.seeds.v1_local
.\.venv\Scripts\python.exe -m app.seeds.dev_user
.\.venv\Scripts\python.exe scripts/run_local_api.py
```

`v1_local` seed는 정책, 영화 Topic·Subtopic, 로컬 공개 콘텐츠를 준비합니다. `dev_user` seed는 인증 우회에 필요한 데모 사용자를 준비합니다. 두 seed 모두 마이그레이션 후에 실행합니다. `/api/v1/contents`의 범용 Mock CRUD와 `mock_contents`는 정식 경로에서 사용하지 않습니다. Agent의 Draft와 내부 AgentRun 저장·조회는 공개 Content와 구분합니다.

조회는 주로 200, 생성은 201, 삭제·로그아웃은 본문 없는 204입니다. `/api/v1` 응답은 `Cache-Control: private, no-store`를 보냅니다. 오류는 `code`와 `message`를 포함하고 입력 검증 오류의 `fields[]`에는 `field`·`code`·`message`가 붙습니다. `requestId`는 실제 값이 있을 때만 보냅니다. 대표 오류는 `MALFORMED_JSON`(400), `AUTH_REQUIRED`(401), `CSRF_INVALID`·`ONBOARDING_REQUIRED`(403), `TOPIC_NOT_FOUND`·`CONTENT_NOT_FOUND`(404), `ONBOARDING_ALREADY_COMPLETED`·`STATE_CONFLICT`(409), `UNSUPPORTED_MEDIA_TYPE`(415), `VALIDATION_ERROR`·`INVALID_CURSOR`(422)입니다.

[Swagger 테스트 가이드](../docs/SWAGGER_TEST_GUIDE.md)에서 실제 ID를 조회해 온보딩·피드·반응까지 호출하는 순서를 확인하세요. 추천 이유·노출 상태 등 미연결 기능은 후속 작업입니다. OAuth 백엔드는 공급자 응답 대체 테스트까지 구현했으며 실제 앱 등록·배포·실계정 검증은 [OAuth 안내](../docs/OAUTH_T02_1_SETUP.md)에 따라 진행합니다. Celery, Redis 조회 캐시, 관리자 전체 API 등은 후속 범위입니다.

### 온보딩 V2와 v1 호환 (2026-10-28)

신규 흐름은 `GET /api/v1/onboarding/works?topicId=...&q=...`로 작품을 1~5개 선택하고, `POST /api/v1/onboarding/preference-options`에 `{topicId,entityIds}`를 보내 활성 장르·소재 칩 전체를 받습니다. 선택 작품에 붙은 칩은 `sourceEntityIds`로 출처 작품을 알려 힌트로 씁니다. 칩은 장르와 소재를 각각 1개 이상, 종류별 7개까지 선택하고 기존 여섯 소비 유형 중 하나를 제출합니다. 두 조회는 가입 대기 세션을 사용하며 POST에는 CSRF가 필요합니다.

완료 요청의 새 답변은 `answers:{version:2,favoriteEntityIds:[...],preferenceTagIds:[...],consumptionType:"world"}`입니다. 기존 프로필·정책·Topic 입력과 세션 교체는 유지합니다. 현재 카탈로그를 재검증한 뒤 답변·선택·동의·Tap을 함께 저장합니다. `entity` 등 신규 6개 테이블과 기존 `onboarding_answers` 버전 조건의 도입 revision은 `e3a7b9c2d451`입니다. 현재 서비스 target과 유일한 head는 캐러셀 확장 `f4b8c1d9a620`에 추천 연결 표 4개를 더한 자식 `150a601074af`이며 [적용 경로](../docs/CAROUSEL_DB_API_INTEGRATION_2026-10-28.md#2-db-변경과-명시적-migration-대상)를 따릅니다. 생년월일 삭제 d2 파일은 이 후보에 포함하지 않습니다.

카탈로그는 검토된 JSON을 `python -m app.seeds.onboarding_catalog --file <JSON> --expected-database <DB명>`으로 먼저 확인합니다. 기본은 dry-run이며 `--apply`만 저장합니다. [실작품 시작 카탈로그](app/seeds/onboarding_catalog_starter.json)는 대표작 30개·장르/소재 38개이며 [출처·선정 기준](../docs/ONBOARDING_CATALOG_SOURCES_2026-10-28.md)을 함께 관리합니다. 별도의 두 가상 작품 예시는 개발용 `--demo`에 한정됩니다. [JSON·API 상세 계약](../docs/ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md)과 [Pivot 연결 검증](../docs/ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md)을 참고하세요.

아래는 기존 v1 호환 계약과 생년월일 변경의 과거 기록입니다. V2로 자동 변환하거나 기존 회원에게 재온보딩을 강제하지 않습니다.

`POST /api/v1/onboarding/complete`는 `nickname`, `accountType=user`, `consents`, `topicId`와 함께 필수 `answers: {"q1":"world","q2":"releases"}`를 받습니다. Q1은 `world`, `relationships`, `ost`, `goods`, `events`, `news` 중 하나, Q2는 `releases`, `characters`, `restock`, `events` 중 하나의 문자열입니다. 배열·누락·다른 필드는 거절하며, 기존 `subtopicIds`는 온보딩 요청에서 제거되어 보내면 422입니다.

**생년월일 결정 보류:** 통합 완료 후 결정하며 현재 로컬 구현을 최종 정책으로 해석하지 않습니다. 온보딩과 캐러셀 migration은 기존 생년월일 컬럼과 값을 보존합니다. d2 삭제 파일은 후보에서 제외했고, 배포 Compose와 로컬 OAuth helper는 현재 서비스 target `150a601074af`를 명시적으로 선택합니다. 과거 d2가 이미 적용된 DB는 해당 이력을 임의로 수정하지 않으며, 이 후보만으로의 자동 업그레이드는 별도 검토 대상입니다. 구성 파일의 변경은 실제 migration·배포 실행을 뜻하지 않습니다.

현재 로컬 코드에서는 생년월일 수집·저장과 연령 확인 절차가 제거되어 있습니다. `birthDate`는 `null`도 포함해 전송하면 422 `VALIDATION_ERROR`이며 연령 확인용 대체 필드도 받지 않습니다. `GET/PATCH /api/v1/users/me` 응답에도 `birthDate`가 없습니다. 닉네임·동의·Topic·Q1/Q2·완료 상태와 OAuth 인증은 유지합니다.

**이전 작업 기록(2026-10-27, 현재 후보에서 제외):** 당시 마이그레이션 `d2f6a9c4e731`은 `c0e5f3b8a124` 다음에 `user_account.birth_date`만 삭제했습니다. downgrade는 nullable 컬럼만 다시 만들며 삭제된 값은 복원하지 못합니다. 전용 로컬 PostgreSQL `me_nu_oauth_local`에 적용했고 생년월일을 제외한 회원·관심 분야·Q1/Q2·동의 데이터 보존과 관련 자동 테스트 149개 통과를 확인했습니다. 당시 원본 DB와 원격 테스트/Labs DB에는 적용하지 않았습니다. 후속 ERD 정합화에서는 회귀 테스트 262개가 통과했습니다. 연결 재활성화 후 읽기 전용 재확인에서 `me_nu=e7a4c2d9b812`의 `signup_channel`·`birth_date` 존재 및 `oauth_attempt`·`onboarding_answers` 부재, `me_nu_oauth_local=d2f6a9c4e731`의 반대 상태를 확인했습니다. 이는 과거 상태 기록이며 이 후보에 d2 파일이 있다는 뜻이 아닙니다.

위 생년월일 제거 작업 당시에는 프론트를 수정하지 않았습니다. 2026-10-28 후속 Pivot 연결에서는 생년월일 정책 보류에 따라 새 가입 화면에서 입력·검사·전송을 연결하지 않고 닉네임만 받습니다. 레거시 Consumer 경로는 그대로이며 현재 API에 `birthDate`를 보내면 422입니다. 최종 수집·저장·연령 확인 정책은 여전히 미정입니다.

온보딩에서는 프로필 이미지를 받지 않습니다. 요청 JSON에서 `profileImageId`를 생략해야 하며, `null`을 포함해 전송하면 추가 필드로 판단하여 422 `VALIDATION_ERROR`를 반환합니다. 이번 이미지 입력 제거는 온보딩 요청·검증에만 적용하며 DB 변경이나 추가 마이그레이션이 필요하지 않습니다. 별도 프로필 수정 API와 이미지 업로드 백로그는 이번 변경 범위에 포함하지 않습니다.

답변은 `tap`에 연결된 `onboarding_answers`에 프로필·동의·온보딩 완료 상태와 함께 저장합니다. 성공 응답에는 `answers`와 `activeTopic.subtopicIds=[]`가 포함됩니다. Q2는 정보 선호도이며 알림·마케팅 동의를 변경하지 않습니다. 기존 세부 관심사 데이터·`tap_topic` 및 별도 구독 API는 유지합니다.

추가 마이그레이션은 `c0e5f3b8a124`(이전 `b9d4e2a7f013`)입니다. 환경별 실제 적용 상태는 별도로 확인해야 합니다. 프론트는 이번 변경 범위 밖이며 호출 경로·요청/응답 타입을 새 계약에 맞추는 후속 작업이 필요합니다.

2026-10-21 이 PC의 카카오 테스트용 `me_nu_oauth_local`에는 위 마이그레이션과 로컬 테스트용 약관 4개, `anime` Topic을 준비했습니다. `scripts/run_local_oauth.py`로 실행하면 이 DB를 사용합니다. 원본 `me_nu`와 배포 DB에는 이번 변경을 적용하지 않았습니다. 수동 테스트에는 `onboarding_pending` 세션과 현재 CSRF를 사용하고, Topic ID·약관 버전은 GET 결과를 사용하세요. 이미 온보딩을 마친 계정에 답변을 임의로 채우거나 완료 상태를 초기화하지 않습니다.

### Redis 로그인 세션

루트에서 `docker compose up -d --wait redis`를 실행하고, `backend/.env`에 다음 설정을 추가한 뒤 백엔드를 재시작합니다. Redis 접속 포트를 바꿀 때는 루트 `.env`의 `REDIS_PORT`와 아래 URL을 함께 변경하세요. 로컬 Redis는 IPv4에 바인딩하므로 Windows의 localhost 주소 해석 지연을 피하도록 `127.0.0.1`을 사용합니다. 브라우저·프런트엔드 주소는 계속 localhost를 사용하세요.

```dotenv
SESSION_BACKEND=redis
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_KEY_PREFIX=me_nu
REDIS_SOCKET_TIMEOUT_SECONDS=0.5
REDIS_MAX_CONNECTIONS=20
SESSION_EXPIRE_MINUTES=30
```

- 익명 CSRF 세션과 로그인 세션을 Redis에 저장합니다. 로그인·온보딩 완료 시 이전 세션을 폐기하고 새 ID를 발급하며 로그아웃은 해당 키를 삭제합니다. HttpOnly 쿠키와 기존 CSRF·Origin 검사는 유지합니다. 실제 인증 검증 시 `ENABLE_DEV_AUTH_BYPASS=false`를 사용하세요.
- 키는 `me_nu:auth-session:v1:<세션 토큰의 SHA-256>`입니다. 값에는 사용자 ID·상태·생성/만료 시각·토큰 해시만 저장합니다. 원본 쿠키, 원본 CSRF 토큰, 비밀번호는 저장하지 않습니다. 기본 만료는 발급 시점부터 30분이며 조회해도 연장하지 않습니다.
- Redis 세션 조회마다 사용자 존재·활성 상태는 PostgreSQL에서 확인합니다. Redis 키가 없거나 만료되면 미인증, 연결/저장/폐기 실패는 `503 SESSION_STORE_UNAVAILABLE`입니다. 장애 시 PostgreSQL 세션으로 자동 전환하지 않습니다.
- `/health/redis`는 연결 성공 시 `200 {"status":"ok"}`, URL 미설정 시 `200 {"status":"disabled"}`, 장애 시 `503 REDIS_UNAVAILABLE`을 반환합니다. 세션 모드를 Redis로 지정하면서 URL을 빠뜨리면 서버 설정 검증이 실패합니다.
- Compose는 localhost에만 포트를 열고 전용 볼륨, AOF·`appendfsync always`, `noeviction`을 사용합니다. 메모리 한도에 도달하면 기존 세션을 임의 삭제하는 대신 새 저장이 실패합니다. 캐시·작업 큐와 공유하지 않는 세션 전용 구성입니다. 외부 배포 시 인증·TLS가 설정된 접속 URL과 비공개 네트워크를 사용하세요.
- DB와 Redis를 묶는 분산 트랜잭션은 없습니다. 로그인·온보딩 DB 저장이 실패하면 새 Redis 세션을 정리하고 쿠키를 발급하지 않습니다. 정리 중 Redis도 실패하면 미발급 세션은 TTL로 사라집니다. 이전 세션 폐기 이후 실패한 경우 다시 로그인해야 합니다.
- 기존 PostgreSQL 세션은 자동 이관하지 않으므로 전환 후 다시 로그인합니다. `SESSION_BACKEND=postgres`로 복귀하면 기존 DB의 미만료 세션이 다시 유효할 수 있습니다. 저장소 전환을 전체 세션 폐기 수단으로 사용하지 마세요. 세션 테이블은 호환성을 위해 유지하며 추가 DB 마이그레이션은 필요 없습니다.

연결 관리는 Redis의 [비동기 클라이언트 안내](https://redis.io/docs/latest/develop/clients/redis-py/async/)에 맞춰 앱 실행 중 연결 풀을 공유하고 종료 시 닫습니다.

실제 Redis의 공유·폐기·TTL 검증은 `backend`에서 아래 명령으로 실행합니다. 테스트마다 별도 키 접두사를 만들고 자기 키만 삭제합니다. `TEST_REDIS_URL`이 없으면 실제 서버 테스트 2개만 건너뛰고 나머지 테스트는 Redis 대역으로 실행합니다.

```powershell
$env:TEST_REDIS_URL = 'redis://127.0.0.1:6379/0'
.\.venv\Scripts\python.exe -m pytest -q
```

### 캐러셀 묶음의 서비스 적재·조회 (2026-10-28)

`content_slide`에 3~10장의 텍스트·배치와 검증된 PNG를 보관하고 `content.feed_valid_until`로 공개 만료를 관리합니다. 기존 피드·상세에 `slides[]`, 상세에 `sources[]`를 추가했으며, PNG는 세션·활성 Topic·공개 상태를 확인하는 이미지 GET으로 제공합니다. 기존 `body`·`imageUrl`·`sourceUrl`·`displayMode` 계약은 유지하고 새 캐러셀에는 `AI_GENERATED` 고지를 반환합니다. 프론트 캐러셀 연결은 이번 범위에 포함하지 않습니다.

`backend`에서 `python -m app.seeds.carousel_contents --file <bundle.json> --expected-database <DB명>`으로 먼저 검사합니다. 기본은 읽기 전용 dry-run이며 `--apply`만 저장합니다. 별도 운영자 검토 기록 `--review-record-file <review.json>`이 없는 콘텐츠는 `pending_review`로 저장하고 공개하지 않습니다. 원본 Agent의 점수·ready 상태는 승인 증거로 사용하지 않습니다. 정확한 묶음 해시에 연결된 승인 기록만 공개할 수 있으며 미검수 강제 발행 옵션은 없습니다. 입력 규격·원자성·동일 요청 충돌·출처 표시와 검증 범위는 [캐러셀 DB·API 문서](../docs/CAROUSEL_DB_API_INTEGRATION_2026-10-28.md)를 따릅니다. 에이전트 생성 DB와 코드는 변경하지 않습니다.

### 기존 커밋된 Agent 콘텐츠 일괄 적재

`backend`에서 `python -m app.seeds.agent_contents --git-ref f645df0`으로 읽기 전용 검사 후, `--apply`를 붙여 적재할 수 있습니다. 원격 커밋의 JSON을 직접 읽으며 현재 브랜치를 변경하지 않습니다. 전체 트랜잭션·중복 방지·원본 추적을 지원합니다. [적재 결과와 실행 안내](../docs/AGENT_CONTENT_IMPORT.md)를 참고하세요.

기존 분야별 임시 세부 관심사 7개는 콘텐츠 적재 후 `python -m app.seeds.agent_interests --git-ref f645df0 --apply`로 등록할 수 있습니다. 현재 Q1·Q2 온보딩의 필수 준비 과정은 아니며, 기존 작품 분류를 보존하면서 콘텐츠 연결을 추가하는 용도입니다. [임시 분류와 실행 안내](../docs/PROVISIONAL_ONBOARDING_INTERESTS.md)를 참고하세요.

콘텐츠 사진은 `backend/.env`에 `TMDB_KEY`를 넣은 뒤 `python -m app.seeds.content_images --apply`로 채웁니다. 영화·애니는 TMDB 장면 사진(없으면 포스터), 음악은 MusicBrainz 앨범 표지를 쓰며 `image_url`이 비어 있는 Agent 콘텐츠만 바꿉니다. 먼저 `--apply` 없이 실행하면 찾은 결과만 미리 볼 수 있습니다.

## 구조 원칙

- 공개 API의 공통 Base Path는 /api/v1입니다.
- API 코드는 실제 endpoint의 리소스 경로를 기준으로 분리합니다.
- 인증은 서버 Session ID와 HttpOnly Cookie를 사용합니다.
- 변경 요청에는 CSRF 토큰과 Origin 검증을 적용합니다.
- 별도의 공개 /agent API는 만들지 않고 /chat API가 내부 에이전트 로직을 호출합니다.
- 2차 개발 API는 명세에는 유지하되 실제 구현 시점에 파일과 테스트를 추가합니다.

## 폴더 구조

현재 저장소에 실제 존재하는 V1 관련 파일만 발췌했습니다. 아래 API 명세 목록은 이후 구현 목표까지 포함합니다.

~~~text
me_nu/
├─ agent/
├─ backend/
│  ├─ alembic/versions/
│  │  └─ 7c8e1a9b4d2f_add_v1_persistent_api_tables.py
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ api/
│  │  │  ├─ deps.py
│  │  │  ├─ health.py
│  │  │  ├─ dev/auth.py
│  │  │  └─ v1/
│  │  │     ├─ router.py
│  │  │     ├─ auth/router.py
│  │  │     ├─ onboarding/router.py
│  │  │     ├─ policies/router.py
│  │  │     ├─ topics/router.py
│  │  │     ├─ subtopics/router.py
│  │  │     ├─ me/topics.py
│  │  │     ├─ contents/router.py
│  │  │     └─ users/me/
│  │  │        ├─ router.py
│  │  │        └─ consents.py
│  │  ├─ core/
│  │  ├─ db/
│  │  ├─ models/
│  │  ├─ schemas/
│  │  ├─ repositories/
│  │  ├─ services/
│  │  └─ seeds/
│  │     ├─ v1_local.py
│  │     └─ dev_user.py
│  └─ tests/
└─ frontend/
   ├─ lib/api.ts
   └─ components/AuthIntegrationTest.tsx
~~~

## V1.1 목표 API 명세 목록

- 전체 108개 endpoint입니다.
- API-003은 현재 명세에서 사용하지 않는 결번입니다.
- 차수의 — 표시는 PRD에 대응하는 세부 FR이 없는 보조 endpoint입니다.

### 01. 인증·온보딩 — 5개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-001 | 소셜 로그인 시작 | POST | /api/v1/auth/login/{provider} | 1차 |
| API-002 | 소셜 콜백·세션 발급 | GET | /api/v1/auth/login/{provider}/callback | 1차 |
| API-004 | 회원·기본 프로필 생성 및 온보딩 일괄 제출 | POST | /api/v1/onboarding/complete | 1차 |
| API-005 | 현재 세션·온보딩 상태 조회 | GET | /api/v1/auth/session | 1차 |
| API-006 | 로그아웃 | POST | /api/v1/auth/logout | — |

### 02. 내 정보·동의 — 13개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-007 | 내 프로필 조회 | GET | /api/v1/users/me | 1차 |
| API-008 | 내 프로필 정정 | PATCH | /api/v1/users/me | 1차 |
| API-009 | 회원 탈퇴 | DELETE | /api/v1/users/me | 1차 |
| API-010 | 유예 중 탈퇴 복구 | POST | /api/v1/auth/withdrawal-restorations | 1차 |
| API-011 | 동의 항목·정책 조회 | GET | /api/v1/policies | 1차 |
| API-012 | 내 동의 내역 조회 | GET | /api/v1/users/me/consents | 1차 |
| API-013 | 동의 변경·선택 동의 철회 | PATCH | /api/v1/users/me/consents | 1차 |
| API-014 | 내 활동·관심 정보 열람 | GET | /api/v1/users/me/data | 1차 |
| API-015 | 개인정보 권리 요청 접수 | POST | /api/v1/users/me/data-requests | 1차 |
| API-016 | 개인정보 권리 요청 처리 결과 조회 | GET | /api/v1/users/me/data-requests | 1차 |
| API-096 | 프로필 이미지 업로드 | POST | /api/v1/profile-images | 1차 |
| API-097 | 미사용 프로필 이미지 폐기 | DELETE | /api/v1/profile-images/{imageId} | 1차 |
| API-098 | 개인정보 권리 요청 상세·진행 상태 | GET | /api/v1/users/me/data-requests/{requestId} | 1차 |

### 03. 토픽·관심사 탭 — 12개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-017 | 토픽 목록·검색 | GET | /api/v1/topics | 1차 |
| API-018 | 세부 주제 목록·검색 | GET | /api/v1/topics/{topicId}/subtopics | 1차, 2차 |
| API-019 | 세부 주제 상세 | GET | /api/v1/subtopics/{subtopicId} | 1차, 2차 |
| API-020 | 없는 세부 주제 요청 | POST | /api/v1/subtopic-requests | 2차 |
| API-021 | 내 활성 토픽 목록 | GET | /api/v1/me/topics | 1차 |
| API-022 | 내 토픽 추가·활성화 | POST | /api/v1/me/topics | 1차 |
| API-023 | 관심사 탭 삭제 | DELETE | /api/v1/me/topics/{topicId} | 1차 |
| API-024 | 관심사 탭 복구 | POST | /api/v1/me/topics/{topicId}/restorations | 1차 |
| API-025 | 구독 중인 세부 주제 목록 | GET | /api/v1/me/topics/{topicId}/subtopics | 1차, 2차 |
| API-026 | 세부 주제 구독 | PUT | /api/v1/me/topics/{topicId}/subtopics/{subtopicId} | 2차 |
| API-027 | 세부 주제 구독 해제 | DELETE | /api/v1/me/topics/{topicId}/subtopics/{subtopicId} | 2차 |
| API-106 | 내 세부 주제 요청 접수 내역 | GET | /api/v1/users/me/subtopic-requests | 2차 |

### 04. 피드·콘텐츠 — 7개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-028 | 토픽별 홈 추천 피드 | GET | /api/v1/topics/{topicId}/feed | 1차 |
| API-029 | 콘텐츠 상세 조회 | GET | /api/v1/contents/{contentId} | 1차 |
| API-030 | 같은 주제 묶음 조회 | GET | /api/v1/content-clusters/{clusterId} | 1차 |
| API-031 | 인접 주제로 더 깊게 파기 | GET | /api/v1/contents/{contentId}/exploration | 1차 |
| API-032 | 콘텐츠 이어보기 | GET | /api/v1/contents/{contentId}/related | 1차 |
| API-033 | 노출·열람 기록 수집 | POST | /api/v1/users/me/activity-events | 1차 |
| API-034 | 콘텐츠 공유 정보 조회 | GET | /api/v1/contents/{contentId}/share | 1차 |

### 05. 반응·저장·팔로우 — 15개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-035 | O/X 선호도 설정 | PUT | /api/v1/contents/{contentId}/preference | 1차 |
| API-036 | O/X 선호도 취소 | DELETE | /api/v1/contents/{contentId}/preference | 1차 |
| API-037 | 좋아요 설정 | PUT | /api/v1/contents/{contentId}/like | 1차 |
| API-038 | 좋아요 취소 | DELETE | /api/v1/contents/{contentId}/like | 1차 |
| API-039 | 북마크 저장 | PUT | /api/v1/contents/{contentId}/bookmark | 1차 |
| API-040 | 북마크 취소 | DELETE | /api/v1/contents/{contentId}/bookmark | 1차 |
| API-041 | 저장함 목록·검색 | GET | /api/v1/users/me/bookmarks | 1차, 2차 |
| API-042 | 저장 분류 정정·재노출 끄기 | PATCH | /api/v1/users/me/bookmarks/{savedItemId} | 1차 |
| API-043 | 토픽별 관심 목록 | GET | /api/v1/me/topics/{topicId}/interests | 1차 |
| API-044 | 관심 목록 항목 제거 | DELETE | /api/v1/me/topics/{topicId}/interests/{entityId} | — |
| API-045 | 채널 팔로우 | PUT | /api/v1/channels/{channelId}/follow | 1차 |
| API-046 | 채널 팔로우 취소 | DELETE | /api/v1/channels/{channelId}/follow | 1차 |
| API-047 | 내 팔로우 채널 목록 | GET | /api/v1/users/me/following-channels | 1차 |
| API-102 | 내 저장 항목 상세 | GET | /api/v1/users/me/bookmarks/{savedItemId} | 1차 |
| API-103 | 저장 항목 ID로 저장 취소 | DELETE | /api/v1/users/me/bookmarks/{savedItemId} | 1차 |

### 06. AI 대화 — 9개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-048 | AI 대화 생성 | POST | /api/v1/chat/sessions | 1차 |
| API-049 | 내 AI 대화 목록 | GET | /api/v1/chat/sessions | 1차 |
| API-050 | AI 대화 정보 조회 | GET | /api/v1/chat/sessions/{sessionId} | 1차 |
| API-051 | AI 대화 메시지 목록 | GET | /api/v1/chat/sessions/{sessionId}/messages | 1차 |
| API-052 | 질문 전송·AI 답변 생성 | POST | /api/v1/chat/sessions/{sessionId}/messages | 1차 |
| API-053 | AI 답변 생성 상태·결과 | GET | /api/v1/chat/jobs/{jobId} | 1차 |
| API-054 | AI 대화 삭제 | DELETE | /api/v1/chat/sessions/{sessionId} | 1차 |
| API-055 | 대화 보관 정책 조회 | GET | /api/v1/chat/retention-policy | 1차 |
| API-104 | AI 대화 삭제 작업 상태 | GET | /api/v1/users/me/operations/{operationId} | 1차 |

### 07. 채널·DM — 10개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-056 | 채널 기본 정보 조회 | GET | /api/v1/channels/{channelId} | 1차, 2차 |
| API-057 | 내 채널 목록 조회 | GET | /api/v1/users/me/channels | 2차 |
| API-058 | 채널 DM 수신 설정 조회 | GET | /api/v1/channels/{channelId}/dm-settings | 2차 |
| API-059 | 채널 DM 수신 설정 변경 | PATCH | /api/v1/channels/{channelId}/dm-settings | 2차 |
| API-060 | 채널에 첫 DM 보내기 | POST | /api/v1/dm/conversations | 2차 |
| API-061 | 내 DM 대화·요청함 목록 | GET | /api/v1/dm/conversations | 2차 |
| API-062 | DM 요청 수락 | PATCH | /api/v1/dm/conversations/{conversationId} | 2차 |
| API-063 | DM 메시지 목록 | GET | /api/v1/dm/conversations/{conversationId}/messages | 2차 |
| API-064 | 수락된 DM에 메시지 보내기 | POST | /api/v1/dm/conversations/{conversationId}/messages | 2차 |
| API-101 | DM 대화 상태·상대 채널 조회 | GET | /api/v1/dm/conversations/{conversationId} | 2차 |

### 08. 광고·알림 — 8개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-065 | 현재 토픽 광고 조회 | GET | /api/v1/topics/{topicId}/ads | 1차 |
| API-066 | 광고 노출 사유 조회 | GET | /api/v1/ads/{adId}/reason | 1차 |
| API-067 | 광고 그만 보기 | PUT | /api/v1/users/me/hidden-ads/{adId} | 1차 |
| API-068 | 알림 설정 조회 | GET | /api/v1/users/me/notification-settings | 1차 |
| API-069 | 알림 설정 변경 | PATCH | /api/v1/users/me/notification-settings | 1차 |
| API-107 | 내 알림 목록 조회 | GET | /api/v1/users/me/notifications | 1차 |
| API-108 | 읽지 않은 알림 개수 조회 | GET | /api/v1/users/me/notifications/unread-count | 1차 |
| API-109 | 알림 읽음 처리 | PATCH | /api/v1/users/me/notifications/{notificationId} | 1차 |

### 09. 관리자·신고 — 29개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-070 | 콘텐츠·DM 신고 접수 | POST | /api/v1/reports | 1차, 2차 |
| API-071 | 내 신고 처리 결과 | GET | /api/v1/users/me/reports | 1차, 2차 |
| API-072 | 권리침해 요청 접수 | POST | /api/v1/rights-claims | 1차 |
| API-073 | 내 권리침해 요청 조회 | GET | /api/v1/users/me/rights-claims | 1차 |
| API-074 | 운영 대시보드 | GET | /api/v1/admin/dashboard | 1차 |
| API-075 | 사용자 검색 | GET | /api/v1/admin/users | 1차 |
| API-076 | 사용자 통합 상세 | GET | /api/v1/admin/users/{userId} | 1차 |
| API-077 | 관리자 작업 기록 조회 | GET | /api/v1/admin/audit-logs | 1차 |
| API-078 | 콘텐츠 검색·자동 판정 확인 | GET | /api/v1/admin/contents | 1차 |
| API-079 | 관리자 콘텐츠 상세·삭제 영향 조회 | GET | /api/v1/admin/contents/{contentId} | 1차 |
| API-080 | 콘텐츠 분류·숨김·복구 | PATCH | /api/v1/admin/contents/{contentId} | 1차 |
| API-081 | 콘텐츠 삭제 | DELETE | /api/v1/admin/contents/{contentId} | 1차 |
| API-082 | 중복 신고 묶음 목록 | GET | /api/v1/admin/report-groups | 1차 |
| API-083 | 신고 묶음 상세 | GET | /api/v1/admin/report-groups/{groupId} | 1차 |
| API-084 | 신고 검토 결과 확정 | PATCH | /api/v1/admin/report-groups/{groupId} | 1차, 2차 |
| API-085 | 일괄 작업 미리보기 생성 | POST | /api/v1/admin/bulk-action-previews | 1차 |
| API-086 | 미리본 일괄 작업 실행 | POST | /api/v1/admin/bulk-actions | 1차, 2차 |
| API-087 | 권리침해 접수 목록 | GET | /api/v1/admin/rights-claims | 1차 |
| API-088 | 권리침해 소명 상세 | GET | /api/v1/admin/rights-claims/{claimId} | 1차 |
| API-089 | 권리침해 조치 확정 | PATCH | /api/v1/admin/rights-claims/{claimId} | 1차 |
| API-090 | 개인정보 권리 요청 목록·내용 조회 | GET | /api/v1/admin/data-requests | 1차 |
| API-091 | 개인정보 권리 요청 처리 | PATCH | /api/v1/admin/data-requests/{requestId} | 1차 |
| API-092 | 개인정보 유출 정황 등록 | POST | /api/v1/admin/privacy-incidents | 1차 |
| API-093 | 유출 통지·신고 기록 조회 | GET | /api/v1/admin/privacy-incidents/{incidentId} | 1차 |
| API-094 | 유출 대상 회원 통지 실행 | POST | /api/v1/admin/privacy-incidents/{incidentId}/notifications | 1차 |
| API-095 | 감독기관 신고 사실 기록 | POST | /api/v1/admin/privacy-incidents/{incidentId}/authority-reports | 1차 |
| API-099 | 내 신고 상세·처리 결과 | GET | /api/v1/users/me/reports/{reportId} | 1차, 2차 |
| API-100 | 내 권리침해 요청 상세 | GET | /api/v1/users/me/rights-claims/{claimId} | 1차 |
| API-105 | 개인정보 사고 통지 배치 상태 | GET | /api/v1/admin/privacy-incidents/{incidentId}/notification-batches/{notificationBatchId} | 1차 |

## 남은 V1.1 목표 범위

위 전체 목록 중 현재 구현 절에 표시되지 않은 endpoint는 후속 범위입니다. OAuth는 백엔드 모의 통합 테스트를 완료했으며, 공급자 앱 등록·실계정 인증·배포 환경 검증은 아직 남아 있습니다. 채팅·알림 등 부분 구현 영역도 위 현재 구현 표의 범위를 기준으로 확인하세요.

## API 문서

- Swagger UI: /docs
- ReDoc: /redoc
- OpenAPI JSON: /openapi.json

실제 도메인은 아직 확정하지 않았으므로 특정 운영 URL을 고정하지 않습니다.
