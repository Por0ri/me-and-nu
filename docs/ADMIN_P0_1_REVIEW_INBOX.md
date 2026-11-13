# ADMIN P0-1: 읽기 전용 콘텐츠 검수 대기함

기준: Checking `ff4191414e44eb9bde6bfa61f6d9e63d8114134f`, 2026-11-13.
로컬 브랜치: `codex/admin-p0-1-review-inbox-1113`.

## 조회 계약

기존 `GET /api/v1/admin/contents`에 `mode=all|review` 하나만 추가한다. 기본은 `all`이며 FE는 기존 URL 호환을 위해 기본 모드를 생략한다. 잘못된 모드는 FE에서 조회 조건 오류, 서버에서 권한 검증 후 422로 처리한다. 응답 DTO와 상세 API, Service는 그대로 재사용한다.

| 모드 | 서버 대상 | 서버 정렬 |
| --- | --- | --- |
| 전체 콘텐츠 (`all`, 생략) | 기존 Content 전체, 기존 검색·상태 필터 | `created_at DESC, content_id DESC` |
| 검수 대기 (`review`) | `Content.judgment_status = needs_review`와 기존 필터의 교집합 | `active` → `pending_review` → 그 외, 각 그룹에서 `created_at DESC, content_id DESC` |

- `q`, `judgmentStatus`, `status`, `page`, `pageSize`는 기존 의미와 범위를 유지한다. 검색은 제목 부분 검색이며 `%`, `_`, `/`는 문자 그대로 처리한다.
- 검수 대기에서 `confirmed`, `failed`, `pending`을 검수 상태 필터로 선택하면 교집합이 없어 빈 목록·`total=0`이다. 필터를 무시하거나 자동으로 바꾸지 않는다.
- `status=pending_review`만으로 검수 대상으로 판단하지 않는다. `confirmed/failed/pending + pending_review`는 대기함에서 제외한다.
- `hidden/deleted + needs_review`는 검수 대상에서 누락하지 않고 마지막 그룹에 배치하며 `상태 확인 필요`를 표시한다.
- `COUNT`와 페이지 조회는 동일 조건을 사용한다. 우선순위는 SQL `ORDER BY CASE`에 적용하므로 OFFSET/LIMIT 전 전체 결과에 적용된다. FE는 응답을 다시 정렬하거나 걸러내지 않는다. 검수 모드 응답에 다른 judgment가 포함되면 계약 오류로 표시한다.
- Draft는 조회·JOIN하지 않는다. 기존 `carousel_contents` 적재기는 호출자가 소유한 한 트랜잭션에서 Draft와 Content를 생성한다. Draft 단독은 이 목록에 포함하지 않는다. Celery/PR #68·#71 코드는 변경하지 않는다.
- 페이지 번호 방식은 기존대로 유지한다. 동시 적재·수정 사이의 페이지 스냅샷 고정은 제공하지 않는다.

## 화면과 목록 복원

- `/admin/contents`에 `전체 콘텐츠`, `검수 대기` 버튼을 추가하고 현재 선택을 `aria-pressed`로 표시한다. 기존 관리자 스타일을 재사용하며 Figma 확정 디자인으로 간주하지 않는다.
- 모드 전환은 검색·검수 상태·콘텐츠 상태·페이지 크기를 보존하고 페이지 번호만 1로 초기화한다. 기존 검색/필터/페이지 크기 변경도 1페이지부터 조회한다.
- URL에 선택 모드와 모든 적용 조회 조건을 저장한다. 상세 링크, 상세의 목록 링크, 브라우저 뒤로가기, 새로고침에서 복원한다. 상세 컴포넌트의 기존 공통 query 직렬화를 그대로 사용한다.
- `active + needs_review`는 `우선 확인`, `pending_review + needs_review`는 `일반 비공개 대기`다. 저장된 검수 상태와 콘텐츠 상태는 각각 표시한다.
- `confirmed/failed`는 기존 저장 enum이다. 이번 작업은 사람의 승인·반려 실행이나 이력을 만들지 않으며 UI에서 인간 검수 완료로 재해석하지 않는다.

## active와 실제 노출의 차이

Checking의 `carousel_repository.public_content_condition()`과 `_carousel_review_condition()`, `content_repository.list_public_contents()`, `feed_service.content_notices()`를 확인했다.

- 공통 공개 조건은 active 외에도 deleted_at, feed_valid_until, published_at을 확인한다. 미래 발행 시각은 공개 조건에서 제외된다.
- 수입 캐러셀은 검수·preview 플래그, 환경/DB가 일치하는 Checking 또는 로컬 preview 메타데이터, `quality_approval=not_reviewed`, 슬라이드 2..10장과 연속성, 유효 기한·출처 조건도 적용한다.
- 실제 피드는 주제/출처 및 기존 피드 조건을 추가 적용한다. 레거시 콘텐츠와 캐러셀의 공개 조건도 다르다.
- 관리자 DTO에는 이 모든 근거가 없으므로 `공개 중`, `미리보기 중` 같은 파생 상태를 만들지 않는다. 발행 시각을 별도 필드로 읽는 기존 상세는 유지한다. Consumer 공개 판정·피드 코드는 변경하지 않는다.

## 검증

- BE: `test_admin_review_queue.py`, 기존 `test_admin_content.py`, `test_admin_content_api.py`, `test_admin_content_input.py` — **157 passed**.
- 새 BE 검증은 동일 시간 ID 동률, 더 오래된 active 우선, 일반 대기, confirmed/failed, 상태 불일치, 미래 발행 active, Draft 단독 제외, 검색/와일드카드, 필터 교집합, 모든 페이지·total, PostgreSQL SQL 컴파일 및 권한 뒤 검증을 포함한다. 실제 읽기 SQL은 격리 SQLite에서 실행한다.
- FE: 관리자 조회·입력 단위 테스트 **20 passed**, `npm run typecheck`, `npm run build -- --webpack` 통과. node_modules junction을 고려한 기존 빌드 방식을 사용했다.
- 브라우저: `frontend/tests/admin-review-browser-check.cjs`는 `backend/tests/serve_admin_review_fixture.py`의 메모리 DB·실제 HTTP 세션/API/Service/Repository에 연결한다. 허용 권한 Provider와 라우터 등록은 테스트 앱에만 존재한다. 운영 DB 또는 관리자 API 등록이 아니다.
- **1728px·390px 모두 통과**: 전체/검수 모드, 우선순위 전체 5페이지, 검색/와일드카드, 교집합 상태 필터, 정확한 total, 페이지 크기, 범위 밖 페이지 복구, 상세 목록 링크·브라우저 뒤로가기·새로고침 복원, GET 요청만 발생, 가로 넘침·공개 상태 오표시 없음. 스크린샷도 확인했다. 결과는 ignored `.preview-pivot/admin-review-1113/results.json`과 `review-1728.png`, `review-390.png`에 있다.

재현 환경: 기존 Python venv와 worktree의 `.venv-deps`, `DATABASE_URL=postgresql+psycopg://test:test@127.0.0.1:1/admin_review_test`를 사용한다. DB 의존성은 모두 메모리 SQLite로 override하므로 해당 주소에 연결하지 않는다. 테스트 서버는 `127.0.0.1:8197`, FE production preview는 `127.0.0.1:3197`, `FRONTEND_ORIGINS=http://127.0.0.1:3197`, 빌드 시 `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8197`을 사용한다. 브라우저 도구는 기존 Playwright 설치를 `PIVOT_PLAYWRIGHT_MODULE`로 지정한다.

## 운영 사용 제한 및 심각도

- **Blocker (운영 연결)**: 기본 앱에 관리자 라우터를 등록하지 않았고 기본 Permission Provider는 계속 거부한다. 운영 권한 저장소/서버 권한 부여와 별도 운영 연결 승인·등록 전 실제 사용은 불가능하다. 이 제한은 의도적으로 유지했다.
- **Major**: 구현 범위 내 미해결 Major는 없음. 운영 PostgreSQL 실데이터·권한 Provider 검증은 이번 작업에서 수행하지 않았다.
- 승인·반려·숨김 API, 쓰기 작업, Migration, 운영 DB 변경은 없다. 기존 콘텐츠 등록/수정 입력 기반의 저장 비활성화도 그대로다.
- 로컬 커밋까지만 수행한다. Push·PR·Merge·배포 및 타 팀 PR 수정은 하지 않는다.

## 변경 파일

- BE: `app/schemas/admin_content.py`, `app/repositories/admin_content_repository.py`, `app/api/v1/admin/router.py`.
- FE: `lib/admin-content.ts`, `components/admin/contents-screen.tsx`, `components/admin/admin.module.css`.
- 테스트: `backend/tests/test_admin_review_queue.py`, `backend/tests/serve_admin_review_fixture.py`, `frontend/tests/admin-content.test.cjs`, `frontend/tests/admin-review-browser-check.cjs`.
- 문서: `docs/ADMIN_P0_1_FOUNDATION.md`, `docs/ADMIN_P0_1_REVIEW_INBOX.md`.
