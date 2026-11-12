# ADMIN P0-1: 콘텐츠 조회 기반

기준: Checking `1823b0444ae2737faad865768e1d6af354ac3309`, 2026-11-12.

## 현재 연결 범위

- FE: `/admin` → `/admin/contents` → `/admin/contents/[contentId]`.
- BE: Content 조회 Repository/Service/Pydantic DTO 및 기본 정보·ContentSource 상세 조회.
- **관리자 데이터 API 라우터는 만들거나 등록하지 않았다.** 실제 앱에서 아래 후보 URL은 세션 유무와 관계없이 404이며 운영 데이터에 접근하지 않는다. 서버 권한 체계가 결정되기 전 공개를 금지한다.
- FE는 기존 세션 조회와 `apiRequest`를 사용한다. 인증 여부는 로그인 안내에만 쓰며 `accountType`, `UserAccount.role`, 이메일, 요청 헤더로 관리자 권한을 판정하지 않는다.
- 정상 로그인 후 미등록 API의 404는 `조회 연결 준비 중`으로 표시한다. 401/403, 서버·네트워크 오류, 성공한 빈 결과, 상세 `CONTENT_NOT_FOUND`를 구분한다.
- Mock은 테스트의 메모리 DB·HTTP interception에만 존재한다. 서비스·Demo 화면에 관리자 가상 데이터 경로를 추가하지 않았다.

## 제안 계약 (아직 HTTP API 아님)

목록: `GET /api/v1/admin/contents?q=&judgmentStatus=&status=&page=1&pageSize=20`

- `q`: 앞뒤 공백 제거, 최대 300자, 대소문자를 무시한 제목 부분 검색. `%`, `_`, `/`도 SQL 와일드카드가 아닌 문자로 검색.
- `judgmentStatus`: `pending | confirmed | needs_review | failed` 또는 생략.
- `status`: `active | hidden | pending_review | deleted` 또는 생략.
- 두 필터는 AND 조건. **기본 검수 대기 필터 없음.** 생략하면 삭제·테스트 콘텐츠를 포함해 해당 Content 테이블의 모든 상태를 조회하는 내부 기반 함수다. 운영 기본 범위는 팀 확정 후 권한 경계에서 적용할 것.
- `page`: 1..1,000,000, 기본 1. `pageSize`: 1..100, 기본 20.
- 정렬: `created_at DESC, content_id DESC`. `total`과 목록은 동일 필터를 사용한다. 페이지 번호 방식이며 동시 데이터 변경에 대한 스냅샷 고정은 제공하지 않는다.
- 응답: `{items: [{contentId, title, judgmentStatus, status, createdAt, publishedAt}], page, pageSize, total}`.
- ID는 기존 API와 같은 JSON 정수. FE는 안전한 정수 범위를 넘는 ID를 오류 처리하며 반올림한 ID로 상세를 요청하지 않는다.

상세: `GET /api/v1/admin/contents/{contentId}` 후보. 행 필드에 `summary`, `body`, `sources` 추가. 출처 DTO는 기존 `ContentSourceResponse` 사용. 출처가 없어도 기본 정보는 반환한다. 내부 AI 판정 JSON·실행 정보·임베딩·이미지 바이트를 내보내지 않는다. 조회 외 조치와 미공개 이미지 API는 범위 밖이다.

## 권한 연결 전 필수 결정

1. DB 등 신뢰 가능한 서버 측 관리자 권한 저장·부여·회수 정책. 모든 데이터 경로에 권한 의존성 적용, 로컬 인증 우회 거부, 권한 검증 오류 시 거부.
2. 검수 대기 기준: `Content.status=pending_review`인지 `judgment_status=needs_review`인지, Draft만 있는 항목을 포함할지.
3. 실제 운영/Checking 미리보기/삭제/만료 데이터 포함 범위.
4. Figma 단일 상태의 표시 규칙과 검수·발행 상태의 명칭. `active`를 게시 완료로 변환하지 않았다. `publishedAt`은 시각 자체만 표시한다.

권한 연결 시 비로그인 401·비관리자 403·허용 관리자 200·권한 회수/검증 장애 거부 테스트를 추가하고, 현재 미등록 라우트 테스트를 교체해야 한다. 권한 DB 준비 없이 라우트만 연결하면 안 된다. 이번 작업은 migration·권한 부여·배포를 하지 않는다.

## 화면 범위

Figma `iLGLjporSKRHdHBdhsKEvV`, 목록 `2344:13128`의 로고·사이드바·색상·검색/표/페이지 형태를 재사용했다. 조회수 통계/차트, 분류·분야, 예약발행·신고 탭은 이번 P0-1 범위에 포함하지 않았다. 콘텐츠 ID와 두 상태 열을 구분하고 원본 enum 값을 표시한다. 나머지 사이드바 메뉴는 준비 중이며 링크가 아니다.

상세 Figma는 미확정으로, 기본 정보·본문·출처만 확인하는 진입 구조다. 검토/수정/승인/발행 UI를 임의로 만들지 않았다. 목록 query를 상세 URL에 유지해 복귀 시 검색·필터·페이지를 보존한다. 시간은 Asia/Seoul로 표시한다.

## 검증

- `backend/tests/test_admin_content.py`: 실제 SQL 실행(격리된 SQLite 메모리 테이블), PostgreSQL 방언의 바인딩/escape 확인, 필터·정렬·페이지·DTO·상세·실패 전파·API 미노출. 운영 PostgreSQL 데이터는 사용하지 않는다.
- `frontend/tests/admin-content.test.cjs`: 공통 Client, query/DTO 검증, 오류 상태, 안전한 출처 링크, 취소, 목록 렌더링.
- `frontend/tests/admin-content-browser-check.cjs`: 테스트 전용 HTTP 응답으로 검색·필터·페이지·상세 복귀·재시도·접근 상태·지연 응답, 1728/390px 확인. Playwright는 기존 설치를 `PIVOT_PLAYWRIGHT_MODULE`로 지정한다.
- FE 전체 단위 테스트와 기존 BE 인증/피드/상세/북마크 회귀 테스트를 함께 실행한다.
- 이 워크트리의 `node_modules` junction은 기본 Turbopack의 파일시스템 루트 제한에 걸린다. 검증용 프로덕션 빌드는 `npm run build -- --webpack`을 사용하며 저장소의 기본 빌드 설정은 변경하지 않았다.

실행 결과: 신규 BE 25개, 기존 BE 회귀 131개, FE 전체 단위 612개 통과. TypeScript 및 Webpack production build 통과. 관리자 1728/390px 브라우저 흐름과 Consumer HOME/SAVE/DETAIL/MY 기존 내비게이션 35개 검사(375px) 통과. Consumer 검사는 기존 fixture가 지정한 API origin `http://127.0.0.1:8195`와 로컬 opt-in 설정을 맞춰 실행했다. 브라우저 응답은 테스트 전용이며 실제 권한 부여 또는 운영 API 연결 성공을 뜻하지 않는다.
