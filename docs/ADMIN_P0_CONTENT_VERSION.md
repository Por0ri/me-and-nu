# Admin P0: 상세 updatedAt 계약

기준: `d46fcdcfe578feb60ea4eaa235c3dd58d93c45d2` (`origin/Checking`, 2026-11-13 fetch).
브랜치: `codex/admin-p0-content-version-1113`.
시작 작업 트리는 clean이었고, 기존 #74 브랜치 `3634ce3862d1f165d77242a70d8d4bfeb6803712`는 변경하지 않았다.

## 계약과 연결 경계

- 관리자 상세 GET `/api/v1/admin/contents/{contentId}` DTO에 필수 `updatedAt`을 추가했다. 운영 Router는 계속 미등록이다.
- 상세 Repository가 `Content.updated_at`을 조회하고, 기존 Service의 `**row` 전달을 통해 DTO까지 그대로 연결한다. `createdAt`이나 서버/브라우저 현재 시각을 사용하지 않는다.
- DTO는 strict `AwareDatetime`을 요구한다. 누락·null·naive datetime·숫자/문자열 강제 변환을 허용하지 않는다. DB datetime의 UTC 오프셋과 마이크로초를 JSON으로 직렬화하며 UTC는 `Z`로 표현한다.
- FE는 `YYYY-MM-DDTHH:mm:ss[.ffffff](Z|±HH:mm)` 문자열을 필수로 요구한다. 소수는 없거나 1~6자리이며, 실제 달력 날짜(윤년 포함), 00~23시, 00~59분/초와 오프셋 범위를 검사한다. 시간대 누락, 알 수 없는 오프셋 `-00:00`, 공백·추가 문자, 6자리 초과는 실패 처리한다.
- FE는 원문 문자열을 그대로 반환한다. `Date` 파싱·재직렬화·시간대 변환·밀리초 절삭을 하지 않는다. 1마이크로초 차이의 두 버전도 구분한다. 예: `2026-11-13T09:12:34.123456+09:00`.
- 목록 SQL/DTO와 기존 상태·출처·발행 조건은 그대로다. 버전 오류는 기존 상세/수정 읽기 오류로 처리되어 성공 화면과 검수 UI가 열리지 않는다.

다음 Slice는 `loadAdminContent → state.data → ContentReviewPanel(content)`에 이미 전달되는 `content.updatedAt`을 `expectedUpdatedAt`으로 그대로 복사해야 한다. 수정 화면의 최초 상세 seed와 작성 필드는 분리되어 있으며, `updatedAt`은 작성 필드에 포함되지 않는다. 이 작업은 요청 builder나 transport를 추가하지 않고 승인·반려 실행을 계속 비활성화한다. 충돌 시 재조회, requestId/CSRF, 저장 결과 검증은 후속 쓰기 연결 작업의 범위다.

PostgreSQL timestamp는 마이크로초 해상도와 소수 0~6자리 범위를 제공한다. [공식 날짜/시간 문서](https://www.postgresql.org/docs/current/datatype-datetime.html). DTO의 시간대/strict 제약은 [Pydantic datetime 문서](https://docs.pydantic.dev/latest/api/standard_library_types/#datetimes)를 참고했다.

## 검증

- BE 관련 **168개 통과**: 상세 SQL→Service→DTO→격리 HTTP, DB 버전 변경 반영, UTC/양수·음수 오프셋/마이크로초/후행 0, 검수 요청 DTO로 손실 없는 전달, 필수 OpenAPI 필드, 목록 계약 유지, 401/403/404, 기본 거부·운영 Router 미등록, 입력 준비 경로.
- FE 관련 **25개 통과**: 상세 파싱, 누락/잘못된 날짜·시간대·과도한 정밀도 거부, 원문 보존과 JSON 왕복, 동일 밀리초 내 다른 버전 구분, 목록/출처/등록·수정 입력 회귀. 수정 입력 fixture 보완 후 해당 7개만 재검증했다.
- `npm run typecheck`, `npm run build -- --webpack` 통과.
- 브라우저 **3종 × 1728px·390px 모두 통과**: 목록→상세/복귀, 출처/빈 출처, 잘못된 버전 차단·재시도, 등록/수정 초기값·입력 보존·이탈 경고, 401/403/404, 검수 확인창과 실행 비활성화. 외부 요청·POST·페이지 오류가 없었으며 상세/출처·수정·승인 확인 스크린샷도 직접 점검했다.

BE는 격리 SQLite 실 SQL/HTTP와 PostgreSQL SELECT 컴파일을 사용했다. 운영 DB·실계정에는 연결하지 않았다. 실제 PostgreSQL 드라이버/행 잠금/경합 검증은 수행하지 않았다. 브라우저는 loopback production build와 GET 전용 Mock을 사용하며, 실제 쓰기 E2E 결과가 아니다. 영향 없는 Consumer·Agent 및 기존 쓰기 트랜잭션 전체 테스트는 반복하지 않았다.

재현 명령:

```text
# backend: 기존 Python venv + 이 worktree의 .venv-deps, dummy DATABASE_URL 사용
python -m pytest tests/test_admin_content.py tests/test_admin_content_api.py tests/test_admin_review_queue.py tests/test_admin_content_input.py tests/test_admin_content_review.py::test_runtime_app_does_not_expose_review_even_with_allowing_overrides -q --tb=short
# frontend
node --test tests/admin-content.test.cjs tests/admin-content-input.test.cjs
npm run typecheck
npm run build -- --webpack
```

브라우저는 `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8197`로 빌드한 뒤 `npm run start -- --hostname 127.0.0.1 --port 3197`로 실행한다. 기존 Playwright를 `PIVOT_PLAYWRIGHT_MODULE`로 지정하고 `admin-content-browser-check.cjs`, `admin-content-input-browser-check.cjs`, `admin-review-actions-browser-check.cjs`를 실행한다. 결과는 ignored `.preview-pivot/admin-content-version-1113/{content,input,review}/`에 저장한다.

## 안전 조건과 한계

실제 관리자 read/write Provider 기본 거부, 운영 read/write Router 미등록, FE 승인·반려 실행 비활성화를 유지한다. Migration, PR #71 모델·권한·감사 정책, Consumer·Agent 코드는 변경하지 않았다. 운영 DB·실계정·배포·Push·PR 생성·Checking Merge는 수행하지 않는다.

**Content.updatedAt 제공만으로 Draft·Source·Carousel 전체 자료의 버전 정합성을 보장하지 않는다.** 관련 writer의 갱신·잠금 순서, 통합 버전 또는 snapshot 정책은 후속 합의가 필요하다. 실제 Provider/감사 DB 연결, 운영 Router 등록, FE 쓰기 연결 및 격리 PostgreSQL 검증은 기존 후속 과제로 남는다.

이 Slice의 구현·영향 검증에서 새 Blocker·Major는 발견하지 않았다. 로컬 커밋 대상은 아래 13개 파일이며 검증 산출물은 포함하지 않는다.

## 실제 변경 파일

- `backend/app/repositories/admin_content_repository.py`
- `backend/app/schemas/admin_content.py`
- `backend/tests/test_admin_content.py`
- `backend/tests/test_admin_content_api.py`
- `backend/tests/test_admin_content_input.py`
- `backend/tests/test_admin_review_queue.py`
- `frontend/lib/admin-content.ts`
- `frontend/tests/admin-content.test.cjs`
- `frontend/tests/admin-content-input.test.cjs`
- `frontend/tests/admin-content-browser-check.cjs`
- `frontend/tests/admin-content-input-browser-check.cjs`
- `frontend/tests/admin-review-actions-browser-check.cjs`
- `docs/ADMIN_P0_CONTENT_VERSION.md`
