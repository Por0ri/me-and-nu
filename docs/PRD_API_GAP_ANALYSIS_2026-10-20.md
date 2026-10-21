# PRD 기준 비관리자 API 구현 대조

기준일: 2026-10-20, 한국 시각. 코드: 로컬 `main`, `8df21b9`.

**2026-10-21 온보딩 후속 반영:** API-004는 `POST /api/v1/onboarding/complete`에서 기존 세부 관심사 선택을 Q1·Q2 각각 1개 저장으로 대체했다. `answers: {q1,q2}`가 필수이며 `subtopicIds` 요청은 거절한다. 닉네임·생년월일·`accountType=user`·동의·프로필 이미지·Topic 입력은 유지한다. 답변용 `onboarding_answers` 추가 마이그레이션은 `c0e5f3b8a124`이며 실제 적용 상태는 환경별로 확인한다. 프론트는 수정하지 않아 호출 계약·화면 연결이 남아 있다. 아래 전체 API 집계와 다른 영역의 상태는 최초 대조일 기준이다.

**관리자 전용 API를 제외한 V1.3 계약 72개 중 31개는 라우트가 있고 41개는 없다.** 기존 라우트에도 계정 유형, 추천, AI, 알림 등의 보완이 필요하다. 우선 구현할 범위는 최신 PRD의 1차 요구이며, 후속 기능과 차수 미정 기능은 구분한다.

## 1. 기준과 제외 범위

- PRD: [PRD V1.0 사본과 Notion 대조](https://docs.google.com/spreadsheets/d/1qzCRxMw6RndRQznNr5tqDdj1PXZ9i7uE1rxZtsbvdO4/edit)의 기능 요구사항, MoSCoW, 대조 기록. 2026-10-19 수정 사본을 확인했다. [원본](https://docs.google.com/spreadsheets/d/1ds2fpOpIIqj6eTnjdbdGzHeyG9nwLfgI03WRF6zKuCM/edit)은 연결 도구에서 권한 오류로 읽을 수 없어 원본과 사본의 최신 동기화 여부는 확인하지 못했다.
- HTTP 계약: [API 명세서 V1.3](https://app.notion.com/p/3f704c45a9618146b3def322d33c646f)의 95개 계약 전체. 이 중 `/api/v1/admin/*` 23개(API-074~095·105)를 제외했다. “관리자 대시보드 제외”는 관리자 전용 운영 API 전체를 제외하는 범위로 해석했다.
- **사용자 신고·권리침해·개인정보 요청은 포함**한다. 관리자 화면이 없어도 사용자가 접수하고 결과를 조회하는 기능이다.
- V1.3에서 삭제한 크리에이터·채널·DM API-045~047·056~064·101, 총 13개는 남은 구현 목록에 넣지 않았다.
- 개발용 6개와 상태 확인 3개는 제품 API 집계에서 제외했다. 현재 코드의 전체 HTTP 작업은 개발 API 활성화 시 40개(공개 31+개발 6+상태 3)다.
- 표의 **구현**은 해당 API의 핵심 실행 코드가 있다는 의미다. **부분**은 기능 일부만 제공, **불일치**는 현재 코드와 최신 계약의 차이, **미구현**은 해당 HTTP 메서드·경로가 없다는 뜻이다. PRD 전체 수용 기준이나 운영 배포 완료 판정은 아니다.
- V1.3의 FR 열과 차수 열은 여전히 v0.6 표기를 계승한다. 아래 PRD 연결은 기능군 단위이며, 상세 차수는 최신 PRD 사본을 우선했다. API 계약 개수와 PRD 기능 개수는 같지 않다.

## 2. 영역별 현황

| 영역 | V1.3 대상 | 라우트 있음 | 라우트 없음 | 핵심 판단 |
| --- | ---: | ---: | ---: | --- |
| 인증·온보딩 | 5 | 3 | 2 | 실제 OAuth 필요, user 계정 계약 정합화 |
| 내 계정·개인정보 | 13 | 5 | 8 | 닉네임·동의 기반 있음, 이미지·탈퇴·권리 요청 없음 |
| 분야·관심사 | 12 | 8 | 4 | 조회·구독·추가는 있음, 삭제/복구는 PRD 후속 |
| 피드·콘텐츠 | 7 | 2 | 5 | 최신순 피드·상세 기반, 추천·이벤트·관련 콘텐츠 보완 |
| 반응·저장 | 12 | 7 | 5 | 상태 저장·목록은 있음, 관심 연계·분류·재노출 없음 |
| AI 대화 | 9 | 3 | 6 | 생성·질문·결과는 있음, 이력·삭제·검수·기억 보완 |
| 광고·알림 | 8 | 3 | 5 | 알림함 기반만 있음, 광고·설정 및 명세 정합화 필요 |
| 사용자 신고·권리침해 | 6 | 0 | 6 | 사용자 접수·조회 전체 신규 구현 |
| **합계** | **72** | **31** | **41** | 부분 구현 포함, 완성률로 해석하지 않음 |

근거: [V1.3 계약 목록](https://app.notion.com/p/104fdd5bcd9b460a84f03770fa416e43), [공개 라우터 등록](C:/Users/USER/me_nu/backend/app/api/v1/router.py:18), [앱 및 개발 라우터 등록](C:/Users/USER/me_nu/backend/app/main.py:17). 현재 코드에서 `create_app(enable_dev_api=True).openapi()`를 생성해 메서드+경로를 대조했다.

## 3. 기존 API에서 먼저 보완할 기능

| PRD / 최신 계약 | 현재 상태 | 필요한 변경 |
| --- | --- | --- |
| FR-001 로그인·유지, V1.3 일반 회원 통합 | 실제 OAuth 없음. 계정 유형 consumer/creator 유지. 기본 세션 만료 30분 | API-001/002와 정식 가입 연결. 요청·응답·DB 제약·FE 타입을 user로 통일. PRD 30일 유지와 세션/재인증 정책 정합화 |
| FR-004 최초 취향 선택 | 2026-10-21: API-004는 기존 `subtopicIds` 대신 필수 Q1·Q2 각 1개 검증·저장을 구현. 응답에 `answers`, `activeTopic.subtopicIds=[]` 제공 | 프론트 호출 경로·입출력 타입과 화면 연결. 1차 애니 우선 범위를 카탈로그·노출에 반영 |
| FR-206 프로필 정정 | 닉네임만 실질 변경. 이미지 조회는 null, 이미지 ID 연결은 404 | API-096/097 구현 후 004/007/008에 업로드·연결·조회 적용 |
| FR-102·305·401~403·408 추천 | 최신순 조회. recommendationId/reason=null, seen=false. 실험 모델과 API 미연결 | API-033 이벤트, 추천 공급·다양성·중복/기조회 감점·한 줄 추천 이유 연결 |
| FR-104 자주 보는 주제 | 구독 시각 순 정렬 | 열람 기록에 따른 순서, 기록 부족 시 구독 순서 적용 |
| FR-311·701·913 콘텐츠 표현 | 단일 대표 이미지+본문/발췌. AI 생성 구분 있음. practicalInfo는 기본 null. V1.3에서 삭제한 `channel` 응답 필드 제거 완료(2026-10-21) | 캐러셀 카드 순서·이미지·캡션/본문 계약과 실용정보 매핑 |
| FR-405·409·713 피드백 | O/X·좋아요 현재 상태만 저장 | 사유 수집, 학습 이벤트, 좋아요↔작품/인물 관심 목록 연계. 토론장 개설 투표와 구분 |
| FR-705·718 저장 | 콘텐츠 북마크·목록·검색 구현. 재노출 스케줄/저장 종류 없음 | API-042/102/103, 일반/영수증 종류·분류 규칙·재노출 제외. 세 번째 저장 종류는 확정 필요 |
| FR-711 이어보기 | FE가 기존 피드/저장 목록에서 최대 4개를 선택 | API-032로 관련 콘텐츠 5개씩 추가 조회와 부족 사유 제공 |
| FR-709 공유 | FE 내부 상세 링크 공유/클립보드 있음. 상세 응답의 share.url은 원문 | API-034 및 상세 share 정보 통일. OS 공유창·미리보기는 FE 범위 |
| FR-601~612 AI 에이전트 | 실제 LLM 생성과 답·출처 저장 있음. 제한된 검색과 최근 대화 사용 | 다음 절의 1차 AI 요구 보완. 기존 3개 API만으로 전체 AI 완료 처리 불가 |
| FR-712·알림함, API-107/108 | 조회 시 최근 30일 분야별 최대 10개 새 글+환영 공지 보충 | V1.3 운영공지 전용 범위로 생산·목록·집계 정합화, 실제 공지 공급·설정/발송 정책 연결 |
| FR-1201·1204 AI/동의 안내 | 동의 버전·시각 저장 있음. 정책 API는 “AI 기능 미포함” 문구 반환 | 실제 제공 기능에 맞는 고지. 선택동의 철회가 수집·학습·발송 중단으로 이어지도록 적용 |

세션 유지 기간은 **코드 기본값과 예제 설정** 기준이며 실제 배포 환경변수는 조회하지 않았다. 원본이 사라진 북마크는 현재 목록에서 제외되고, 콘텐츠 hard-delete 시 저장 레코드도 cascade 삭제되므로 “원본이 없어도 저장 항목 관리” 요구와 모델 정합화가 필요하다.

코드 근거:
[계정 유형과 온보딩 검증](C:/Users/USER/me_nu/backend/app/schemas/onboarding.py:15) · [DB 계정 제약](C:/Users/USER/me_nu/backend/app/models/user.py:30) · [세션 기본값](C:/Users/USER/me_nu/backend/app/core/config.py:23) · [프로필 처리](C:/Users/USER/me_nu/backend/app/api/v1/users/me/router.py:16) · [피드 기본 필드](C:/Users/USER/me_nu/backend/app/schemas/feed.py:14) · [피드 및 상세 서비스](C:/Users/USER/me_nu/backend/app/services/feed_service.py:64) · [반응 저장](C:/Users/USER/me_nu/backend/app/services/reaction_service.py:22) · [저장 목록 필터](C:/Users/USER/me_nu/backend/app/repositories/bookmark_repository.py:20) · [저장 모델](C:/Users/USER/me_nu/backend/app/models/v1.py:307) · [이어보기 선택](C:/Users/USER/me_nu/frontend/lib/continue-reading.ts:11) · [공유 처리](C:/Users/USER/me_nu/frontend/components/home/content-card-actions.tsx:72) · [알림 생산](C:/Users/USER/me_nu/backend/app/services/notification_service.py:25) · [정책 고지](C:/Users/USER/me_nu/backend/app/services/policy_service.py:40).

## 4. AI 요구사항 대조

PRD 사본에서 **FR-601~612의 12개 기능·35개 세부 요구가 2026-10-19 결정으로 모두 1차 Must**가 됐다. [기능 요구사항 154~188행](https://docs.google.com/spreadsheets/d/1qzCRxMw6RndRQznNr5tqDdj1PXZ9i7uE1rxZtsbvdO4/edit#gid=1582685820&range=A154:Z188), [MoSCoW](https://docs.google.com/spreadsheets/d/1qzCRxMw6RndRQznNr5tqDdj1PXZ9i7uE1rxZtsbvdO4/edit#gid=761326038), [대조 기록](https://docs.google.com/spreadsheets/d/1qzCRxMw6RndRQznNr5tqDdj1PXZ9i7uE1rxZtsbvdO4/edit#gid=9001)을 기준으로 한다.

| FR | 요구 | 현재 구현 | 남은 작업 |
| --- | --- | --- | --- |
| 601 | 현재 분야 맥락·분야 전환 안내 | 부분 | 현재 분야 검색·전환 제안은 있음. 정책/실제 답변 품질 검증 |
| 602 | 수준 조절·모호한 질문 재질문 | 부분 | 답변은 고정 3~5문장 프롬프트. 상세도 제어·needs_clarification 분기 필요 |
| 603 | 읽던 글·선택 문단·읽은 범위 | 부분 | anchorContentId/selectedText 입력은 있음. contextCoverage는 근거 부족 여부 기반으로 실제 읽은 범위 측정과 다름 |
| 604 | 반대 관점·근거 | 미구현 | 반대 관점 전용 검색·비교·없음 처리 |
| 605 | 과거 글/대화 연결 | 부분 | 현재 분야 관련 글 검색은 있음. 과거 대화 검색·이어 대화 제안·알림 제한 없음 |
| 606 | 순서 있는 이어보기 | 미구현 | 목록 반환 계약·순서·부족/전환 안내 |
| 607 | 답변 검수·재시도 | 부분 | 실패 상태·근거 부족 안내는 있음. 개인정보/오정보 독립 검사·통과 후 전달·자동 재시도 없음 |
| 608 | 장기 기억·보관·삭제 | 부분 | 메시지 DB 저장·최근 5개 사용. 장기 검색·만료 삭제·사용자 삭제 API 없음 |
| 609 | 음성 입력 | 미구현 | FE/브라우저 음성 또는 외부 STT 방식 결정. 전용 백엔드 endpoint 필수로 단정하지 않음 |
| 610 | 수집 콘텐츠 근거 답변 | 부분 | 같은 분야 최근 100개에서 단어 겹침으로 최대 3개+현재 글, 각 3,500자 사용. 근거 준수/범위 밖 처리 보강 |
| 611 | 첫 진입 및 상시 AI 고지 | 부분 | API-048에 AI 고지 있음. 정책 API의 모순 문구 정리 및 화면 고지 일관성 확인 |
| 612 | 최대 500자 | 불일치 | 현재 서버 content 최대 1,000자. 500자 정책으로 정합화 |

작업 실행은 FastAPI BackgroundTasks이고 작업 결과는 DB 메시지로 남는다. 프로세스 재시작 후 미완료 작업 복구·내구성 큐는 없다. 이는 “실제 AI 호출 없음”과 다르다. 실제 호출 코드는 이미 있으나 이번 대조에서 외부 LLM 성공이나 답변 품질을 검증하지 않았다.

근거: [대화 라우트](C:/Users/USER/me_nu/backend/app/api/v1/chat/router.py:22) · [입력·응답 스키마](C:/Users/USER/me_nu/backend/app/schemas/chat.py:27) · [검색·생성·저장](C:/Users/USER/me_nu/backend/app/services/chat_service.py:199) · [LLM 호출](C:/Users/USER/me_nu/backend/app/integrations/llm.py:25).

## 5. 현재 라우트가 있는 API 31개

아래 경로 앞에는 모두 `/api/v1`이 붙는다. API 번호는 개별 V1.3 계약 링크다.

| API | 기능 / PRD 관련 기능 | 메서드와 경로 | 상태 | 구현 내용 또는 남은 작업 |
| --- | --- | --- | --- | --- |
| [API-004](https://app.notion.com/3f704c45a96181efba2fdaddb843c5e8) | 회원·기본 프로필 생성(온보딩 일괄 제출)<br>FR-002·004·1204 | `POST /onboarding/complete` | 부분 | 2026-10-21: 온보딩 대기 세션의 프로필·동의·Topic·Q1/Q2 저장. 만 14세·동의·활성 Topic 검증, accountType=user. 필수 answers, subtopicIds 요청은 422. 응답 answers 및 activeTopic.subtopicIds=[]. 이미지 연결 미지원·프론트 계약 갱신 필요. |
| [API-005](https://app.notion.com/3f704c45a96181a88acce1a8f10e4d6a) | 현재 세션·온보딩 상태 조회<br>FR-001 | `GET /auth/session` | 불일치 | 세션/CSRF/온보딩 상태 조회 구현. accountType이 consumer/creator이며 로그인 유지 기본값 30분. |
| [API-006](https://app.notion.com/3f704c45a9618149866cd91caeb07fed) | 로그아웃<br>보조 계약 | `POST /auth/logout` | 구현 | 현재 세션·쿠키 폐기, 반복 로그아웃 204. |
| [API-007](https://app.notion.com/3f704c45a9618163b001f267a48a935e) | 내 프로필 조회<br>FR-206 | `GET /users/me` | 부분·불일치 | 내 정보 조회 구현. profileImageId/Url은 항상 null, accountType 구형. |
| [API-008](https://app.notion.com/3f704c45a96181cf8c71c1002c9895e6) | 내 프로필 정정<br>FR-206 | `PATCH /users/me` | 부분·불일치 | 닉네임 수정·이미지 연결 해제만 가능. 이미지 ID 연결은 404, accountType 구형. |
| [API-011](https://app.notion.com/3f704c45a96181feab39c19dc27b8b96) | 동의 항목·정책 조회<br>FR-001·FR-1201·FR-1204 | `GET /policies` | 부분 | 정책·버전 조회 구현. AI 미제공이라는 오래된 고지와 미정 보관정책 보완 필요. |
| [API-012](https://app.notion.com/3f704c45a9618104883cd3a4eb3accd0) | 내 동의 내역 조회<br>FR-206·FR-1204 | `GET /users/me/consents` | 구현 | 항목별 최신 동의 상태·버전·시각 조회. |
| [API-013](https://app.notion.com/3f704c45a96181069a1bfd4c085b6b72) | 동의 변경·선택 동의 철회<br>FR-206·FR-1204·FR-712 | `PATCH /users/me/consents` | 구현 | 선택동의 변경/철회와 이력 저장. 추천 처리 중단 등 후속 효과는 별도 연결 필요. |
| [API-017](https://app.notion.com/3f704c45a961810786a9db8e37a3eed9) | 토픽 목록·검색<br>FR-004 | `GET /topics` | 구현 | 활성 분야 검색/조회. 1차 애니 우선 노출 정책은 별도 정합화. |
| [API-018](https://app.notion.com/3f704c45a9618152996ecf153689eec6) | 세부 주제 목록·검색<br>FR-004·FR-103 | `GET /topics/{topicId}/subtopics` | 구현 | 평면 세부 주제 목록·검색·커서 조회. |
| [API-019](https://app.notion.com/3f704c45a96181d3b2b1c9c108e0c34f) | 세부 주제 상세<br>FR-004·FR-103 | `GET /subtopics/{subtopicId}` | 구현 | 세부 주제·소속 분야 조회. |
| [API-021](https://app.notion.com/3f704c45a961811fafbac13c97c8b7ea) | 내 활성 Topic 목록<br>FR-005·FR-101·FR-706 | `GET /me/topics` | 구현 | 내 활성/삭제 분야 조회. 복수 분야 기능은 최신 PRD 추후 논의. |
| [API-022](https://app.notion.com/3f704c45a9618106ab1ceca5165bb420) | 내 Topic 추가·활성화<br>FR-005·FR-006 | `POST /me/topics` | 구현 | 분야+소속 세부 주제 추가. 최신 PRD 추후 논의 기능을 선행 구현한 상태. |
| [API-025](https://app.notion.com/3f704c45a961816d82dadf5f13c2234b) | 구독 중인 세부 주제 목록<br>FR-103·FR-104·FR-706 | `GET /me/topics/{topicId}/subtopics` | 부분 | 구독 목록은 구독 시각 순. 자주 본 순서에 필요한 열람 기록 연결 없음. |
| [API-026](https://app.notion.com/3f704c45a96181e9b343e947c865467a) | 세부 주제 구독<br>FR-103 | `PUT /me/topics/{topicId}/subtopics/{subtopicId}` | 구현 | 동일 분야 세부 주제 단건 구독·중복 방지. FR-103 차수 충돌. |
| [API-027](https://app.notion.com/3f704c45a96181628f94d660379dab49) | 세부 주제 구독 해제<br>FR-103 | `DELETE /me/topics/{topicId}/subtopics/{subtopicId}` | 구현 | 세부 주제 단건 해제·반복 204. FR-103 차수 충돌. |
| [API-028](https://app.notion.com/3f704c45a9618195ae2ef914d7c0ca59) | Topic별 홈 추천 피드<br>FR-101·FR-102·FR-104·FR-305·FR-306·FR-401·FR-402·FR-403·FR-408·FR-701·FR-705·FR-1101·FR-1102 | `GET /topics/{topicId}/feed` | 부분 | 최신순 공개 글+필터/커서. 개인화·다양성·기조회 감점·추천 이유·저장 재노출·광고 공급 없음. |
| [API-029](https://app.notion.com/3f704c45a9618122ac45fa9c0debbce0) | 콘텐츠 상세 조회<br>FR-201·202·308·311·913 | `GET /contents/{contentId}` | 부분·불일치 | 본문/발췌·출처·본인 반응 구현. `channel` 응답·Swagger 제거 완료(2026-10-21). 실용정보 null, share.url은 원문, 캐러셀 계약 없음. |
| [API-035](https://app.notion.com/3f704c45a96181fa9388c453fb4bbb4a) | O/X 선호도 설정<br>FR-102·FR-405 | `PUT /contents/{contentId}/preference` | 구현 | O/X 현재 상태 저장. 학습 반영·누적 행동 이벤트·피드백 사유는 별도 미구현. |
| [API-036](https://app.notion.com/3f704c45a961819e8231f3e9776368b6) | O/X 선호도 취소<br>FR-405 | `DELETE /contents/{contentId}/preference` | 구현 | O/X만 취소하고 좋아요/저장은 유지. |
| [API-037](https://app.notion.com/3f704c45a961816b9018d04af8e1cc40) | 좋아요 설정<br>FR-713 | `PUT /contents/{contentId}/like` | 구현 | 좋아요 현재 상태 저장. 작품/인물 관심 목록 연계와 추천 학습은 미구현. |
| [API-038](https://app.notion.com/3f704c45a9618181be4cf0a0abef9e9e) | 좋아요 취소<br>FR-713 | `DELETE /contents/{contentId}/like` | 구현 | 좋아요 취소. 해당 좋아요가 만든 관심 항목 제거 기능은 연결되지 않음. |
| [API-039](https://app.notion.com/3f704c45a9618196b719f62dcfa3f7cd) | 북마크 저장<br>FR-202·FR-718 | `PUT /contents/{contentId}/bookmark` | 구현 | 북마크·분야/세부주제 태그 저장. 일반/영수증 등 저장 종류 구분은 없음. |
| [API-040](https://app.notion.com/3f704c45a9618105aee7e6a9c21c8cfc) | 북마크 취소<br>FR-718 | `DELETE /contents/{contentId}/bookmark` | 구현 | 콘텐츠 ID 기준 북마크 취소. 저장 ID 취소와 별개. |
| [API-041](https://app.notion.com/3f704c45a96181d79a49de3909763072) | 저장함 목록·검색<br>FR-708·FR-718 | `GET /users/me/bookmarks` | 구현 | 분야·검색·커서·이미지/출처 등 목록 제공. 원본 숨김/삭제 시 제외; 저장 종류별 조회 미지원. |
| [API-048](https://app.notion.com/3f704c45a96181d2b84ffb2102bd8f55) | AI 대화 생성<br>FR-601·FR-603·FR-611 | `POST /chat/sessions` | 구현 | 분야/기준 글 소속 확인, 대화 DB 생성, AI 고지. 보관 만료 정책은 미연결. |
| [API-052](https://app.notion.com/3f704c45a961818ea8bdfa8b7f8102ab) | 질문 전송·AI 답변 생성<br>FR-501·FR-601·FR-602·FR-603·FR-607·FR-608·FR-612 | `POST /chat/sessions/{sessionId}/messages` | 부분 | 질문 저장·실제 LLM 생성 구현. PRD 500자와 코드 1,000자 불일치, 재질문·답변 검수·장기 기억 보완. |
| [API-053](https://app.notion.com/3f704c45a9618194ad4bcc69acb58f5a) | AI 답변 생성 상태·결과<br>FR-501·FR-601·FR-602·FR-607 | `GET /chat/jobs/{jobId}` | 부분 | 작업 상태/답/출처·분야 전환 반환. needs_clarification/blocked 분기·독립 검수 미구현. |
| [API-107](https://app.notion.com/3f704c45a961816cb6c2f1a62e4c0ee2) | 내 알림 목록 조회<br>알림함, FR-712 | `GET /users/me/notifications` | 불일치 | 목록/커서/미읽음 필터 구현. V1.3은 운영공지 전용인데 현재 분야 새 글을 조회 시 자동 생성해 혼합. |
| [API-108](https://app.notion.com/3f704c45a96181968e74ccef46d9ae3e) | 내 미확인 알림 개수 조회<br>알림함, FR-712 | `GET /users/me/notifications/unread-count` | 불일치 | 전체 미읽음 개수 구현. 분야 새 글도 집계하므로 V1.3 알림 범위 정합화 필요. |
| [API-109](https://app.notion.com/3f704c45a96181c0b6afe0a765e2f495) | 내 알림 읽음 처리<br>알림함, FR-712 | `PATCH /users/me/notifications/{notificationId}` | 구현 | 본인 단건 읽음·최초 readAt 보존. |

## 6. 라우트가 없는 API 41개

**이 41개 모두가 1차 필수라는 뜻은 아니다.** “후속·차수 확인·정책 확인” 표기와 다음 절을 함께 적용한다.

| API | 기능 / PRD 관련 기능 | 메서드와 경로 | 상태 | 구현 내용 또는 남은 작업 |
| --- | --- | --- | --- | --- |
| [API-001](https://app.notion.com/3f704c45a9618129aac0fb2b55c85e71) | 소셜 로그인 시작<br>FR-001 | `POST /auth/login/{provider}` | 미구현 | 카카오/네이버 실제 OAuth 시작·재인증 URL 발급. |
| [API-002](https://app.notion.com/3f704c45a96181208b87d1d534f49876) | 소셜 콜백·세션 발급<br>FR-001 | `GET /auth/login/{provider}/callback` | 미구현 | OAuth callback/state 검증·계정 연계·세션 발급. 개발 로그인은 대체 완료가 아님. |
| [API-009](https://app.notion.com/3f704c45a961817b99cce249d7206f28) | 회원 탈퇴<br>FR-205 | `DELETE /users/me` | 미구현 | 즉시/유예 탈퇴, 재인증, 전체 세션 종료, 데이터 처리. |
| [API-010](https://app.notion.com/3f704c45a96181b2a6abf567c56ce2ba) | 유예 중 탈퇴 복구<br>FR-205 | `POST /auth/withdrawal-restorations` | 미구현 | 유예 중 동일 소셜 계정 복구. 탈퇴 삭제 배치와 함께 설계. |
| [API-014](https://app.notion.com/3f704c45a961813e93d1e81d78038a54) | 내 활동·관심 정보 열람<br>FR-206·FR-202 | `GET /users/me/data` | 미구현 | 본인 열람·반응·관심·기억 정보 조회. |
| [API-015](https://app.notion.com/3f704c45a96181e49431eb6547edbe2c) | 개인정보 권리 요청 접수<br>FR-206 | `POST /users/me/data-requests` | 미구현 | 개인정보 정정/삭제/처리정지 요청 접수. |
| [API-016](https://app.notion.com/3f704c45a96181699f72d1256ca0134e) | 개인정보 권리 요청 처리 결과 조회<br>FR-206 | `GET /users/me/data-requests` | 미구현 | 본인 개인정보 요청 목록·처리 결과. |
| [API-020](https://app.notion.com/3f704c45a961814f971dfc3365b1352a) | 없는 세부 주제 요청<br>FR-103 | `POST /subtopic-requests` | 차수 확인·미구현 | 없는 세부 주제 요청. FR-103 기능표 2차/MoSCoW 1차 충돌. |
| [API-023](https://app.notion.com/3f704c45a9618140ad14d8a6f8f80f2c) | 관심사 탭 삭제<br>FR-006 | `DELETE /me/topics/{topicId}` | 후속·미구현 | 분야 삭제/학습기록 처리. 최신 PRD FR-006은 추후 논의. |
| [API-024](https://app.notion.com/3f704c45a96181cb810cd8329dbc55fe) | 관심사 탭 복구<br>FR-006 | `POST /me/topics/{topicId}/restorations` | 후속·미구현 | 삭제 분야 복구. 복구 기한 및 기록 복구 정책 필요. |
| [API-030](https://app.notion.com/3f704c45a961815db0fbf5165388ddd3) | 같은 주제 묶음 조회<br>FR-304·FR-308 | `GET /content-clusters/{clusterId}` | 미구현 | 같은 사건/주제 콘텐츠 묶음·출처 조회. 묶음 생성 로직도 필요. |
| [API-031](https://app.notion.com/3f704c45a9618174bef6c6d172b60b47) | 연관 주제 탐색<br>FR-703 | `GET /contents/{contentId}/exploration` | 미구현 | 같은 분야의 연관 주제 탐색. 최신 PRD 인접 콘텐츠 요구와 응답 정합화. |
| [API-032](https://app.notion.com/3f704c45a96181aab473e4eb8a720723) | 콘텐츠 이어보기<br>FR-711 | `GET /contents/{contentId}/related` | 미구현 | 관련 콘텐츠 5개씩 추가 조회. FE의 기존 피드/저장 목록에서 최대 4개 선택과 다름. |
| [API-033](https://app.notion.com/3f704c45a9618171b104cd68ce217468) | 노출·열람 기록 수집<br>FR-102·FR-305·FR-406·FR-407 | `POST /users/me/activity-events` | 미구현 | 노출/열람/체류 수집, eventId 멱등 처리. 추천·seen·자주 본 순서의 선행 작업. |
| [API-034](https://app.notion.com/3f704c45a961816fa15ede5e2ee4c9e7) | 콘텐츠 공유 정보 조회<br>FR-709 | `GET /contents/{contentId}/share` | 미구현 | 공유 대상 URL·문구 계약. FE 내부 링크 공유/클립보드 기능은 이미 존재. |
| [API-042](https://app.notion.com/3f704c45a96181fa87caedcab116f614) | 저장 분류 정정·재노출 끄기<br>FR-705 | `PATCH /users/me/bookmarks/{savedItemId}` | 미구현 | 저장 분류 정정·재노출 끄기 및 이후 추천 제외. |
| [API-043](https://app.notion.com/3f704c45a961817ebd09da5a8e63fe88) | 토픽별 관심 목록<br>FR-713 | `GET /me/topics/{topicId}/interests` | 미구현 | 좋아요/저장과 연결된 작품·인물 관심 목록. |
| [API-044](https://app.notion.com/3f704c45a9618145abdcfed68096a3da) | 관심 목록 항목 제거<br>보조 계약, 구 FR-715 삭제 | `DELETE /me/topics/{topicId}/interests/{entityId}` | 범위 확인·미구현 | 관심 항목 삭제 보조 계약. 구 FR-715 삭제/세부 요구 부재를 먼저 정리. |
| [API-049](https://app.notion.com/3f704c45a96181beb674c19a49a772dc) | 내 AI 대화 목록<br>FR-608 | `GET /chat/sessions` | 미구현 | 내 AI 대화 목록·재진입. |
| [API-050](https://app.notion.com/3f704c45a96181db8053ef8a183b5cfb) | AI 대화 정보 조회<br>FR-601·FR-603·FR-611·FR-1202 | `GET /chat/sessions/{sessionId}` | 미구현 | 대화 기본 정보·상태 조회. |
| [API-051](https://app.notion.com/3f704c45a961815b9430e2a082993845) | AI 대화 메시지 목록<br>FR-607·FR-608 | `GET /chat/sessions/{sessionId}/messages` | 미구현 | 과거 메시지·근거 목록 조회. |
| [API-054](https://app.notion.com/3f704c45a961814cb6f0cdf79174dee8) | AI 대화 삭제<br>FR-206·FR-608 | `DELETE /chat/sessions/{sessionId}` | 미구현 | 대화 삭제·개인정보/기억 제거 연계. |
| [API-055](https://app.notion.com/3f704c45a96181e8a396e7dfda20e20f) | 대화 보관 정책 조회<br>FR-1202 | `GET /chat/retention-policy` | 정책 확인·미구현 | 보관정책 조회. 1차 FR-608과 추후 FR-1202 차수·보관기간 충돌 해결 필요. |
| [API-065](https://app.notion.com/3f704c45a961814ea8edcce7d33a8e32) | 현재 토픽 광고 조회<br>FR-1101·FR-1102 | `GET /topics/{topicId}/ads` | 미구현 | 현재 분야 광고·표시·타기팅 공급. |
| [API-066](https://app.notion.com/3f704c45a961819ebd03db74707f3f06) | 광고 노출 사유 조회<br>FR-1103 | `GET /ads/{adId}/reason` | 미구현 | 광고 노출 이유/사용 조건 조회. |
| [API-067](https://app.notion.com/3f704c45a96181a79791c421335d3a4f) | 광고 그만 보기<br>FR-1103 | `PUT /users/me/hidden-ads/{adId}` | 미구현 | 광고 그만 보기·재노출 제외. |
| [API-068](https://app.notion.com/3f704c45a961815990f1e8c715df504d) | 알림 설정 조회<br>FR-712 | `GET /users/me/notification-settings` | 미구현 | 알림 종류별 설정 조회. 화면 토글만으로 완료 아님. |
| [API-069](https://app.notion.com/3f704c45a961815c9f00e60803f38bde) | 알림 설정 변경<br>FR-712 | `PATCH /users/me/notification-settings` | 미구현 | 설정 저장·발송 적용, 광고 동의/야간 제한 등 PRD 규칙 연결. |
| [API-070](https://app.notion.com/3f704c45a961811e9cb1c081e4726a62) | 콘텐츠 신고 접수<br>FR-710 | `POST /reports` | 미구현 | 사용자 콘텐츠 신고 접수. 관리자 제외 범위에도 포함. |
| [API-071](https://app.notion.com/3f704c45a961812d8034ffe88567646e) | 내 신고 처리 결과<br>FR-710 | `GET /users/me/reports` | 미구현 | 본인 신고 목록·처리 결과 조회. |
| [API-072](https://app.notion.com/3f704c45a96181539dd1c511dd5f002b) | 권리침해 요청 접수<br>FR-818 | `POST /rights-claims` | 미구현 | 권리침해 소명·증빙 접수. 일반 신고와 별개. |
| [API-073](https://app.notion.com/3f704c45a961816ba870ece3c85b4f60) | 내 권리침해 요청 조회<br>FR-818 | `GET /users/me/rights-claims` | 미구현 | 본인 권리침해 요청 목록. |
| [API-096](https://app.notion.com/3f704c45a9618125ae35d192a0d4f95e) | 프로필 이미지 업로드<br>FR-206 | `POST /profile-images` | 미구현 | 프로필 이미지 업로드·소유권/만료 검증·004/008 연결. |
| [API-097](https://app.notion.com/3f704c45a96181588923fc5e3151661b) | 미사용 프로필 이미지 폐기<br>FR-206 | `DELETE /profile-images/{imageId}` | 미구현 | 미사용 프로필 이미지 삭제. |
| [API-098](https://app.notion.com/3f704c45a96181fbbf53fe90961c3e34) | 개인정보 권리 요청 상세·진행 상태<br>FR-206 | `GET /users/me/data-requests/{requestId}` | 미구현 | 본인 개인정보 요청 상세·진행 상태. |
| [API-099](https://app.notion.com/3f704c45a96181348f3efe87282c346e) | 내 신고 상세·처리 결과<br>FR-710 | `GET /users/me/reports/{reportId}` | 미구현 | 본인 신고 상세·처리 결과. |
| [API-100](https://app.notion.com/3f704c45a96181bea1b8c78d9dd76f17) | 내 권리침해 요청 상세<br>FR-818 | `GET /users/me/rights-claims/{claimId}` | 미구현 | 본인 권리침해 요청 상세·조치 상태. |
| [API-102](https://app.notion.com/3f704c45a9618167878cea87b70ef1b1) | 내 저장 항목 상세<br>FR-718 | `GET /users/me/bookmarks/{savedItemId}` | 미구현 | 저장 ID 상세·원본 이용 불가 상태 표시. |
| [API-103](https://app.notion.com/3f704c45a961813ba304db01ee846fab) | 저장 항목 ID로 저장 취소<br>FR-718 | `DELETE /users/me/bookmarks/{savedItemId}` | 미구현 | 원본이 없어도 저장 ID로 저장 취소. |
| [API-104](https://app.notion.com/3f704c45a96181a98d94cbc7b297d060) | AI 대화 삭제 작업 상태<br>FR-206·FR-608 | `GET /users/me/operations/{operationId}` | 미구현 | 대화 삭제 작업 상태. 삭제 실행/복구와 함께 구현. |
| [API-106](https://app.notion.com/3f704c45a961818eacf1febcc8eee4e8) | 내 세부 주제 요청 접수 내역<br>FR-103 | `GET /users/me/subtopic-requests` | 차수 확인·미구현 | 세부 주제 요청 접수 내역. FR-103 차수 확정 후 진행. |

## 7. 신규 계약 또는 내부 서비스가 필요한 PRD 항목

| 항목 | 기존 API와의 관계 | 필요한 정리 |
| --- | --- | --- |
| 캐러셀 콘텐츠, FR-701·303·312 | 028/029 응답 확장 후보 | 다중 카드 순서·이미지·캡션·접근성·상세 링크. 새 API 번호가 필수인 것은 아님 |
| 피드백 사유, FR-409 | 035/037 확장 또는 별도 계약 | 사유 선택·무작위 제안·건너뛰어도 원반응 유지·학습 연결 |
| 저장 종류, FR-718 | 039/041/042 확장 후보 | 일반/영수증과 미정인 세 번째 종류, 변경·필터 규칙 |
| 보기 방식 유지, FR-717 | 현재 FE localStorage 사용 | 같은 브라우저 유지가 요구 범위를 충족하는지, 계정/기기간 동기화가 필요한지 확정. 별도 API를 자동 추가하지 않음 |
| AI 재질문/관점/이어보기, FR-602·604~607 | 052/053의 메시지/결과 형식 확장 후보 | 답변 외 결과 타입과 검사 상태·순서 목록 정의 |
| 민감 분야 응답, FR-501 | AI 실행 서비스에 적용 | S1~S6 분류·고지·차단·판정 기록. 고정 문안 미정 |
| 퀴즈, FR-716 | 현 V1.3 전용 계약 없음 | 차수 확정 후 출제·답안·채점·해설/시도 이력 필요 여부 설계 |
| 토론장, FR-1001~1002 | V1.3에 계약 없음 | 주제 건의→개설 찬성투표→조건 충족 개설. 득표 수·유지 기간·차수 확정 후 공식 API 편성 |
| 수집·생성·검수, FR-301~314 | 사용자 HTTP API와 별개인 내부 파이프라인 | Agent·원고·출처·판정 및 영화 DB 발행은 일부 구현. 애니 자동 DB 발행·정기 공급·캐러셀 조립·검수 규칙을 연결 |
| 추천 학습·주제 묶음 | 028/030/033 및 내부 배치 | 실험 추천 코드·클러스터 테이블만으로 서비스 완료가 아님. 공급/학습/평가 연결 필요 |

영화 CLI는 생성 결과의 `publish_candidate + draft.approved`를 DB 공개 콘텐츠로 발행한다. **approved는 사람 검수 완료를 의미하지 않고**, 사실 확인 필요 항목은 `needs_review`로 남아 발행될 수 있다. PRD의 검수 통과·발행 정책과 다시 맞춰야 한다. 애니 Agent는 파일 산출, 별도 seed는 커밋된 파일 수동 적재 경로이며 애니 생성부터 DB 발행까지 자동 연결은 확인되지 않았다.

근거: [영화 실행·발행 연결](C:/Users/USER/me_nu/backend/app/services/movie_agent_runner.py:85) · [발행 조건](C:/Users/USER/me_nu/backend/app/services/movie_agent_publication.py:107) · [애니 출력](C:/Users/USER/me_nu/agent/anime/anime_agent.py:4859) · [파일 적재](C:/Users/USER/me_nu/backend/app/seeds/agent_contents.py:184) · [추천 실험](C:/Users/USER/me_nu/model/algo/content_v0.py:148) · [보기 설정 유지](C:/Users/USER/me_nu/frontend/components/home/home-screen.tsx:117).

## 8. 차수와 계약을 먼저 정리할 항목

| 항목 | 확인된 불일치 / 범위 | 처리 기준 |
| --- | --- | --- |
| 복수 분야·분야 추가/삭제/복구 | 최신 PRD FR-005/006은 추후 논의, API 명세에는 1차 흔적 | 021/022는 기존 구현으로 유지해 기록. 023/024는 1차 미완료로 단정하지 않음 |
| 세부 주제·작품 구독/요청 | FR-103 기능표 2차, MoSCoW Must/1차 | 020/106과 026/027 출시 차수 확정. 최초 온보딩 세부 선택과 분리 |
| 퀴즈 | FR-716 기능표 2차, MoSCoW Must/1차 | 스크럼 확인 후 범위와 API 편성 |
| AI 보관 | FR-608은 1차로 승격됐으나 FR-1202는 추후 논의 | 050/054/055/104 및 만료 삭제 정책을 함께 결정 |
| 온보딩 Q1·Q2 저장 | 사용자 후속 확정: 각 질문에서 정확히 1개씩 선택하고 기존 온보딩 세부 관심사 선택은 제거. API 검증·답변 저장과 추가 마이그레이션 구현 | 프론트 연동은 후속. 기존 세부 관심사·`tap_topic` 데이터 및 구독 API는 유지. Q2는 정보 선호도이며 알림 동의로 처리하지 않음 |
| 저장·관심 목록 | FR-704/715 세부 요구 부재, API-044는 구 FR-715 삭제 표기 | FR-713 관심 연계는 유지. 보조 API 필요성 별도 결정 |
| 저장 3종 | 세 번째 종류 미정 | 임의 종류 추가 없이 계약 확정 |
| 소셜·콘텐츠 후속 | 애플 로그인, 복수 분야, 3줄 요약 등은 후속/추후 | 1차 신규 API 수에 섞지 않음 |
| 기타 2차 | FR-404 유사 사용자 추천, FR-702 읽기 옵션, FR-707 아침 요약, FR-708-3 저장 검색 | 041 검색처럼 선행 구현된 기능은 기록하되 1차 필수로 바꾸지 않음 |
| 커뮤니티·DM | 토론장 흐름은 정리됐으나 차수/정책 미정. DM은 재합의 대상, V1.3 채널 DM 삭제 | 토론장 상세 설계 별도, 삭제된 채널 DM API를 복원 대상으로 자동 포함하지 않음 |

## 9. 권장 구현 순서

다음은 PRD의 공식 차수가 아니라 현재 의존성을 기준으로 한 작업 순서다.

1. **기존 계약 정합화:** user 계정 모델·온보딩·세션·프로필, 500자 질문, AI 고지, 알림 타입, Q1·Q2 저장 계약의 프론트 연결·보관정책 결정.
2. **정식 회원 이용 흐름:** 카카오/네이버 OAuth와 온보딩 연결, 프로필 이미지, 탈퇴/복구, 개인정보 요청, 사용자 신고/권리침해 접수·결과 조회.
3. **추천·콘텐츠 기반:** 애니 콘텐츠 공급/표현 계약, 033 이벤트 → 028 추천/이유 → 030~032 묶음/탐색/이어보기. O/X·좋아요 학습과 관심 연계.
4. **1차 AI 완성:** 기존 048/052/053 확장, 049~051 이력, 054/104 삭제, 보관정책에 맞춘 055와 기억·검수·재질문·입력 제한.
5. **저장·알림·광고:** 042/102/103, 저장 종류/재노출, 068/069와 실제 알림 공급, 065~067 광고. 데이터 수집·동의/발송 규칙 연결.
6. **차수 확정 뒤 후속:** 분야 삭제/복구, 세부 주제 요청, 퀴즈, 토론장. 신규 API 번호는 계약 승인 후 정한다.

## 10. 확인 범위

- 현재 소스·서비스·스키마·모델, V1.3의 모든 계약 95개 및 접근 가능한 PRD 사본을 대조했다.
- 현재 앱 객체에서 OpenAPI 생성을 완료하고 공개 31개/개발 6개/상태 3개를 확인했다. DB 조회나 서버 실행 없이 확인한 등록 경로다.
- 저장함·대화·알림 기존 테스트 4개 파일 실행을 시도했으나 진행이 정체되어 중단했다. 전체 실행 결과나 통과 건수를 확정하지 않았다.
- 실제 DB 데이터·배포 서버·OAuth 공급자·LLM·알림 발송의 종단 간 성공 여부는 이번 결과에 포함하지 않는다.
- 애플리케이션 코드는 수정하지 않았다.
