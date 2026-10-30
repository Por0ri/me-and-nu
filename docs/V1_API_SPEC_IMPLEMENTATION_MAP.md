# V1.0 명세 번호 ↔ 현재 구현 API

**현재 목록 안내 (2026-10-28):** 이 파일은 2026-09-26 최초 대조와 이후 온보딩 메모를 보존한 과거 기록이다. 현재 통합 브랜치 구현은 공개 V1 37개·개발용 6개·상태 확인 3개이며 [API V1.1 로컬 정합화](API_V1_1_LOCAL_ALIGNMENT.md)의 최신 표를 따른다. 작품 검색·장르/소재 후보 조회와 버전별 완료 계약은 [온보딩 V2 구현 명세](ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md)에 정리했다. 후속 [캐러셀 DB·API 확장](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md)은 피드·상세의 `slides[]`, 상세의 `sources[]`와 보호된 PNG 이미지 GET 하나를 추가한다. 공개 import·승인 API는 추가하지 않았다. 서비스 target은 온보딩 e3·캐러셀 f4를 포함한 `150a601074af`이며 생년월일 삭제 d2는 별도로 보류한다. 로컬 코드에서 생년월일은 온보딩·프로필 계약에서 제거되어 있으나, **최신 사용자 지시에 따라 수집·저장·연령 확인 방식은 통합 완료 후 결정**한다. 현재 코드가 최종 정책을 뜻하지 않으며 생년월일 관련 프론트 변경과 DB 마이그레이션 추가 적용은 보류한다. 아래의 24개·32개 수치와 미구현 설명을 현재 상태로 사용하지 않는다.

검증 기준: 2026-09-26, `main`의 `ab9235e`. [Notion V1.0 API 명세서](https://app.notion.com/p/6cf04c45a96182f4a1d781fe8f2d5af6)와 그 안의 [PRD v0.6 FR 매칭 목록](https://app.notion.com/p/b9504c45a96182a7ad4981e43cd406fe), 현재 FastAPI `/openapi.json`의 **HTTP 메서드와 경로**를 대조했다. 표의 `대응`은 라우트가 존재한다는 뜻이며, 명세의 모든 요청·응답·권한 조건을 검증했다는 뜻은 아니다.

2026-10-21 API-004 후속 변경: 온보딩 세부 관심사 선택을 Q1·Q2 각각 1개 저장으로 대체했다. 필수 `answers`를 받고 기존 `subtopicIds`는 거절한다. DB 추가 마이그레이션은 `c0e5f3b8a124`이며 실제 적용 상태는 별도 확인한다. 프론트 호출부는 이번에 수정하지 않았다. 아래 API 개수는 최초 검증일의 기록이다.

- 현재 OpenAPI: **32개 작업** = 명세 번호에 대응하는 공개 V1 API **24개** + 번호 없는 로컬 개발용 API **6개** + 번호 없는 상태 확인 API **2개**. 개발용 API는 `ENABLE_DEV_API=true`일 때만 나타난다.
- 명세 번호는 API-001~API-106 중 **API-003이 결번**이라 실제 번호가 붙은 항목은 105개다. 현재 표는 그중 구현된 24개만 다룬다.
- 경로 끝의 `/`는 붙이지 않는다. 이는 [명세의 공통 계약](https://app.notion.com/p/15f04c45a961825c9172817e6931113e) 및 현재 라우터와 일치한다. `backend/README.md`의 전체 목표 API 표에는 과거의 끝 `/` 표기가 남아 있다.

## 1. 명세 번호에 대응하는 공개 API — 24개

### 인증·정책·내 정보 — 8개

| 명세 번호 | 기능 | 현재 구현 메서드·경로 | 대조 결과 |
| --- | --- | --- | --- |
| [API-004](https://app.notion.com/p/c6004c45a961823ea9450197815f97ec) | 온보딩 일괄 제출 | `POST /api/v1/onboarding/complete` | **부분**: 온보딩 대기 세션의 프로필·동의·Topic·Q1/Q2를 저장한다. 기존 필수 프로필·동의 입력을 유지하며 `answers: {q1,q2}` 필수, `subtopicIds` 요청은 422. 응답은 `answers`와 `activeTopic.subtopicIds=[]`. Q2는 알림 동의가 아니며 기존 세부 관심사·구독 API는 유지한다. 이미지 연결은 미지원이다. |
| [API-005](https://app.notion.com/p/49604c45a96182499b66818e8cb63277) | 현재 세션·온보딩 상태 | `GET /api/v1/auth/session` | 대응. 비로그인도 200을 반환한다. |
| [API-006](https://app.notion.com/p/09104c45a96182a3a71a81cc8cd4cf8b) | 로그아웃 | `POST /api/v1/auth/logout` | 대응. 성공 시 본문 없는 204. |
| [API-007](https://app.notion.com/p/20504c45a961832d81c1018633526ca6) | 내 프로필 조회 | `GET /api/v1/users/me` | 대응. |
| [API-008](https://app.notion.com/p/b4d04c45a96183a09aff018332273008) | 내 프로필 정정 | `PATCH /api/v1/users/me` | **부분**: 닉네임 변경 가능. 명세의 업로드된 프로필 이미지 ID 연결은 API-096 미구현으로 현재 지원하지 않는다. |
| [API-011](https://app.notion.com/p/1d604c45a96183fe8fd301e365665c25) | 동의 항목·정책 조회 | `GET /api/v1/policies` | 대응. |
| [API-012](https://app.notion.com/p/b9004c45a96182e6aaf081e88f5478b7) | 내 동의 내역 조회 | `GET /api/v1/users/me/consents` | 대응. |
| [API-013](https://app.notion.com/p/1ec04c45a96182ddb406019679019980) | 동의 변경·선택 동의 철회 | `PATCH /api/v1/users/me/consents` | 대응. |

### Topic·Subtopic·내 관심사 — 8개

| 명세 번호 | 기능 | 현재 구현 메서드·경로 | 대조 결과 |
| --- | --- | --- | --- |
| [API-017](https://app.notion.com/p/0b104c45a96183ad86bf81780b1368ad) | Topic 목록·검색 | `GET /api/v1/topics` | 대응. |
| [API-018](https://app.notion.com/p/9b004c45a961839cb23901fb4cb41f8c) | Subtopic 목록·검색 | `GET /api/v1/topics/{topicId}/subtopics` | 대응. |
| [API-019](https://app.notion.com/p/a8d04c45a9618246a0dd01b4735ebfc7) | Subtopic 상세 | `GET /api/v1/subtopics/{subtopicId}` | 대응. |
| [API-021](https://app.notion.com/p/b6104c45a9618289847201d8e50faf4e) | 내 활성 Topic 목록 | `GET /api/v1/me/topics` | 대응. |
| [API-022](https://app.notion.com/p/0d204c45a961837aa5128159fb6ac096) | 내 Topic 추가·활성화 | `POST /api/v1/me/topics` | 대응. |
| [API-025](https://app.notion.com/p/91e04c45a96182fc9bd58168abc95cca) | 구독 중인 Subtopic 목록 | `GET /api/v1/me/topics/{topicId}/subtopics` | 대응. |
| [API-026](https://app.notion.com/p/b3504c45a961825797b8016a5b8489a7) | Subtopic 구독 | `PUT /api/v1/me/topics/{topicId}/subtopics/{subtopicId}` | 대응. |
| [API-027](https://app.notion.com/p/be704c45a961821580f501a37442a558) | Subtopic 구독 해제 | `DELETE /api/v1/me/topics/{topicId}/subtopics/{subtopicId}` | 대응. 성공 시 본문 없는 204. |

### 피드·콘텐츠·반응 — 8개

| 명세 번호 | 기능 | 현재 구현 메서드·경로 | 대조 결과 |
| --- | --- | --- | --- |
| [API-028](https://app.notion.com/p/5c304c45a96183b48dec01f8f45ea0b2) | Topic별 홈 추천 피드 | `GET /api/v1/topics/{topicId}/feed` | **부분**: 활성 Topic의 공개 글을 최신순으로 조회한다. 실제 추천·광고·저장 재노출은 아직 연결되지 않았다. |
| [API-029](https://app.notion.com/p/aea04c45a96182349dad0149b7d51131) | 콘텐츠 상세 | `GET /api/v1/contents/{contentId}` | 대응. 명세대로 `topicId` query가 필수다. |
| [API-035](https://app.notion.com/p/b0c04c45a96182ae847b81ed37d4f1da) | O/X 선호도 설정 | `PUT /api/v1/contents/{contentId}/preference` | 대응. |
| [API-036](https://app.notion.com/p/63f04c45a96183c2b2c881bd0a71720e) | O/X 선호도 취소 | `DELETE /api/v1/contents/{contentId}/preference` | 대응. `topicId` query 필수, 성공 시 204. |
| [API-037](https://app.notion.com/p/6a204c45a961830c90d081f15379f0de) | 좋아요 설정 | `PUT /api/v1/contents/{contentId}/like` | 대응. |
| [API-038](https://app.notion.com/p/0c304c45a9618355810581cdf701b6aa) | 좋아요 취소 | `DELETE /api/v1/contents/{contentId}/like` | 대응. `topicId` query 필수, 성공 시 204. |
| [API-039](https://app.notion.com/p/99a04c45a961826b928a81262bab31b4) | 북마크 저장 | `PUT /api/v1/contents/{contentId}/bookmark` | 대응. |
| [API-040](https://app.notion.com/p/57d04c45a9618260971301be21370c6c) | 북마크 취소 | `DELETE /api/v1/contents/{contentId}/bookmark` | 대응. `topicId` query 필수, 성공 시 204. |

## 2. 명세 번호가 없는 로컬 개발용 API — 6개

이 경로들은 `ENABLE_DEV_API=true`에서만 등록된다. 개발용 이메일 인증은 [API-001](https://app.notion.com/p/7e604c45a96182499feb810ab08e911e)·[API-002](https://app.notion.com/p/84c04c45a96183eb9dfe81d4a0d50f85)의 **소셜 로그인·콜백 구현으로 간주하지 않는다**. Agent 실행 생성은 HTTP API가 아니라 `backend/scripts/run_movie_agent_once.py`로 준비한다.

| 구분 | 현재 구현 메서드·경로 | 용도 |
| --- | --- | --- |
| 개발용 가입 | `POST /api/v1/dev/auth/register` | 로컬 테스트 사용자 생성 |
| 개발용 로그인 | `POST /api/v1/dev/auth/login` | 이메일·비밀번호로 로컬 세션 발급 |
| Agent 실행 목록 | `GET /api/v1/dev/agent-runs` | 저장된 내부 실행 결과 조회 |
| Agent 실행 상세 | `GET /api/v1/dev/agent-runs/{agentRunId}` | 초안·검사·판정·출처 조회 |
| 승인 초안 발행 재시도 | `POST /api/v1/dev/agent-runs/{agentRunId}/publish` | 조건을 충족한 초안의 공개 Content 발행 |
| 탈락 초안 로컬 미리보기 | `POST /api/v1/dev/agent-runs/{agentRunId}/publish-preview` | 개발 모드에서 검토 필요 표식과 함께 시험 게시 |

## 3. 명세 번호가 없는 상태 확인 API — 2개

| 현재 구현 메서드·경로 | 용도 |
| --- | --- |
| `GET /health` | FastAPI 상태 확인 |
| `GET /health/db` | PostgreSQL 연결 확인 |

## 검증 방법과 범위

- 현재 서버의 `http://localhost:8000/openapi.json`에서 32개 작업을 확인하고, [V1 라우터](../backend/app/api/v1/router.py), [개발용 라우터 등록](../backend/app/main.py), [백엔드 목표 API 목록](../backend/README.md)을 함께 대조했다.
- 이 목록은 **구현된 HTTP 작업의 번호 매핑**이다. 완전한 명세 준수 판정이나 전체 105개 API의 구현 현황표는 아니다. 특히 API-004·008·028은 위의 기능 차이를 남겨 두었다.
- `ENABLE_DEV_API=false`에서는 개발용 6개가 OpenAPI에서 사라져 공개 V1 24개와 상태 확인 2개만 남는다.
