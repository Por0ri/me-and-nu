# me_nu

AI 에이전트와 추천 알고리즘을 활용하는 초개인화 큐레이팅 플랫폼입니다.

이 문서의 아래 전체 API 표는 V1.0 목표 목록입니다. 현재 구현된 로컬 범위는 다음 절에 따로 표시했습니다. URL 표의 과거 말미 `/` 표기는 원 명세 목록을 보존한 것이며, 현재 구현 경로에는 끝의 `/`를 붙이지 않습니다.

## 현재 구현된 V1 로컬 범위

| 영역 | Endpoint |
| --- | --- |
| 개발용 인증 | `POST /api/v1/dev/auth/register`, `POST /api/v1/dev/auth/login` |
| 개발용 Agent 결과 | `GET /api/v1/dev/agent-runs`, `GET /api/v1/dev/agent-runs/{agentRunId}` |
| 개발용 Agent 발행 | `POST /api/v1/dev/agent-runs/{agentRunId}/publish`, `POST /api/v1/dev/agent-runs/{agentRunId}/publish-preview` |
| 세션 | `GET /api/v1/auth/session`, `POST /api/v1/auth/logout` |
| 정책·온보딩 | `GET /api/v1/policies`, `POST /api/v1/onboarding` |
| 내 정보 | `GET/PATCH /api/v1/users/me`, `GET/PATCH /api/v1/users/me/consents` |
| Topic·Subtopic | `GET /api/v1/topics`, `GET /api/v1/topics/{topicId}/subtopics`, `GET /api/v1/subtopics/{subtopicId}` |
| 내 관심사 | `GET/POST /api/v1/me/topics`, `GET /api/v1/me/topics/{topicId}/subtopics`, `PUT/DELETE /api/v1/me/topics/{topicId}/subtopics/{subtopicId}` |
| 피드·콘텐츠 | `GET /api/v1/topics/{topicId}/feed`, `GET /api/v1/contents/{contentId}?topicId=...` |
| 반응·저장 | `PUT/DELETE /api/v1/contents/{contentId}/preference`, `/like`, `/bookmark` |

인증과 데이터는 PostgreSQL을 사용합니다. 개발용 가입·로그인과 Agent 결과 조회는 `ENABLE_DEV_API=true`에서만 등록됩니다. Agent 결과 조회는 로컬 요청과 인증된 개발 사용자로 제한되며 Swagger의 **개발용 Agent 결과**에 표시됩니다. `ENABLE_DEV_API=true`와 `ENABLE_DEV_AUTH_BYPASS=true`를 함께 설정하면 로컬 루프백 요청을 준비된 데모 사용자로 처리합니다. 이때 보호 API 테스트에 로그인·세션 쿠키·CSRF 헤더가 필요하지 않습니다. 우회를 `false`로 되돌리면 일반 세션 인증이 적용됩니다. 영화 Agent 실행 결과가 승인되고 사실 확인 항목이 없으면 저장 직후 공개 Content 피드에 자동 발행됩니다. 탈락하거나 사실 확인이 필요한 초안은 자동 발행되지 않습니다. 일반 인증에서는 `GET /auth/session`이 비로그인에도 익명 CSRF 토큰을 발급하며, 변경 요청은 `X-CSRF-Token`을 보냅니다.

### 환경 변수와 실행

`backend/.env.example`의 `DATABASE_URL`, `FRONTEND_ORIGINS`, `ENABLE_DEV_API`, `ENABLE_DEV_AUTH_BYPASS`, `SESSION_COOKIE_NAME`, `SESSION_EXPIRE_MINUTES`, `SESSION_COOKIE_SECURE`를 참고하세요. 비밀값은 `.env.example`에 넣지 않습니다. 루트 `compose.yaml`의 PostgreSQL과 같은 접속 정보를 사용합니다. Swagger에서 로그인 없이 테스트하려면 `backend/.env`에 두 개발용 설정을 모두 `true`로 지정하고 서버를 `localhost`에만 바인딩하세요.

```powershell
# 저장소 루트에서 docker compose up -d postgres를 먼저 실행
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seeds.v1_local
.\.venv\Scripts\python.exe -m app.seeds.dev_user
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host localhost --port 8000
```

`v1_local` seed는 정책, 영화 Topic·Subtopic, 로컬 공개 콘텐츠를 준비합니다. `dev_user` seed는 인증 우회에 필요한 데모 사용자를 준비합니다. 두 seed 모두 마이그레이션 후에 실행합니다. `/api/v1/contents`의 범용 Mock CRUD와 `mock_contents`는 정식 경로에서 사용하지 않습니다. Agent의 Draft와 내부 AgentRun 저장·조회는 공개 Content와 구분합니다.

조회는 주로 200, 생성은 201, 삭제·로그아웃은 본문 없는 204입니다. 오류는 `code`와 `message`를 포함하고 필요한 경우 `fields`, `requestId`를 포함합니다. 대표 오류는 `AUTH_REQUIRED`(401), `CSRF_INVALID`·`ONBOARDING_REQUIRED`(403), `TOPIC_NOT_FOUND`·`CONTENT_NOT_FOUND`(404), `ONBOARDING_ALREADY_COMPLETED`·`STATE_CONFLICT`(409), `VALIDATION_ERROR`·`INVALID_CURSOR`(422)입니다.

[Swagger 테스트 가이드](../docs/SWAGGER_TEST_GUIDE.md)에서 실제 ID를 조회해 온보딩·피드·반응까지 호출하는 순서를 확인하세요. 추천 알고리즘과 이벤트 로그가 아직 없으므로 추천 이유·노출 상태는 기본값이며, `/chat/*`와 내부 AgentRun 연결은 추후 서비스 계층에서 진행합니다. OAuth 공급자 통신, Celery, Redis, 관리자 전체 API 등은 이번 구현에 포함되지 않습니다. 아래 목표 API 표에서 위 구현 범위를 제외한 항목은 후속 작업 대상입니다.

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

## API 명세 목록

- 전체 105개 endpoint입니다.
- API-003은 현재 명세에서 사용하지 않는 결번입니다.
- 차수의 — 표시는 PRD에 대응하는 세부 FR이 없는 보조 endpoint입니다.

### 01. 인증·온보딩 — 5개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-001 | 소셜 로그인 시작 | POST | /api/v1/auth/login/{provider}/ | 1차 |
| API-002 | 소셜 콜백·세션 발급 | GET | /api/v1/auth/login/{provider}/callback/ | 1차 |
| API-004 | 회원·기본 프로필 생성 및 온보딩 일괄 제출 | POST | /api/v1/onboarding/ | 1차 |
| API-005 | 현재 세션·온보딩 상태 조회 | GET | /api/v1/auth/session/ | 1차 |
| API-006 | 로그아웃 | POST | /api/v1/auth/logout/ | — |

### 02. 내 정보·동의 — 13개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-007 | 내 프로필 조회 | GET | /api/v1/users/me/ | 1차 |
| API-008 | 내 프로필 정정 | PATCH | /api/v1/users/me/ | 1차 |
| API-009 | 회원 탈퇴 | DELETE | /api/v1/users/me/ | 1차 |
| API-010 | 유예 중 탈퇴 복구 | POST | /api/v1/auth/withdrawal-restorations/ | 1차 |
| API-011 | 동의 항목·정책 조회 | GET | /api/v1/policies/ | 1차 |
| API-012 | 내 동의 내역 조회 | GET | /api/v1/users/me/consents/ | 1차 |
| API-013 | 동의 변경·선택 동의 철회 | PATCH | /api/v1/users/me/consents/ | 1차 |
| API-014 | 내 활동·관심 정보 열람 | GET | /api/v1/users/me/data/ | 1차 |
| API-015 | 개인정보 권리 요청 접수 | POST | /api/v1/users/me/data-requests/ | 1차 |
| API-016 | 개인정보 권리 요청 처리 결과 조회 | GET | /api/v1/users/me/data-requests/ | 1차 |
| API-096 | 프로필 이미지 업로드 | POST | /api/v1/profile-images/ | 1차 |
| API-097 | 미사용 프로필 이미지 폐기 | DELETE | /api/v1/profile-images/{imageId}/ | 1차 |
| API-098 | 개인정보 권리 요청 상세·진행 상태 | GET | /api/v1/users/me/data-requests/{requestId}/ | 1차 |

### 03. 토픽·관심사 탭 — 12개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-017 | 토픽 목록·검색 | GET | /api/v1/topics/ | 1차 |
| API-018 | 세부 주제 목록·검색 | GET | /api/v1/topics/{topicId}/subtopics/ | 1차, 2차 |
| API-019 | 세부 주제 상세 | GET | /api/v1/subtopics/{subtopicId}/ | 1차, 2차 |
| API-020 | 없는 세부 주제 요청 | POST | /api/v1/subtopic-requests/ | 2차 |
| API-021 | 내 활성 토픽 목록 | GET | /api/v1/me/topics/ | 1차 |
| API-022 | 내 토픽 추가·활성화 | POST | /api/v1/me/topics/ | 1차 |
| API-023 | 관심사 탭 삭제 | DELETE | /api/v1/me/topics/{topicId}/ | 1차 |
| API-024 | 관심사 탭 복구 | POST | /api/v1/me/topics/{topicId}/restorations/ | 1차 |
| API-025 | 구독 중인 세부 주제 목록 | GET | /api/v1/me/topics/{topicId}/subtopics/ | 1차, 2차 |
| API-026 | 세부 주제 구독 | PUT | /api/v1/me/topics/{topicId}/subtopics/{subtopicId}/ | 2차 |
| API-027 | 세부 주제 구독 해제 | DELETE | /api/v1/me/topics/{topicId}/subtopics/{subtopicId}/ | 2차 |
| API-106 | 내 세부 주제 요청 접수 내역 | GET | /api/v1/users/me/subtopic-requests/ | 2차 |

### 04. 피드·콘텐츠 — 7개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-028 | 토픽별 홈 추천 피드 | GET | /api/v1/topics/{topicId}/feed/ | 1차 |
| API-029 | 콘텐츠 상세 조회 | GET | /api/v1/contents/{contentId}/ | 1차 |
| API-030 | 같은 주제 묶음 조회 | GET | /api/v1/content-clusters/{clusterId}/ | 1차 |
| API-031 | 인접 주제로 더 깊게 파기 | GET | /api/v1/contents/{contentId}/exploration/ | 1차 |
| API-032 | 콘텐츠 이어보기 | GET | /api/v1/contents/{contentId}/related/ | 1차 |
| API-033 | 노출·열람 기록 수집 | POST | /api/v1/users/me/activity-events/ | 1차 |
| API-034 | 콘텐츠 공유 정보 조회 | GET | /api/v1/contents/{contentId}/share/ | 1차 |

### 05. 반응·저장·팔로우 — 15개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-035 | O/X 선호도 설정 | PUT | /api/v1/contents/{contentId}/preference/ | 1차 |
| API-036 | O/X 선호도 취소 | DELETE | /api/v1/contents/{contentId}/preference/ | 1차 |
| API-037 | 좋아요 설정 | PUT | /api/v1/contents/{contentId}/like/ | 1차 |
| API-038 | 좋아요 취소 | DELETE | /api/v1/contents/{contentId}/like/ | 1차 |
| API-039 | 북마크 저장 | PUT | /api/v1/contents/{contentId}/bookmark/ | 1차 |
| API-040 | 북마크 취소 | DELETE | /api/v1/contents/{contentId}/bookmark/ | 1차 |
| API-041 | 저장함 목록·검색 | GET | /api/v1/users/me/bookmarks/ | 1차, 2차 |
| API-042 | 저장 분류 정정·재노출 끄기 | PATCH | /api/v1/users/me/bookmarks/{savedItemId}/ | 1차 |
| API-043 | 토픽별 관심 목록 | GET | /api/v1/me/topics/{topicId}/interests/ | 1차 |
| API-044 | 관심 목록 항목 제거 | DELETE | /api/v1/me/topics/{topicId}/interests/{entityId}/ | — |
| API-045 | 채널 팔로우 | PUT | /api/v1/channels/{channelId}/follow/ | 1차 |
| API-046 | 채널 팔로우 취소 | DELETE | /api/v1/channels/{channelId}/follow/ | 1차 |
| API-047 | 내 팔로우 채널 목록 | GET | /api/v1/users/me/following-channels/ | 1차 |
| API-102 | 내 저장 항목 상세 | GET | /api/v1/users/me/bookmarks/{savedItemId}/ | 1차 |
| API-103 | 저장 항목 ID로 저장 취소 | DELETE | /api/v1/users/me/bookmarks/{savedItemId}/ | 1차 |

### 06. AI 대화 — 9개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-048 | AI 대화 생성 | POST | /api/v1/chat/sessions/ | 1차 |
| API-049 | 내 AI 대화 목록 | GET | /api/v1/chat/sessions/ | 1차 |
| API-050 | AI 대화 정보 조회 | GET | /api/v1/chat/sessions/{sessionId}/ | 1차 |
| API-051 | AI 대화 메시지 목록 | GET | /api/v1/chat/sessions/{sessionId}/messages/ | 1차 |
| API-052 | 질문 전송·AI 답변 생성 | POST | /api/v1/chat/sessions/{sessionId}/messages/ | 1차 |
| API-053 | AI 답변 생성 상태·결과 | GET | /api/v1/chat/jobs/{jobId}/ | 1차 |
| API-054 | AI 대화 삭제 | DELETE | /api/v1/chat/sessions/{sessionId}/ | 1차 |
| API-055 | 대화 보관 정책 조회 | GET | /api/v1/chat/retention-policy/ | 1차 |
| API-104 | AI 대화 삭제 작업 상태 | GET | /api/v1/users/me/operations/{operationId}/ | 1차 |

### 07. 채널·DM — 10개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-056 | 채널 기본 정보 조회 | GET | /api/v1/channels/{channelId}/ | 1차, 2차 |
| API-057 | 내 채널 목록 조회 | GET | /api/v1/users/me/channels/ | 2차 |
| API-058 | 채널 DM 수신 설정 조회 | GET | /api/v1/channels/{channelId}/dm-settings/ | 2차 |
| API-059 | 채널 DM 수신 설정 변경 | PATCH | /api/v1/channels/{channelId}/dm-settings/ | 2차 |
| API-060 | 채널에 첫 DM 보내기 | POST | /api/v1/dm/conversations/ | 2차 |
| API-061 | 내 DM 대화·요청함 목록 | GET | /api/v1/dm/conversations/ | 2차 |
| API-062 | DM 요청 수락 | PATCH | /api/v1/dm/conversations/{conversationId}/ | 2차 |
| API-063 | DM 메시지 목록 | GET | /api/v1/dm/conversations/{conversationId}/messages/ | 2차 |
| API-064 | 수락된 DM에 메시지 보내기 | POST | /api/v1/dm/conversations/{conversationId}/messages/ | 2차 |
| API-101 | DM 대화 상태·상대 채널 조회 | GET | /api/v1/dm/conversations/{conversationId}/ | 2차 |

### 08. 광고·알림 — 5개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-065 | 현재 토픽 광고 조회 | GET | /api/v1/topics/{topicId}/ads/ | 1차 |
| API-066 | 광고 노출 사유 조회 | GET | /api/v1/ads/{adId}/reason/ | 1차 |
| API-067 | 광고 그만 보기 | PUT | /api/v1/users/me/hidden-ads/{adId}/ | 1차 |
| API-068 | 알림 설정 조회 | GET | /api/v1/users/me/notification-settings/ | 1차 |
| API-069 | 알림 설정 변경 | PATCH | /api/v1/users/me/notification-settings/ | 1차 |

### 09. 관리자·신고 — 29개

| API | 기능 | Method | Endpoint | 차수 |
|---|---|---|---|---|
| API-070 | 콘텐츠·DM 신고 접수 | POST | /api/v1/reports/ | 1차, 2차 |
| API-071 | 내 신고 처리 결과 | GET | /api/v1/users/me/reports/ | 1차, 2차 |
| API-072 | 권리침해 요청 접수 | POST | /api/v1/rights-claims/ | 1차 |
| API-073 | 내 권리침해 요청 조회 | GET | /api/v1/users/me/rights-claims/ | 1차 |
| API-074 | 운영 대시보드 | GET | /api/v1/admin/dashboard/ | 1차 |
| API-075 | 사용자 검색 | GET | /api/v1/admin/users/ | 1차 |
| API-076 | 사용자 통합 상세 | GET | /api/v1/admin/users/{userId}/ | 1차 |
| API-077 | 관리자 작업 기록 조회 | GET | /api/v1/admin/audit-logs/ | 1차 |
| API-078 | 콘텐츠 검색·자동 판정 확인 | GET | /api/v1/admin/contents/ | 1차 |
| API-079 | 관리자 콘텐츠 상세·삭제 영향 조회 | GET | /api/v1/admin/contents/{contentId}/ | 1차 |
| API-080 | 콘텐츠 분류·숨김·복구 | PATCH | /api/v1/admin/contents/{contentId}/ | 1차 |
| API-081 | 콘텐츠 삭제 | DELETE | /api/v1/admin/contents/{contentId}/ | 1차 |
| API-082 | 중복 신고 묶음 목록 | GET | /api/v1/admin/report-groups/ | 1차 |
| API-083 | 신고 묶음 상세 | GET | /api/v1/admin/report-groups/{groupId}/ | 1차 |
| API-084 | 신고 검토 결과 확정 | PATCH | /api/v1/admin/report-groups/{groupId}/ | 1차, 2차 |
| API-085 | 일괄 작업 미리보기 생성 | POST | /api/v1/admin/bulk-action-previews/ | 1차 |
| API-086 | 미리본 일괄 작업 실행 | POST | /api/v1/admin/bulk-actions/ | 1차, 2차 |
| API-087 | 권리침해 접수 목록 | GET | /api/v1/admin/rights-claims/ | 1차 |
| API-088 | 권리침해 소명 상세 | GET | /api/v1/admin/rights-claims/{claimId}/ | 1차 |
| API-089 | 권리침해 조치 확정 | PATCH | /api/v1/admin/rights-claims/{claimId}/ | 1차 |
| API-090 | 개인정보 권리 요청 목록·내용 조회 | GET | /api/v1/admin/data-requests/ | 1차 |
| API-091 | 개인정보 권리 요청 처리 | PATCH | /api/v1/admin/data-requests/{requestId}/ | 1차 |
| API-092 | 개인정보 유출 정황 등록 | POST | /api/v1/admin/privacy-incidents/ | 1차 |
| API-093 | 유출 통지·신고 기록 조회 | GET | /api/v1/admin/privacy-incidents/{incidentId}/ | 1차 |
| API-094 | 유출 대상 회원 통지 실행 | POST | /api/v1/admin/privacy-incidents/{incidentId}/notifications/ | 1차 |
| API-095 | 감독기관 신고 사실 기록 | POST | /api/v1/admin/privacy-incidents/{incidentId}/authority-reports/ | 1차 |
| API-099 | 내 신고 상세·처리 결과 | GET | /api/v1/users/me/reports/{reportId}/ | 1차, 2차 |
| API-100 | 내 권리침해 요청 상세 | GET | /api/v1/users/me/rights-claims/{claimId}/ | 1차 |
| API-105 | 개인정보 사고 통지 배치 상태 | GET | /api/v1/admin/privacy-incidents/{incidentId}/notification-batches/{notificationBatchId}/ | 1차 |

## 남은 V1.0 범위

위 전체 목록 중 현재 구현 절에 표시되지 않은 endpoint는 후속 범위입니다. 특히 OAuth, 채팅, 추천·검색, 관리자·신고, 개인정보 권리 요청, 광고·알림·DM은 아직 로컬 통합 테스트 대상이 아닙니다. 기존 Agent Adapter와 실행·저장 로직은 유지하며, `/chat/*` 구현 시 내부 서비스에서 연결합니다.

## API 문서

- Swagger UI: /docs
- ReDoc: /redoc
- OpenAPI JSON: /openapi.json

실제 도메인은 아직 확정하지 않았으므로 특정 운영 URL을 고정하지 않습니다.
