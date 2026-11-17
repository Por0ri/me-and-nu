# ADMIN P0-1: 콘텐츠 조회 기반

Foundation 기준: Checking `1823b0444ae2737faad865768e1d6af354ac3309`, 2026-11-12.
권한·HTTP 기반 추가 기준: Checking `eb14ebf20effb8fcf5a4c90e61cd9dcc6a00f5ed`, 2026-11-13.

검수 대기함 추가 기준: Checking `ff4191414e44eb9bde6bfa61f6d9e63d8114134f`, 2026-11-13.
`mode=review`의 확정된 조회 기준·우선순위·검증 결과는 [검수 대기함 계약](ADMIN_P0_1_REVIEW_INBOX.md)을 따른다. 아래 기본 조회 계약은 `mode` 생략 또는 `mode=all`에 해당한다.

## 현재 연결 범위

- FE: `/admin` → `/admin/contents` → `/admin/contents/[contentId]`.
- BE: Content 조회 Repository/Service/Pydantic DTO 및 기본 정보·ContentSource 상세 조회.
- 관리자 데이터 조회 라우터와 서버 `require_admin` 경계를 구현했다. **기본 앱에는 라우터를 등록하지 않았다.** 실제 앱에서 아래 URL은 세션 유무와 관계없이 404이며 OpenAPI에도 없다. 권한 저장소와 운영 조회 범위가 확정되기 전 공개를 금지한다.
- FE는 기존 세션 조회와 `apiRequest`를 사용한다. 인증 여부는 로그인 안내에만 쓰며 `accountType`, `UserAccount.role`, 이메일, 요청 헤더로 관리자 권한을 판정하지 않는다.
- 정상 로그인 후 미등록 API의 404는 `조회 연결 준비 중`으로 표시한다. 401/403, 서버·네트워크 오류, 성공한 빈 결과, 상세 `CONTENT_NOT_FOUND`를 구분한다.
- Mock은 테스트의 메모리 DB·HTTP interception에만 존재한다. 서비스·Demo 화면에 관리자 가상 데이터 경로를 추가하지 않았다.

## HTTP 계약 (격리 테스트 앱에서만 등록)

목록: `GET /api/v1/admin/contents?q=&judgmentStatus=&status=&page=1&pageSize=20`

- `q`: 앞뒤 공백 제거, 최대 300자, 대소문자를 무시한 제목 부분 검색. `%`, `_`, `/`도 SQL 와일드카드가 아닌 문자로 검색.
- `judgmentStatus`: `pending | confirmed | needs_review | failed` 또는 생략.
- `status`: `active | hidden | pending_review | deleted` 또는 생략.
- 두 필터는 AND 조건. **기본 검수 대기 필터 없음.** 생략하면 삭제·테스트 콘텐츠를 포함해 해당 Content 테이블의 모든 상태를 조회하는 내부 기반 함수다. 운영 기본 범위는 팀 확정 후 권한 경계에서 적용할 것.
- `page`: 1..1,000,000, 기본 1. `pageSize`: 1..100, 기본 20.
- 정렬: `created_at DESC, content_id DESC`. `total`과 목록은 동일 필터를 사용한다. 페이지 번호 방식이며 동시 데이터 변경에 대한 스냅샷 고정은 제공하지 않는다.
- 응답: `{items: [{contentId, title, judgmentStatus, status, createdAt, publishedAt}], page, pageSize, total}`.
- ID는 기존 API와 같은 JSON 정수. FE는 안전한 정수 범위를 넘는 ID를 오류 처리하며 반올림한 ID로 상세를 요청하지 않는다.

상세: `GET /api/v1/admin/contents/{contentId}`. 행 필드에 `summary`, `body`, `sources` 추가. 출처 DTO는 기존 `ContentSourceResponse` 사용. 출처가 없어도 기본 정보는 반환한다. 내부 AI 판정 JSON·실행 정보·임베딩·이미지 바이트를 내보내지 않는다. 조회 외 조치와 미공개 이미지 API는 범위 밖이다.

## 서버 권한 경계

- `app/api/admin_deps.py`의 `require_admin`을 관리자 라우터 전체에 적용한다. 기존 HttpOnly 쿠키 스키마와 세션 Repository(토큰 해시·만료·회수, PostgreSQL/Redis 선택), 사용자 조회를 재사용한다. 개발용 인증 우회가 있는 `get_optional_auth_context`는 호출하지 않는다. 유효한 실제 active 세션과 active 사용자만 권한 조회 단계에 진입한다.
- `AdminPermissionProvider.can_read_contents(db, user_id=...)`는 매 요청 현재 서버 권한을 조회해야 한다. 권한 결과를 세션이나 프로세스에 캐시하지 않는다. `True`만 허용하고, 미부여·회수·불명확한 결과는 403으로 거부한다. 기본 Provider는 항상 거부하며 허용 구현은 격리 테스트 내부에만 존재한다.
- `admin_account`의 실제 Schema·Migration·업무용 계정 연결·권한 부여/회수 담당은 미확정이다. DB 모델, 관리자 계정, 이메일 허용 목록, 개발 백도어, 실행 환경의 활성화 플래그를 추가하지 않았다. 운영 등록에는 실제 Provider와 조회 범위 검토를 마친 후 명시적인 코드 변경이 필요하다.
- `admin_action_log` 연동은 조치 종류·기록 정책·Schema 확정 대기다. 이번 API는 GET 조회만 제공하며 승인·수정·발행 등의 조치와 로그 저장은 구현하지 않는다. 조회 감사 기록 필요 여부도 BE 확인 대상이다.
- 등록된 격리 앱의 응답: 비로그인/유효하지 않은 세션 401 `AUTH_REQUIRED`, 비관리자/권한 회수 403 `ADMIN_REQUIRED`, 인증·권한 검증 장애 503, 잘못된 query/path 422, 없는 콘텐츠 404 `CONTENT_NOT_FOUND`, 조회 장애 500. 권한 검사 전에 콘텐츠 Service를 호출하지 않으며 실패 응답에 데이터나 내부 오류 원문을 포함하지 않는다. 기존 공통 처리로 응답은 `private, no-store`다.
- FE 화면·공통 Client·조회 훅은 기존 구현을 그대로 사용한다. 조회 권한 검증 장애 503도 재시도 가능한 오류로 표시하며, 미등록 경로 404의 `조회 연결 준비 중`과 구분한다.

## 권한 연결 전 필수 결정

1. DB 등 신뢰 가능한 서버 측 관리자 권한 저장·부여·회수 정책. 모든 데이터 경로에 권한 의존성 적용, 로컬 인증 우회 거부, 권한 검증 오류 시 거부.
2. 검수 대기 기준: `Content.status=pending_review`인지 `judgment_status=needs_review`인지, Draft만 있는 항목을 포함할지.
3. 실제 운영/Checking 미리보기/삭제/만료 데이터 포함 범위.
4. Figma 단일 상태의 표시 규칙과 검수·발행 상태의 명칭. `active`를 게시 완료로 변환하지 않았다. `publishedAt`은 시각 자체만 표시한다.

격리 앱에서 비로그인 401·비관리자 403·허용 관리자 200·권한 회수/검증 장애 거부를 검증한다. 기본 앱의 미등록 라우트 테스트도 유지한다. 권한 DB 준비 없이 라우트만 연결하면 안 된다. 이번 작업은 migration·권한 부여·배포를 하지 않는다.

## 화면 범위

Figma `iLGLjporSKRHdHBdhsKEvV`, 목록 `2344:13128`의 로고·사이드바·색상·검색/표/페이지 형태를 재사용했다. 조회수 통계/차트, 분류·분야, 예약발행·신고 탭은 이번 P0-1 범위에 포함하지 않았다. 콘텐츠 ID와 두 상태 열을 구분하고 원본 enum 값을 표시한다. 나머지 사이드바 메뉴는 준비 중이며 링크가 아니다.

상세 Figma는 미확정으로, 기본 정보·본문·출처만 확인하는 진입 구조다. 검토/수정/승인/발행 UI를 임의로 만들지 않았다. 목록 query를 상세 URL에 유지해 복귀 시 검색·필터·페이지를 보존한다. 시간은 Asia/Seoul로 표시한다.

## 검증

- `backend/tests/test_admin_content.py`: 실제 SQL 실행(격리된 SQLite 메모리 테이블), PostgreSQL 방언의 바인딩/escape 확인, 필터·정렬·페이지·DTO·상세·실패 전파·API 미노출. 운영 PostgreSQL 데이터는 사용하지 않는다.
- `backend/tests/test_admin_content_api.py`: 격리 SQLite와 실제 세션/콘텐츠/출처 Repository를 사용하는 HTTP 통합 검사. 테스트 앱에만 라우터·허용 Provider를 연결한다. 실제 쿠키 토큰 해시 조회, 인증/권한/회수/장애, 개발 우회 거부, Redis 세션과 SQL fallback 차단, query/path 검증, 목록·상세·출처·오류 계약을 검증한다. Redis 통신은 메모리 응답으로 대체한다.
- `frontend/tests/admin-content.test.cjs`: 공통 Client, query/DTO 검증, 오류 상태, 안전한 출처 링크, 취소, 목록 렌더링.
- `frontend/tests/admin-content-browser-check.cjs`: 테스트 전용 HTTP 응답으로 검색·필터·페이지·상세 복귀·재시도·접근 상태·지연 응답, 1728/390px 확인. Playwright는 기존 설치를 `PIVOT_PLAYWRIGHT_MODULE`로 지정한다.
- FE 전체 단위 테스트와 기존 BE 인증/피드/상세/북마크 회귀 테스트를 함께 실행한다.
- 이 워크트리의 `node_modules` junction은 기본 Turbopack의 파일시스템 루트 제한에 걸린다. 검증용 프로덕션 빌드는 `npm run build -- --webpack`을 사용하며 저장소의 기본 빌드 설정은 변경하지 않았다.

Foundation 당시 실행 결과: 신규 BE 25개, 기존 BE 회귀 131개, FE 전체 단위 612개 통과. TypeScript 및 Webpack production build 통과. 관리자 1728/390px 브라우저 흐름과 Consumer HOME/SAVE/DETAIL/MY 기존 내비게이션 35개 검사(375px) 통과. Consumer 검사는 기존 fixture가 지정한 API origin `http://127.0.0.1:8195`와 로컬 opt-in 설정을 맞춰 실행했다. 브라우저 응답은 테스트 전용이며 실제 권한 부여 또는 운영 API 연결 성공을 뜻하지 않는다.

2026-11-13 권한·HTTP 기반 검증 결과:

- 신규 HTTP/권한 검사 40개와 기존 관리자 Foundation 검사 25개 통과. 세션·콘텐츠·출처 조회 SQL은 격리 SQLite에서 실행했으며, 실제 관리자 권한 저장소 및 운영 PostgreSQL 연동은 검증하지 않았다.
- 기존 BE 회귀 155개 통과: `test_auth_api.py`, `test_redis_auth_api.py`, `test_redis_session_store.py`, `test_dev_auth_bypass.py`, `test_v1_content_service.py`, `test_v1_bookmarks_api.py`, `test_public_agent_content_detail.py`, `test_recommended_feed.py`, `test_withdrawal.py`.
- FE `npm test` 615개, `npm run typecheck`, `npm run build -- --webpack` 통과.
- 기존 관리자 브라우저 검사 1728/390px 및 Consumer HOME/SAVE/DETAIL/MY 내비게이션 35개 검사(375px) 통과. 브라우저 검사는 테스트 HTTP 응답을 사용하며 새 BE와 운영 환경을 잇는 통합 검증은 아니다.
- 현재 운영 관리자 API는 여전히 미등록 404다. 허용 관리자 200은 격리된 테스트 앱에서만 검증했다. 운영 DB·배포 설정·관리자 계정·Migration은 변경하지 않았다.
