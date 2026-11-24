# 관리자 콘텐츠 관리 — 2026-11-16 Figma 반영

기준: `origin/Checking` `80dc426a0659ca7acb18c31b33fb345022040e41`.
브랜치: `codex/admin-content-figma-1116`.
기존 `codex/admin-p0-content-version-1113` 및 커밋은 보존했다. 시작 시 미커밋 변경은 없었다.

디자인: [콘텐츠 관리 2344:13128](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV/me-nu?node-id=2344-13128)의 최신 디자인 컨텍스트·스크린샷·하위 스타일·에셋을 기준으로 구현했다.
기능은 기존 `ADMIN_P0_1_FOUNDATION.md`, `ADMIN_P0_1_REVIEW_INBOX.md`, `ADMIN_P0_CONTENT_VERSION.md`와 현재 관리자 DTO를 유지한다. `ADMIN_DASHBOARD_V1_SCREEN_SPEC.md`는 제안 문서이며 확정 계약을 대체하지 않는다.

## 구현한 화면

- 공통 레이아웃: 264px 사이드바, 84px 헤더, 로고·ADMIN·분야 배지, 내비게이션 문구·간격·배경, 32px 제목과 안내 문구.
- 콘텐츠 총 조회수 / 콘텐츠 상태 / 콘텐츠 유형 분포: 3개 카드의 252px 높이·도넛 영역·범례·트랙·테두리. 데이터가 없으므로 모든 수치는 `—`, 카드에는 `집계 준비 중`을 표시한다. 채워진 도넛 조각·막대 길이는 표본 운영 데이터이므로 구현 데이터로 복사하지 않았다.
- 전체 콘텐츠 / 게시 중 / 검수 대기 / 예약 발행 / 신고 접수: Figma 밑줄 탭과 색상·간격. 전체/검수 대기는 기존 API 모드를 사용하고 나머지 3개는 비활성화 및 준비 중 설명을 제공한다.
- 검색 입력과 아이콘 제출, Enter 검색, 기존 검수·콘텐츠 상태/페이지 크기 필터. 상세 필터는 접어서 배치하며 적용된 필터가 있으면 자동으로 펼친다. 기존 등록 화면 진입 링크도 유지한다.
- 목록: Figma 순서의 콘텐츠·분류·분야·등록일·상태·조회수 열과 기존 ID. 제목 상세 링크, 실제 서버 `total`, 한국 시간 날짜, 검수 대기 보라색 배지. 별개인 원본 검수·콘텐츠 상태를 보존한다.
- 숫자 페이지 버튼·이전/다음·생략 구간·첫/마지막 페이지. 서버 페이지·total 기준이며 현재 페이지 데이터로 통계를 추산하지 않는다.
- 390px: 통계 카드 세로 배치, 탭/표 내부 가로 스크롤, 키보드로 표 스크롤, 페이지 전체 가로 넘침 방지. 제공된 노드는 데스크톱 프레임이므로 모바일은 같은 요소·색상·타이포그래피의 반응형 배치다.

기존 검색·상태 필터·검수 대기함 정렬·페이지·상세/목록 복귀·뒤로가기·새로고침의 URL 의미는 바꾸지 않았다. 리스트를 브라우저에서 재정렬하거나 필터링하지 않는다.

## API 미연결 및 팀 결정 필요

다음은 **후속 계약 제안**이며 새 API나 응답 필드를 이번 코드에 추가하지 않았다.

| 항목 | 현재 화면 | 연결에 필요한 계약/결정 |
| --- | --- | --- |
| 총 조회수·분야별 조회수 | 빈 도넛/트랙, `—` | 조회 이벤트 정의, 중복/테스트/삭제 포함 범위, 집계 기간·기준 시각, 총합·분야별 수치, 집계 중/실패/미연결 구분 |
| 콘텐츠 상태 통계 | 상태 4개 범례와 `—` | 각 상태의 서버 기준·분모·겹침 처리. 신고는 게시와 겹칠 수 있으므로 단일 비율로 만들기 전 우선순위 합의 필요 |
| 유형별 분포 | Figma 범례·색상, `—` | 실제 분류 코드/표시명, 게시 중인 콘텐츠 총수와 유형별 수치, 기타 분류 기준 |
| 게시 중 탭/배지 | 준비 중 비활성화 | 실제 공개 판정을 서버가 제공하는 목록 필터/상태. `active`, `confirmed`, 과거 `publishedAt`만으로 공개로 판정하지 않음 |
| 예약 발행 탭 | 준비 중 비활성화 | 예약 상태/예약 시각/취소·실패 상태 및 검색·페이지·total 계약. 미래 `publishedAt`을 예약으로 임의 변환하지 않음 |
| 신고 접수 탭 | 준비 중 비활성화 | 미처리 신고의 범위, 콘텐츠 단위 중복 제거, 신고 수·상태, 검색·페이지·total 계약 |
| 행 분류·분야·조회수 | 각 셀 `—`, 연결 전 접근성 문구 | 목록 DTO에 분류·분야의 식별자/표시명 및 조회수 제공. 전역 운영 분야 배지를 개별 행 데이터로 간주하지 않음 |
| 단일 상태 배지 | 검수 상태와 원본 콘텐츠 상태 병기 | 두 상태축과 실제 게시/예약/신고를 하나의 배지로 표현할 우선순위. `needs_review`만 확정된 검수 대기 명칭 사용 |
| 상태 차트 중앙 문구 | 최신 Figma의 `총 조회수` 유지 | 상태별 비율의 분모가 조회수인지 콘텐츠 수인지 확인 필요 |

집계 연결 전 표시를 0으로 바꾸거나 Figma 예시 `11,578`, `1,247`, 비율을 제품 코드에 넣지 않았다. API의 `total`만 조회 성공 시 목록 건수로 표시한다. 모든 테스트 데이터는 테스트 스크립트 안에만 있으며 서비스 Mock/Demo 경로는 추가하지 않았다.

## 유지한 경계

- 관리자 `updatedAt` 원문·마이크로초 계약, 목록/상세 DTO 및 공통 API Client 무변경.
- Permission Provider 기본 거부, 운영 관리자 Router 미등록, 승인·반려 POST 및 저장 실행 비활성화 유지.
- #71 DB·권한 정책, Consumer, Agent, Worker, 운영 데이터·배포 설정 무변경.
- 기존 인증/권한 오류와 성공한 빈 결과를 구분한다. 준비 중 화면을 성공 데이터로 처리하지 않는다.
- Push·PR·Merge·배포 없이 변경 파일만 로컬 커밋한다.

## 검증

- `node --test tests/admin-content.test.cjs tests/admin-content-input.test.cjs`: 영향받는 기존 FE 25개 통과.
- 새 테스트만 `node --test --test-name-pattern=Figma tests/admin-content.test.cjs`: 허위 통계/게시 판정 방지와 페이지 수 검사 2개 통과. 총 27개.
- `npm run typecheck` 통과.
- `npm run build -- --webpack` 통과. 기존 node_modules junction을 고려해 이전과 같은 Webpack 검증 방식을 사용했다. 모바일 CSS 수정 후 빌드도 통과.
- `admin-content-figma-browser-check.cjs`: 로컬 Production Build + 격리 HTTP fixture로 **1728px·390px 모두 통과**. 검색/Enter, 필터 교집합, 검수 대기 우선 표시, 페이지/페이지 크기, 상세 복귀/뒤로가기/새로고침 URL 보존, 빈 결과와 403/미등록 404, GET 전용, 에셋 크기, 표 키보드 스크롤, 가로 넘침을 확인했다. 최종 스크린샷도 확인했다.
- 최신 다운로드와 기존 10개 로고·아이콘 SVG의 SHA-256이 동일하여 재사용. 유형 범례 4개 SVG만 추가. 표시되는 14개 정적 에셋의 decode·원본/렌더 크기를 확인한다. Figma 임시 URL은 출처 manifest에만 있으며 런타임은 로컬 파일을 사용한다.
- 모바일 검사에서 표 내부의 숨김 상태 텍스트가 페이지 너비를 늘리는 문제를 발견해 표에 위치 기준을 추가했다. 데스크톱 표 열 너비·55px 행 높이도 보정했다.
- 관련 BE 코드를 수정하지 않았으므로 기존 BE·Consumer 전수 검사를 반복하지 않았다. 브라우저 데이터는 운영 API 연동 성공의 근거가 아니다.

브라우저 결과와 스크린샷은 Git 제외 경로 `.preview-pivot/admin-content-figma-1116/`에 저장한다. `results.json`, `contents-1728.png`, `contents-390.png`, `review-filters-1728.png`, `review-filters-390.png`, `unavailable-1728.png`, `unavailable-390.png`.

이번 UI 구현 범위의 미해결 Blocker·Major는 없다. 실제 운영 사용은 기존의 권한 Provider/Router 미연결 제한이 유지되며, 위의 새 집계·상태 계약을 별도로 확정해야 한다.

## 변경 파일

- `frontend/app/admin/layout.tsx`
- `frontend/components/admin/admin.module.css`
- `frontend/components/admin/contents-screen.tsx`
- `frontend/components/admin/content-statistics.tsx`
- `frontend/public/admin/sources.json`
- `frontend/public/admin/type-ost.svg`, `type-event.svg`, `type-character.svg`, `type-other.svg`
- `frontend/tests/admin-content.test.cjs`
- `frontend/tests/admin-content-browser-check.cjs` (접힌 필터를 여는 기존 검사 진입 보완)
- `frontend/tests/admin-content-figma-browser-check.cjs`
- `docs/ADMIN_CONTENT_FIGMA_2026-11-16.md`

다음 화면 한 개: **콘텐츠 상세·검수 화면**. 기존 상세/출처/검수 확인 흐름을 최신 확정 Figma에 맞추되 실제 승인·반려 연결은 별도 권한·운영 계약을 따른다.

팀 공유: “11/16 콘텐츠 관리 최신 Figma UI 반영, 기존 조회·검수·URL 유지; 통계·게시 판정·예약·신고는 계약 확정 대기이며 운영 권한/POST는 계속 비활성화입니다.”
