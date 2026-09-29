# API V1.1 로컬 정합화와 화면 연결 기준

기준일: 2026-09-29. [Notion API V1.1 명세서](https://app.notion.com/p/94604c45a961835bbd1601fc3ab15ac0)의 개별 API 계약, [FE 세션·공통 계약](https://app.notion.com/p/0ce04c45a96183a884988159b10ddc81), 현재 로컬 라우터와 `frontend/lib/api.ts`를 대조했다. 이 문서는 **현재 등록된 번호 API 24개의 로컬 연결 범위**를 기록한다. V1.1 전체 구현 현황표는 아니다.

`ENABLE_DEV_API=true`에서 로컬 OpenAPI에 등록되는 HTTP 작업은 번호 API 24개, 개발용 6개, 상태 확인 2개다. 아래 공개 경로는 `/api/v1` 접두사를 한 번만 쓰며 끝에 `/`를 붙이지 않는다. `정합`은 해당 로컬 기능의 메서드·경로·주요 입출력 계약이 대응한다는 뜻이다. `부분` 항목의 빠진 기능은 표에 명시했다. 실제 네트워크·DB 실행 결과는 별도 검증 결과로 판단해야 한다.

## 현재 번호 API 대조

| 번호 | 메서드와 공개 경로 | 상태 | 로컬에서 확인할 계약과 범위 |
| --- | --- | --- | --- |
| API-004 | `POST /api/v1/onboarding` | **부분** | `201`, 동의·프로필·Topic/Subtopic 저장과 소속 검증. 로컬 개발용 계정의 온보딩 완료이며, 명세의 소셜 회원 생성 전체는 아니다. |
| API-005 | `GET /api/v1/auth/session` | **부분** | 비로그인도 `200`과 익명 세션 쿠키·`csrfToken`을 받는다. 응답 모델은 `restoration_pending`을 허용하지만 계정 복구 절차(API-010)는 아직 없다. |
| API-006 | `POST /api/v1/auth/logout` | 정합 | 현재 세션 폐기, `204` 빈 본문. 유효 세션의 변경 요청에는 CSRF가 필요하다. |
| API-007 | `GET /api/v1/users/me` | 정합 | 온보딩 완료 사용자의 프로필 조회. |
| API-008 | `PATCH /api/v1/users/me` | **부분** | 닉네임 수정은 가능하다. 프로필 이미지 업로드·연결은 API-096 미구현으로 현재 `profileImageId`를 새 값으로 설정할 수 없다. |
| API-011 | `GET /api/v1/policies` | 정합 | `consentItems[]`의 실제 `type`·`policyVersion`·`required`를 조회한다. |
| API-012 | `GET /api/v1/users/me/consents` | 정합 | 현재 동의 내역 조회. |
| API-013 | `PATCH /api/v1/users/me/consents` | 정합 | 선택 동의 갱신. 필수 항목을 거짓으로 철회하면 오류를 반환한다. |
| API-017 | `GET /api/v1/topics` | 정합 | Topic 목록과 선택 검색어 `q`. 로컬 seed가 채운 Topic만 나온다. |
| API-018 | `GET /api/v1/topics/{topicId}/subtopics` | 정합 | Topic 아래 평면 목록·검색. 선택 `q`, `cursor`, `limit`을 사용하고 `nextCursor`는 동일한 필터에서 이어 쓴다. 공개 요청에 `parentSubtopicId`가 없다. |
| API-019 | `GET /api/v1/subtopics/{subtopicId}` | 정합 | `subtopicId`·`topicId`·`name`을 반환한다. 공개 응답에 부모 필드가 없다. |
| API-021 | `GET /api/v1/me/topics` | 정합 | 내 활성 Topic 목록; 선택 `includeDeleted`로 삭제 상태를 함께 조회할 수 있다. |
| API-022 | `POST /api/v1/me/topics` | 정합 | `topicId`와 소속된 `subtopicIds`로 추가·활성화, `201`. 기존 활성 Topic은 충돌 오류를 낸다. |
| API-025 | `GET /api/v1/me/topics/{topicId}/subtopics` | 정합 | 해당 Topic의 내 Subtopic 구독 목록. |
| API-026 | `PUT /api/v1/me/topics/{topicId}/subtopics/{subtopicId}` | 정합 | 해당 Topic에 속한 Subtopic을 구독하고 `200`을 반환한다. |
| API-027 | `DELETE /api/v1/me/topics/{topicId}/subtopics/{subtopicId}` | 정합 | 구독 해제, `204` 빈 본문. |
| API-028 | `GET /api/v1/topics/{topicId}/feed` | **부분** | 공개 콘텐츠의 로컬 피드를 `200`과 `sections[]`·`nextCursor`로 조회한다. 현재 최신순이며 추천·광고·저장 재노출 공급 로직은 없다. |
| API-029 | `GET /api/v1/contents/{contentId}` | 정합 | **필수 query `topicId`**와 함께 공개 글 상세 조회. 승인되어 발행된 Agent 글도 같은 경로를 쓴다. |
| API-035 | `PUT /api/v1/contents/{contentId}/preference` | 정합 | 본문 `{ "topicId": 1, "value": "positive" }` 또는 `negative`; `200`. |
| API-036 | `DELETE /api/v1/contents/{contentId}/preference` | 정합 | **필수 query `topicId`**, `204` 빈 본문. |
| API-037 | `PUT /api/v1/contents/{contentId}/like` | 정합 | 본문 `{ "topicId": 1 }`, `200`. |
| API-038 | `DELETE /api/v1/contents/{contentId}/like` | 정합 | **필수 query `topicId`**, `204` 빈 본문. |
| API-039 | `PUT /api/v1/contents/{contentId}/bookmark` | 정합 | 본문 `{ "topicId": 1 }`, `200`. |
| API-040 | `DELETE /api/v1/contents/{contentId}/bookmark` | 정합 | **필수 query `topicId`**, `204` 빈 본문. |

### 공통 계약

- 브라우저 클라이언트는 HttpOnly 세션 쿠키를 `credentials: "include"`로 보낸다. CSRF 토큰은 메모리에만 두고 변경 요청의 `X-CSRF-Token`으로 보낸다. 로그인·온보딩처럼 세션 쿠키가 바뀌면 `getSession()`으로 새 토큰을 받는다. `401` 또는 `CSRF_INVALID` 뒤에는 메모리 토큰을 폐기하므로 세션을 다시 조회한 뒤 사용자의 작업을 재시도한다.
- 첫 익명 세션 발급 시 허용된 출처를 확인한다. 헤더가 없는 로컬 Swagger·CLI GET은 허용한다. 오류 응답은 `code`와 `message`, 입력 검증 오류는 `fields[]`의 `field`·`code`·`message`를 사용한다. 깨진 JSON은 `400 MALFORMED_JSON`이다.
- 공개 `/api/v1` 응답에는 `Cache-Control: private, no-store`가 붙는다. 본문이 있는 변경 요청은 JSON `Content-Type`을 사용한다. 다른 본문 형식은 `415 UNSUPPORTED_MEDIA_TYPE`이다.
- `204` 성공 응답에는 본문이 없다. 클라이언트는 JSON 파싱을 시도하지 않는다. 보호된 API는 로그인·온보딩 상태에 따라 `401 AUTH_REQUIRED` 또는 `403 ONBOARDING_REQUIRED`가 될 수 있다.
- `ENABLE_DEV_API=true`의 개발용 가입·로그인·Agent 결과 API와 `ENABLE_DEV_AUTH_BYPASS=true`의 루프백 우회는 **로컬 테스트 수단**이다. 카카오·네이버 OAuth나 정식 운영 인증을 뜻하지 않는다.

## 새 화면에 연결할 때의 호출 순서

기존 FE 함수는 [frontend/lib/api.ts](../frontend/lib/api.ts)에 있다. 화면 컴포넌트가 완성되면 함수 이름을 그대로 연결하고, 서버가 내려준 ID와 정책 버전을 사용한다. 현 로컬 구성은 `localhost:3000`의 FE에서 `localhost:8000`의 API를 호출한다.

| 화면 단계 | 현재 쓸 수 있는 FE 함수 | 입력·상태 갱신 |
| --- | --- | --- |
| 시작·인증 | `getSession`, 개발용 `register`, `login`, `logout` | 세션을 조회해 쿠키·CSRF를 받는다. 개발용 로그인 후 세션을 재조회한다. 로그아웃 성공은 빈 `204`; 이어서 세션·보호 화면을 갱신한다. |
| 정책·온보딩 선택 | `getPolicies`, `getTopics`, `getSubtopics`, `getSubtopic` | 실제 `policyVersion`, `topicId`, `subtopicId`를 확보한다. Topic 검색은 `getTopics({q})`, Subtopic 검색·더보기는 `getSubtopics(topicId, {q, cursor})`로 이어 간다. |
| 온보딩 제출 | `onboard` | 실제 정책 항목과 선택 ID를 제출한다. 세션 회전 후 `getSession`으로 `active`·새 CSRF를 확인하고 홈 데이터 조회를 시작한다. |
| 마이페이지·동의 | `getProfile`, `updateProfile`, `getConsents`, `updateConsents` | 닉네임·선택 동의 변경 후 관련 조회를 다시 실행한다. `updateProfile({profileImageId:null})`로 기존 이미지 연결 해제만 가능하며 업로드·새 이미지 연결은 아직 지원하지 않는다. |
| 관심사·구독 | `getMyTopics`, `addMyTopic`, `getMySubtopics`, `subscribeSubtopic`, `unsubscribeSubtopic` | `getMyTopics({includeDeleted})` 조회가 가능하다. 추가에는 실제 Topic에 속한 Subtopic ID를 제출한다. 구독 해제의 `204`를 빈 응답으로 처리한다. |
| 홈·글 상세 | `getFeed`, `getContent` | 활성 `topicId`로 피드를 조회하고 카드 `id`를 `contentId`로 전달한다. 상세에는 같은 `topicId`를 필수로 넘긴다. `nextCursor`가 있으면 이어서 가져온다. |
| 글 반응 | `setPreference`, `clearPreference`, `likeContent`, `unlikeContent`, `bookmarkContent`, `unbookmarkContent` | 모든 요청에 글이 속한 활성 `topicId`가 필요하다. 변경 후 피드 카드와 상세를 다시 조회하거나 반환값으로 동기화한다. |

클라이언트의 `ApiError.code`로 `AUTH_REQUIRED`, `ONBOARDING_REQUIRED`, `CSRF_INVALID`, `INVALID_CURSOR` 같은 상태를 분기한다. 만료된 CSRF가 확인되면 `getSession()`으로 현재 토큰을 다시 받은 뒤 사용자의 작업을 재시도한다. 화면에 없는 미구현 동작의 FE 호출 함수는 준비되어 있지 않다.

정책 `type`은 네 가지 고정값으로 가정하지 않고 `getPolicies()`가 반환한 항목을 그대로 동의 입력에 사용한다. `OnboardingInput`은 소비자에게 `topicId`와 `subtopicIds`를 요구하고 크리에이터 입력에서는 두 필드를 제외한다. 로컬 테스트 화면은 Topic 또는 피드 Subtopic 필터가 바뀌면 이전 피드와 cursor를 지운다.

## Swagger UI 로컬 확인 순서

서버·PostgreSQL·seed 준비와 모든 작업의 Try it out 입력값은 [Swagger UI 조작법](SWAGGER_UI_OPERATION_GUIDE.md)과 [서버 준비 가이드](SWAGGER_TEST_GUIDE.md)를 따른다. `/docs`와 `/openapi.json`은 같은 `localhost:8000`으로 연다. 예시의 숫자 ID를 그대로 쓰지 않고 앞선 GET의 실제 값을 사용한다.

1. **빠른 공개 글 확인:** `ENABLE_DEV_API=true`, `ENABLE_DEV_AUTH_BYPASS=true`로 로컬 데모 사용자를 준비한다. `GET /api/v1/me/topics`의 활성 `topicId` → `GET /api/v1/topics/{topicId}/subtopics`의 `subtopicId` → `GET /api/v1/topics/{topicId}/feed`의 카드 `id` → `GET /api/v1/contents/{contentId}?topicId=...` 순서로 실행한다. 공개된 Agent 글도 같은 피드·상세에서 확인한다. 내부 초안은 개발용 Agent 실행 조회 경로를 사용한다.
2. **실제 세션 흐름:** `ENABLE_DEV_AUTH_BYPASS=false`로 재시작한다. `GET /api/v1/auth/session`에서 `anonymous`와 `csrfToken`을 받는다. Swagger **Authorize → CsrfToken**에 넣고 개발용 가입·로그인을 실행한다. 세션을 다시 조회해 `onboarding_pending`과 새 토큰을 받는다.
3. **온보딩:** 정책·Topic·Subtopic을 GET으로 읽고 `POST /api/v1/onboarding`을 실제 값으로 채워 실행한다. `201` 뒤 세션을 다시 조회해 `active`와 새 토큰을 확인한다. 같은 Topic 밖의 Subtopic은 제출할 수 없다.
4. **화면 동작:** 프로필·동의·관심사 조회와 갱신 → 피드·상세 → O/X·좋아요·북마크 PUT 및 DELETE 순서로 실행한다. DELETE와 로그아웃의 `204`에는 본문이 없고, 변경 뒤 GET을 다시 호출해 결과를 본다.
5. **계약 검사:** API-018의 선택 query는 `q`, `cursor`, `limit`이며 부모 필드가 없어야 한다. API-019 응답에도 부모 필드가 없어야 한다. 피드 cursor는 같은 필터에 사용하고, 상세와 반응 취소에는 `topicId`를 입력한다.

코드 회귀 확인 명령은 저장소 루트에서 다음과 같다. 실행 여부와 통과 결과는 작업 결과에 별도로 기록한다.

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
cd ..\frontend
npm run typecheck
npm run build
```

## 이번 범위 밖과 결정이 필요한 항목

- **새 공개 API:** API-023 Topic 삭제, API-034 공유 정보, API-041 저장함 목록은 현재 라우트가 없다. OAuth API-001/002, 계정 복구 API-010, 저장함 수정·상세·삭제, 채널 팔로우·DM·AI 대화, 설정·알림·신고·탈퇴도 별도 작업이다. 이번 로컬 정합화에서 새 공개 라우트나 빈 FE 스텁을 추가하지 않는다.
- **온보딩 선택 수:** Figma는 세부 토픽 **5개 이상**, API-004는 소비자 **1개 이상**이다. 현재 영화 seed는 선택 가능한 Subtopic 두 개다. 화면 문구와 데이터 범위를 결정해야 하며, 서버 최소값을 임의로 올리지 않았다.
- **홈 공급:** API-028은 현재 공개 글 최신순 조회다. 추천·광고·저장 재노출의 공급 로직, Home_AI의 “원하는 글 요청” 계약은 아직 없다. `Content_AI`의 글 질문·답변 계약과 동일하다고 가정하지 않는다.
- **화면과 목록 계약:** Figma의 사람 구독 UI와 V1.1의 채널 팔로우, 번호 페이지 UI와 cursor 목록, 저장함 카드의 썸네일·작성자·시간, “21시 이후 알림허용”과 일반 문의의 필드는 별도 결정이 필요하다. 세부 화면 매핑은 [Figma API 백로그](FIGMA_API_V1_1_IMPLEMENTATION_BACKLOG.md)에 있다.
- **자동 Agent 공급:** 이미 발행된 글은 API-028·029로 조회한다. 정기 실행을 위한 Celery·Redis와 실제 모델·외부 공급자 연동은 이번 API 계약 정리와 별도의 작업이다.

## 2026-09-29 로컬 검증 기록

- `backend`에서 `python -m pytest -q`: **98 passed**. Starlette TestClient의 사용 중단 예정 경고 1개가 남았다.
- `frontend`에서 `npm run typecheck`, `npm run build`: 모두 통과. Next.js는 저장소 밖의 상위 `package-lock.json`을 무시한다는 경고를 출력했다.
- FastAPI TestClient의 `GET /docs`와 `GET /openapi.json`: 둘 다 `200`. 개발 API를 켠 OpenAPI는 전체 HTTP 작업 32개, 번호가 있는 공개 API 24개다. API-018 query는 `topicId`, `q`, `cursor`, `limit`이며 API-019의 `SubtopicItem`에는 `subtopicId`, `topicId`, `name`만 있다. 공개 경로 끝에 `/`가 없다.
- 기존 PostgreSQL을 **읽기 전용**으로 확인했다. Topic 1개, Subtopic 2개, Content 2개이며 부모가 지정된 Subtopic은 0개다. 별도 SQLite 회귀 테스트에서 자식 Subtopic도 평면 목록·검색·커서에 포함됨을 확인했다. 로컬 ASGI 요청에서는 세션, Topic, Subtopic, 피드, 글 상세가 모두 `200`이었다. 이 요청은 실제 TCP 서버를 띄운 브라우저 조작은 아니다.
- DB의 Alembic 현재 버전은 `7c8e1a9b4d2f`다. 저장소 head `b5d2c8f1a7e3`는 이번 공개 API 정합화와 무관한 채팅 테이블 추가 리비전이므로 이 작업에서 적용하지 않았다. 기존 DB 초기화·seed 재실행·데이터 삭제는 하지 않았다.
