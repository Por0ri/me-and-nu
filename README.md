# me_nu V1 로컬 API 통합 테스트

Next.js, FastAPI, PostgreSQL, Redis 기반 로컬 V1 테스트 프로젝트입니다. 공개 API의 기본 경로는 `/api/v1`이며 응답 JSON은 camelCase를 사용합니다. 일반 인증은 서버 세션과 HttpOnly 쿠키, 변경 요청은 `X-CSRF-Token`을 사용합니다. `SESSION_BACKEND=redis`이면 세션을 Redis에 저장하며 사용자 데이터는 PostgreSQL에 유지합니다. 로컬 Swagger 테스트용 로그인 우회도 제공합니다.

2026-10-28 통합 브랜치에는 작품 1~5개 → 작품 기반 장르·소재 1개 이상 → 소비 유형 1개의 온보딩 V2 DB·API와 Pivot 프론트가 구현되어 있습니다. 서비스가 독립적으로 보관하는 캐러셀 DB·조회 API와 내부 적재 경로도 추가했습니다. [캐러셀 DB·API 계약](docs/CAROUSEL_DB_API_INTEGRATION_2026-10-28.md), [실작품 30개 카탈로그](docs/ONBOARDING_CATALOG_SOURCES_2026-10-28.md), [프론트 연결 검증](docs/ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md), [온보딩 계약과 적용 절차](docs/ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md), [PostgreSQL ERD SQL](docs/erd/current_schema.sql), [API 목록](docs/API_V1_1_LOCAL_ALIGNMENT.md)을 참고하세요. 모델은 38개 테이블이며 공개 V1 API는 36개입니다. 캐러셀 프론트 연결과 기존 테스트/Labs 서버 적용은 별도 단계입니다.

**생년월일 수집·저장·연령 확인 정책은 통합 후 결정합니다.** 현재 API의 `birthDate` 미지원 계약과 기존 DB 컬럼 상태를 유지합니다. 새 Pivot 가입 화면은 닉네임만 수집하며 생년월일을 입력·전송하지 않습니다. 새 온보딩 migration은 생년월일 삭제와 독립되어 있습니다. 레거시 Consumer 경로와 생년월일 유틸리티는 이번 연결 범위에 포함하지 않습니다.

팀 테스트 서버용 Dockerfile, 독립 Compose 설정, 환경변수 준비와 실행 순서는 [백엔드 배포 안내](deploy/README.md)를 참고하세요. 카카오·네이버 OAuth 백엔드와 프론트 없이 확인하는 절차는 [OAuth 설정 안내](docs/OAUTH_T02_1_SETUP.md)에 정리했습니다. 기본 예제의 OAuth 설정은 비활성이며, 이미 구성된 환경의 앱 등록·DB 적용·배포 상태는 각 환경 기록을 확인하세요.

## 준비

- Docker Compose, Node.js 20.9 이상, Python 및 PowerShell
- 루트 `.env`의 PostgreSQL 값과 `backend/.env`의 `DATABASE_URL`을 같은 DB로 설정
- 브라우저, 프론트엔드, 백엔드 주소에 모두 `localhost` 사용 (`127.0.0.1` 혼용 금지)

예제 환경 파일은 실제 비밀값이 아닙니다. 기존 `.env`를 덮어쓰지 말고, 처음 설치한 경우에만 복사한 뒤 비밀번호를 직접 설정하세요.

```powershell
# 저장소 루트
if (!(Test-Path .env)) { Copy-Item .env.example .env }
if (!(Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
if (!(Test-Path frontend/.env.local)) { Copy-Item frontend/.env.example frontend/.env.local }
docker compose up -d postgres redis
```

기존 환경은 `backend/.env`에 `SESSION_BACKEND=redis`, `REDIS_URL=redis://127.0.0.1:6379/0`을 추가한 뒤 백엔드를 재시작하면 전환됩니다. 기존 DB 세션은 Redis로 복사하지 않으므로 다시 로그인하세요. 세션의 기본 만료 시간은 30분이며 Redis 장애 시 인증 요청은 503을 반환합니다. 연결 상태는 `/health/redis`에서 확인합니다. 자세한 전환·복구 동작은 [Redis 세션 안내](backend/README.md#redis-로그인-세션)에 있습니다.

## 마이그레이션·seed·백엔드 실행

```powershell
# 저장소 루트에서 시작
cd backend
if (!(Test-Path .venv)) { py -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade 150a601074af
.\.venv\Scripts\python.exe -m app.seeds.v1_local
.\.venv\Scripts\python.exe -m app.seeds.dev_user
.\.venv\Scripts\python.exe scripts/run_local_api.py
```

Windows에서는 위 실행 스크립트가 PostgreSQL 비동기 연결에 맞는 이벤트 루프를 사용합니다. 자동 재시작은 하지 않으므로 코드 변경 후에는 기존 서버를 종료하고 다시 실행하세요. 이미 `http://localhost:8000/health/db`가 정상 응답하면 서버가 실행 중입니다. 같은 8000번 포트로 중복 실행하면 Windows에서 `WinError 10013` 또는 `10048`이 발생할 수 있습니다.

API의 회원 유형은 `user` 하나입니다. 이 서비스 반영 후보의 Alembic head와 명시적 migration target은 추천 연결 표 4개를 더한 `150a601074af` 하나이며 캐러셀 `f4b8c1d9a620`과 온보딩 V2 `e3a7b9c2d451`을 포함합니다. 생년월일 삭제 `d2f6a9c4e731` 파일은 제외했고, 기존 생년월일 컬럼과 값은 보존합니다. 적용 대상을 고정하기 위해 `upgrade head/heads` 대신 위 revision을 명시합니다. 과거 d2가 이미 적용된 DB는 이 후보에 해당 migration 이력이 없으므로 별도 이력 검토 없이 자동 업그레이드하지 않습니다. 환경별 실제 revision을 먼저 확인하세요. 이 저장소의 설정 변경만으로 DB 적용이나 배포가 이루어지지는 않습니다.

`app.seeds.v1_local`은 정책, 영화 Topic·Subtopic, 로컬 공개 콘텐츠를 멱등적으로 준비합니다. `app.seeds.dev_user`는 로컬 테스트용 데모 사용자를 준비합니다. 기존 영화 Agent와 저장 로직은 별개입니다. 서버 실행 중 [Swagger UI](http://localhost:8000/docs), [OpenAPI JSON](http://localhost:8000/openapi.json), [ReDoc](http://localhost:8000/redoc)을 확인할 수 있습니다.

## Swagger에서 로그인 없이 테스트

`backend/.env`에 `ENABLE_DEV_API=true`와 `ENABLE_DEV_AUTH_BYPASS=true`를 설정하고 위 마이그레이션과 두 seed를 실행한 뒤 `localhost:8000/docs`를 여세요. 루프백 요청의 보호 API는 데모 사용자로 처리되어 세션 쿠키와 CSRF 헤더 없이 실행할 수 있습니다. `ENABLE_DEV_AUTH_BYPASS=false`로 바꾸고 서버를 다시 시작하면 일반 인증이 복원됩니다. 기존 로그인 API `POST /api/v1/dev/auth/login`은 `ENABLE_DEV_API=true`일 때 Swagger의 **개발용 인증** 아래에 있습니다.

영화 Agent를 실행해 저장한 초안과 판정은 Swagger의 **개발용 Agent 결과 → `GET /api/v1/dev/agent-runs`**에서 확인합니다. 실행 명령과 상세 조회 순서는 [Swagger 테스트 가이드](docs/SWAGGER_TEST_GUIDE.md)에 있습니다. 승인되고 추가 사실 확인이 필요 없는 초안은 저장 후 공개 피드로 자동 발행됩니다.
공개 피드 테스트에서는 같은 Swagger 그룹의 `POST /api/v1/dev/agent-runs/{agentRunId}/publish`로 발행을 재시도할 수 있습니다. 탈락 초안은 로컬 테스트 모드에서만 `publish-preview`로 게시하며 검토 필요 상태로 표시됩니다.

## 프론트엔드 실행

새 PowerShell 창에서 실행합니다.

```powershell
cd frontend
npm ci
npm run dev
```

[http://localhost:3000](http://localhost:3000)의 V1 로컬 통합 테스트 화면에서는 `ENABLE_DEV_AUTH_BYPASS=false`로 설정하고 다음 순서로 확인합니다.

1. 개발용 회원가입과 로그인. 화면은 첫 변경 요청 전 익명 세션을 조회해 CSRF 토큰을 확보합니다.
2. 로그인 후 세션 조회, 정책·Topic·Subtopic 조회.
3. 기존 화면의 `answers:{q1,q2}`는 v1 호환 경로로 유지됩니다. 새 Pivot 가입 화면은 작품 검색 → 작품 기반 장르·소재 선택 → 소비 유형 1개 → `answers.version=2` 완료 요청으로 연결했습니다. 작품·태그 사전이 먼저 필요하며, 생년월일 입력·전송은 정책 결정 전까지 보류합니다.
4. 내 프로필과 Topic·Subtopic 구독 확인. 필요하면 닉네임 수정 또는 새 Topic 추가.
5. 활성 Topic의 피드와 콘텐츠 상세 조회. 실제 콘텐츠 ID를 선택합니다.
6. O/X, 좋아요, 북마크 설정·취소 후 피드와 상세에 상태가 반영되는지 확인.
7. 로그아웃 후 보호 API가 401을 반환하는지 확인.

개발용 이메일·비밀번호 API는 `/api/v1/dev/auth/*`입니다. `ENABLE_DEV_API=false`에서는 라우트와 OpenAPI에서 숨겨집니다. 정식 경로는 `/api/v1/auth/session`, `/api/v1/auth/logout`, `/api/v1/users/me` 등입니다. 세션 쿠키 이름은 기본 `menu_session`이며 JavaScript에서 읽지 않습니다. CSRF 토큰은 `GET /auth/session` 응답에서 받아 메모리에만 보관합니다. 화면의 결과 패널에는 출력하지 않습니다.

정확한 API 수동 테스트 순서는 [Swagger 테스트 가이드](docs/SWAGGER_TEST_GUIDE.md)를 참고하세요. 개발용 API와 공개 API의 엔드포인트, 확장 예정 전체 명세 목록은 [백엔드 문서](backend/README.md)에 있습니다.

## 검증

백엔드 테스트와 프런트엔드 타입 검사·빌드는 아래 명령으로 실행합니다. Swagger 수동 테스트에는 실행 중인 PostgreSQL, 마이그레이션, 두 seed가 필요합니다.

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
```

```powershell
cd frontend
npm run typecheck
npm run build
```

온보딩 migration 검증은 분리된 테스트 DB에서 명시적 revision으로 수행합니다. V2 답변이나 선택 데이터가 존재하면 해당 migration의 downgrade가 변경 전에 중단됩니다. 기존 서비스 DB에서 자동 downgrade를 실행하지 않습니다. 실제 PostgreSQL 검증은 `ONBOARDING_TEST_DATABASE_URL`로 별도 로컬 `onboarding_test` DB를 지정한 뒤 `tests/test_onboarding_v2_postgres.py`를 실행합니다.

## 현재 범위와 이후 연결 지점

현재 로컬 구현 범위는 세션·OAuth·정책·온보딩·프로필·Topic/Subtopic·구독·피드·콘텐츠 상세·O/X·좋아요·북마크·저장 목록·AI 대화·운영공지입니다. Redis는 익명·로그인 세션 저장에 적용되었으며 조회 캐시와 Celery 작업 큐는 아직 연결하지 않았습니다. 공개 콘텐츠는 DB에서 조회하고, 추천 이유와 노출 여부는 추천·이벤트 로그가 연결되기 전의 기본값입니다. AI 대화는 LLM 설정과 현재 분야의 공개 콘텐츠 근거가 필요하며 내부 AgentRun 조회와 구분합니다. 2026-10-28 통합 작업 공간에서는 실제 Kakao 로그인·V2 온보딩·피드·상세·북마크·로그아웃과 재접근 차단을 확인했습니다. Naver는 로그인 폼 도달까지만 확인했으며 자세한 결과와 후보 재검증 상태는 [서비스 흐름 검증](docs/SERVICE_FLOW_E2E_2026-10-28.md)을 따릅니다. 과거 원격 결과는 [OAuth 안내](docs/OAUTH_T02_1_SETUP.md)와 [Labs 배포 기록](docs/BACKEND_LABS_ENVIRONMENT_2026-10-27.md)에 보존합니다. 이번 후보 준비에서 원격 push·배포는 하지 않았습니다.

오류는 `{ "code": "...", "message": "..." }` 형식입니다. 주요 상태 코드는 조회 200, 생성 201, 성공한 삭제·로그아웃 204, 인증 없음 401, CSRF 또는 온보딩 필요 403, 미존재 404, 중복·상태 충돌 409, 입력·cursor 오류 422입니다.
