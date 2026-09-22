# me_nu

AI 에이전트와 추천 알고리즘을 활용하는 초개인화 큐레이팅 플랫폼입니다.

이 문서는 API 명세서 V1.0을 기준으로 작성했습니다. 기존 모델, Alembic 마이그레이션, 테스트 및 에이전트 코드는 이동하거나 변경하지 않습니다.

## 구조 원칙

- 공개 API의 공통 Base Path는 /api/v1입니다.
- API 코드는 실제 endpoint의 리소스 경로를 기준으로 분리합니다.
- 인증은 서버 Session ID와 HttpOnly Cookie를 사용합니다.
- 변경 요청에는 CSRF 토큰과 Origin 검증을 적용합니다.
- 별도의 공개 /agent API는 만들지 않고 /chat API가 내부 에이전트 로직을 호출합니다.
- 2차 개발 API는 명세에는 유지하되 실제 구현 시점에 파일과 테스트를 추가합니다.

## 폴더 구조

API 구현과 직접 관련된 소스 폴더 및 파일만 표시합니다. 환경 변수, 의존성, Git 설정, 가상환경 및 캐시 파일은 제외했습니다.

~~~text
me_nu/
├─ agent/
│  ├─ movie/
│  ├─ music/
│  └─ py2ipynb.py
│
└─ backend/
   ├─ alembic/
   │  └─ versions/
   │
   ├─ app/
   │  ├─ main.py
   │  ├─ api/
   │  │  ├─ deps.py
   │  │  └─ v1/
   │  │     ├─ router.py
   │  │     ├─ auth/
   │  │     │  └─ router.py
   │  │     ├─ onboarding/
   │  │     │  └─ router.py
   │  │     ├─ policies/
   │  │     │  └─ router.py
   │  │     ├─ topics/
   │  │     │  └─ router.py
   │  │     ├─ subtopics/
   │  │     │  └─ router.py
   │  │     ├─ subtopic_requests/
   │  │     │  └─ router.py
   │  │     ├─ me/
   │  │     │  └─ topics.py
   │  │     ├─ contents/
   │  │     │  └─ router.py
   │  │     ├─ content_clusters/
   │  │     │  └─ router.py
   │  │     ├─ users/
   │  │     │  └─ me/
   │  │     │     ├─ router.py
   │  │     │     ├─ consents.py
   │  │     │     ├─ data.py
   │  │     │     ├─ data_requests.py
   │  │     │     ├─ activity_events.py
   │  │     │     ├─ bookmarks.py
   │  │     │     ├─ channels.py
   │  │     │     ├─ hidden_ads.py
   │  │     │     ├─ notification_settings.py
   │  │     │     ├─ reports.py
   │  │     │     ├─ rights_claims.py
   │  │     │     ├─ operations.py
   │  │     │     └─ subtopic_requests.py
   │  │     ├─ channels/
   │  │     │  └─ router.py
   │  │     ├─ chat/
   │  │     │  └─ router.py
   │  │     ├─ dm/
   │  │     │  └─ router.py
   │  │     ├─ ads/
   │  │     │  └─ router.py
   │  │     ├─ reports/
   │  │     │  └─ router.py
   │  │     ├─ rights_claims/
   │  │     │  └─ router.py
   │  │     ├─ profile_images/
   │  │     │  └─ router.py
   │  │     └─ admin/
   │  │        ├─ router.py
   │  │        ├─ dashboard.py
   │  │        ├─ users.py
   │  │        ├─ audit_logs.py
   │  │        ├─ contents.py
   │  │        ├─ report_groups.py
   │  │        ├─ bulk_actions.py
   │  │        ├─ rights_claims.py
   │  │        ├─ data_requests.py
   │  │        └─ privacy_incidents.py
   │  ├─ core/
   │  ├─ db/
   │  ├─ models/
   │  ├─ schemas/
   │  └─ services/
   │
   └─ tests/
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

## 현재 변경 범위

- 이 문서는 API 명세 기준의 목표 파일 위치와 endpoint 목록만 정의합니다.
- 기존 models/, Alembic 리비전, 테스트 및 에이전트 코드는 이동하지 않습니다.
- 기존 API 동작과 데이터베이스 스키마를 변경하지 않습니다.
- 폴더가 비어 있는 상태로 미리 생성하지 않고 해당 API 구현 시 패키지 파일, 라우터, 스키마 및 테스트를 함께 추가합니다.

## API 문서

- Swagger UI: /docs
- ReDoc: /redoc
- OpenAPI JSON: /openapi.json

실제 도메인은 아직 확정하지 않았으므로 특정 운영 URL을 고정하지 않습니다.
