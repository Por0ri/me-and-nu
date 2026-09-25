# me_nu V1 로컬 API 통합 테스트

Next.js, FastAPI, PostgreSQL 기반 로컬 V1 테스트 프로젝트입니다. 공개 API의 기본 경로는 `/api/v1`이며 응답 JSON은 camelCase를 사용합니다. 일반 인증은 PostgreSQL에 저장하는 세션과 HttpOnly 쿠키, 변경 요청은 `X-CSRF-Token`을 사용합니다. 로컬 Swagger 테스트용 로그인 우회도 제공합니다.

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
docker compose up -d postgres
```

## 마이그레이션·seed·백엔드 실행

```powershell
# 저장소 루트에서 시작
cd backend
if (!(Test-Path .venv)) { py -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seeds.v1_local
.\.venv\Scripts\python.exe -m app.seeds.dev_user
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host localhost --port 8000
```

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
3. consumer는 실제 조회한 Topic과 Subtopic을 선택하고, 필수 정책에 동의한 뒤 온보딩 제출.
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

마이그레이션의 `downgrade -1` 검증은 데이터 손실 위험이 있으므로 기존 DB에서 실행하지 않습니다. 별도 테스트 DB에서 `upgrade head → downgrade -1 → upgrade head` 순서를 사용합니다.

## 현재 범위와 이후 연결 지점

이번 로컬 범위는 세션·정책·온보딩·프로필·Topic/Subtopic·구독·피드·콘텐츠 상세·O/X·좋아요·북마크입니다. 공개 콘텐츠는 DB에서 조회하고, 추천 이유와 노출 여부는 추천·이벤트 로그가 연결되기 전의 기본값입니다. 현재 포함하지 않는 OAuth 공급자 통신, Celery, Redis, 추천 알고리즘, `/chat/*`는 이후 구현 범위입니다. 내부 AgentRun은 공개 API가 아니며, 추후 `/chat/*` 서비스가 내부 Agent 실행과 저장 결과를 연결합니다.

오류는 `{ "code": "...", "message": "..." }` 형식입니다. 주요 상태 코드는 조회 200, 생성 201, 성공한 삭제·로그아웃 204, 인증 없음 401, CSRF 또는 온보딩 필요 403, 미존재 404, 중복·상태 충돌 409, 입력·cursor 오류 422입니다.
