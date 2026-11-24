# 관리자 상세 review 메타데이터 FE 반영 (11/19)

기준은 fetch로 확인한 `origin/Checking` `ab7aa22946663f48fd73e4252c9162d7d7345d01`이다. BE PR #71·#89가 포함되어 있다. 작업 브랜치는 독립 worktree의 `claude/admin-review-metadata-1119`이다. PR #88(E2E 리허설 테스트·체크리스트)의 파일은 수정하지 않았다.

BE 권한 Provider·Migration·Router·감사 트랜잭션·적재기는 변경하지 않았다. 승인·반려 실행 버튼은 계속 비활성이며, 화면 어디에서도 POST를 호출하지 않는다.

## BE 계약 (코드 확인)

- `backend/app/schemas/admin_content.py`: 상세 응답에 `review: { allowedActions, disabledReason }`가 추가됐다. 스키마 기본값은 빈 배열과 `unavailable`이다.
- `backend/app/services/admin_review_eligibility_service.py`: 순서대로 판정한다.
  1. 현재 쓰기 권한이 없으면 `permission_denied`.
  2. 삭제됐으면 `deleted`.
  3. active/pending_review + needs_review가 아니면 `content_not_reviewable`.
  4. Draft가 없거나 checking/approved가 아니면 `draft_not_reviewable`.
  5. 모두 통과하면 `["approve","reject"]` + `null`.
  - 권한 조회 장애는 상세 GET 전체가 503 `ADMIN_PERMISSION_UNAVAILABLE`로 실패한다.
- `backend/app/api/v1/admin/router.py`·`review_router.py`: 운영 v1 Router에 등록됐다. 서버가 배포되면 GET·POST가 실제로 열린다. POST 계약(6필드, 성공·replay 200, CSRF)은 PR #86 Client와 같다.
- `docs/ADMIN_BE_INTEGRATION_2026-11-18.md`: 메타데이터는 표시용이다. 필드 누락·오류는 FE가 비활성으로 처리한다. POST는 권한·버전·상태를 다시 검사한다.

## FE 반영

| 파일 | 내용 |
| --- | --- |
| `frontend/lib/admin-content.ts` | `AdminContentDetail.review` 타입 추가, `parseContentReview`로 BE 정의 값만 허용 |
| `frontend/lib/admin-content-review.ts` | `reviewAvailability` 추가: 서버가 허용한 조치만 확인창을 열 수 있게 함. `ReviewAction`은 같은 타입을 재사용 |
| `frontend/components/admin/content-review-panel.tsx` | 승인·반려 확인 버튼을 `reviewAvailability`로 제어하고 사유 문구 표시. 실행 버튼은 비활성 유지 |
| `frontend/tests/admin-content.test.cjs` | 메타데이터 정상·누락·형식 오류, 역할별 UI 상태 테스트 3개 추가. 상세 fixture에 BE가 항상 내려주는 `review` 추가 |
| `frontend/tests/admin-review-actions-browser-check.cjs` | 모의 상세 응답에 상태에 맞는 `review` 추가 (브라우저 검사 기준 유지) |

- **허용 값**: `allowedActions`는 approve/reject만, 중복 없이 받는다. `disabledReason`은 BE의 5개 값 또는 `null`만 받는다.
  - 두 값이 서로 맞지 않으면(사유 없음 + 빈 배열, 사유 있음 + 조치 있음) 신뢰하지 않는다.
  - 누락·형식 오류·불일치는 `review: null`로 처리한다. 상세 조회는 그대로 보여 주고 검수만 비활성이다. 다른 필드 검증(`updatedAt` 등)은 완화하지 않았다.
- **역할별 UI**:
  - viewer(`permission_denied`)는 두 확인 버튼 모두 비활성이고 "조회 전용" 문구가 보인다.
  - operator/admin은 서버가 허용한 조치의 버튼만 활성이다.
  - 서버는 허용했지만 화면의 저장 상태가 검수 불가이면, 다시 조회하라는 문구와 함께 비활성 처리한다.
- **판단 주체**: 클라이언트 판단은 보조 UI 조건이다. 최종 권한·버전·상태 검사는 BE가 POST마다 수행한다.
- **유지한 계약**: 기존 GET·POST Client는 재구현하지 않았다. 401/403/409/503 분류, `updatedAt` 원문 보존, 같은 requestId·같은 본문 재시도 계약은 그대로다. `prepareReviewRequest`도 바꾸지 않아 PR #88 리허설과 호환된다.

## 검증

| 검사 | 결과 |
| --- | --- |
| 관리자 FE 단위 테스트 (`admin-content`, `admin-content-input`) | 39개 통과 (기존 36 + 신규 3) |
| PR #88 E2E 리허설 7개를 이 브랜치 코드로 임시 실행 | 7개 통과 (파일은 실행 후 제거, PR #88 무변경) |
| `npm run typecheck`, `npm run build -- --webpack` | 통과 |
| 브라우저 검사 (`admin-review-actions-browser-check.cjs`) | 문법 확인만 함. 이 PC에 Playwright가 없어 실행하지 않음 |

## BE 실제 연결 선행 조건 (실제 완료 여부 확인 필요)

코드 병합만으로 아래 항목이 완료됐다고 보지 않는다.

- [ ] Checking DB에 Migration revision `47c1a8d6b302` 적용 (이전 revision `d7b3e9a1c452`, 백업 준비 후)
- [ ] 실제 관리자 계정에 권한 부여: `manage_admin_grants.py` bootstrap/grant, viewer·operator·admin 각 1개 이상
- [ ] Checking BE 서버에 PR #89 코드 배포 (Router 등록 포함)
- [ ] `https://checking.me-nu.kr` → `https://api.checking.me-nu.kr`에서 쿠키(`menu_checking_session`, Secure)·CORS credentials·CSRF Origin이 정상이고, 상세 GET 응답에 `review`가 포함됨
- [ ] PostgreSQL 감사 기록·적재기 상태 보호를 실제 Checking DB에서 검증

위 항목이 확인되면 PR #88 체크리스트의 역할별 실제 E2E를 진행한다. 실행 버튼 연결은 BE 승인과 실환경 검증 이후의 별도 FE 작업이다.
