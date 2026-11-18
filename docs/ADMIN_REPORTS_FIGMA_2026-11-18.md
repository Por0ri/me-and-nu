# 관리자 신고 및 문의 관리 Figma 구현

`/admin/reports`에 신고·문의 목록, 기간·상태 필터, 상세 정보, 콘텐츠 검토 패널을 구현했다. 운영 화면은 관리자 조회 계약이 없어 미연결 상태이며, 실제 처리는 활성화하지 않았다.

기준은 fetch로 확인한 `origin/Checking` `601ff183af957d7ea4c28aeb17f38497473ef493`이다. 기존 `codex/admin-members-figma-1117` (`3ee7f87`)을 보존하고 `codex/admin-reports-figma-1118`을 생성했다. PR #78·#80의 관리자 기반이 포함된 상태에서 시작했다.

## 디자인과 구현 범위

| Figma | 구현 |
| --- | --- |
| [2344:14187 신고 관리](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV/me-nu?node-id=2344-14187) | 통계 4개, 상태 탭 4개, 검색, 5열 목록, 페이지네이션, 선택한 신고 상세, 검토 대기·처리 중·완료 배지 |
| [2611:9721 상세 검토 A](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV/me-nu?node-id=2611-9721) | 오른쪽 512px 콘텐츠 패널, 원본 이미지·제목·본문·출처, 닫기, 비활성 삭제·수정 버튼 |
| [2617:10387 상세 검토 B](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV/me-nu?node-id=2617-10387) | 완료된 서비스 문의 상세, 문의 내용, 완료 배지, 처리 버튼 미표시 |
| [2633:13025 기간 필터 변형](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV/me-nu?node-id=2633-13025) | 최근 7일·30일·3개월·6개월, 선택 강조, 열림·닫힘 상태 |

세 프레임의 최신 디자인 컨텍스트와 스크린샷을 확인했다. 프로토타입에서 `검토 시작`이 A 프레임으로 이동하는 것도 확인했다. 이 버튼은 콘텐츠 조회만 열며 상태를 변경하지 않는다. 본문이 없으면 비활성화한다. 서비스 문의의 목록 표기는 원본의 `서비스 신고`, 상세 표기는 원본의 `서비스 문의`를 유지했다.

검색은 신고 번호·콘텐츠 제목에 적용하고 Enter와 검색 버튼을 지원한다. 기간·상태 변경과 검색은 첫 페이지로 이동하며 서로 조합된다. 조건 변경으로 목록에서 사라진 상세를 남기지 않는다. 검색어는 메모리에서만 유지하며 URL·스토리지에 기록하지 않는다. 검색·필터의 데이터 선택 검증은 테스트 fixture에서 수행했다.

264px 사이드바, 84px 헤더, 브랜드, 검색 폼, 탭, 테이블 스크롤, 페이지네이션, 버튼·배지 스타일과 `AdminStatus`를 기존 관리자 구현에서 재사용했다. 공통 변경은 신고 메뉴 활성화·브레드크럼과 상태 컴포넌트의 신고·문의 문구 추가다. 백엔드·Consumer·권한·감사 DB·운영 Router·배포 설정은 변경하지 않았다.

390px에서는 통계와 목록·상세를 세로 배치하고 표 내부 가로 스크롤을 제공한다. 모바일 전용 Figma 프레임은 제공되지 않아 같은 요소를 반응형으로 배치했다. 본문 패널은 화면 너비에 맞추며 헤더와 스크롤 본문을 분리한다. 기간 메뉴의 방향키·Home·End·Esc, 행 선택 Enter/Space, 패널의 Tab/Shift+Tab·Esc·닫기와 포커스 복귀를 지원한다.

## API와 운영 경계

- 관리자 전용 신고·문의 GET·통계·처리 계약이 없다. 운영 화면의 수치는 `—`, 상태는 `조회 연결 준비 중`과 `집계 준비 중`이다. 성공한 빈 목록과 미연결을 구분한다.
- Consumer `POST /api/v1/reports`, `POST /api/v1/inquiries`를 호출하거나 관리자 계약으로 재사용하지 않는다. 신고 화면에는 네트워크 transport 자체가 없다.
- 보류·신고 처리·콘텐츠 삭제·수정·회원 제재·문의 답변·알림 전송은 실행하지 않는다. 검토 패널을 열고 닫아도 접수 상태가 바뀌지 않는다.
- #71 권한·감사 DB와 기본 거부 Permission Provider, 운영 관리자 Router 미등록을 유지한다.
- fixture는 `tests/fixtures`와 별도 ignored 복사본에만 있다. 운영 라우트에는 fixture 분기·환경 변수 스위치가 없다. `?fixture=figma`를 운영 화면에 붙여도 예시 데이터가 나타나지 않는다.
- 향후 연결에는 관리자 권한·조회 DTO·페이지/검색 계약·기간 집계 기준·처리 전이 및 감사 계약이 필요하다. 현재 프런트 타입은 화면 표현 모델이며 DB/API enum이 아니다. fixture의 기간 계산과 고정 시계는 운영 계약을 정의하지 않는다.

## 원본 에셋

`frontend/public/admin/report-sources.json`에 슬롯·노드·출처 URL·SHA-256·원본 크기를 기록했다. TLS 인증서 검증을 유지한 curl 다운로드로 비교했다.

- SVG 12개 중 11개는 기존 파일과 바이트 단위로 일치하여 재사용했다. 새 파일은 20×20 `reports-active.svg`이며 `-text` 속성으로 원본 바이트를 보존한다.
- 필터의 네 원본 화살표는 모두 기존 24×24 `sort-down.svg`와 일치했다. 열림 상태는 이미지 크기를 바꾸지 않고 래퍼를 회전한다.
- 1308×1632 PNG 원본은 `tests/fixtures/assets/report-cover.png`에 저장했다. 격리 복사본에서만 공개 경로로 복사하고, 패널은 데이터의 `coverUrl`을 327×408로 표시한다. 운영 public에 예시 이미지를 넣지 않는다.
- 모든 SVG의 실제 표시 크기를 브라우저에서 확인했다. 코드에는 임시 Figma URL을 런타임 이미지 경로로 사용하지 않는다.

## 검증 결과

| 검사 | 결과 |
| --- | --- |
| 관련 FE 테스트 | 41개 통과: 콘텐츠 27개, 회원 6개, 신고·문의 8개 |
| TypeScript | `npm run typecheck` 통과, 최종 Production Build의 TypeScript 검사도 통과 |
| Webpack Production Build | 운영 소스와 격리 fixture 복사본 모두 통과 |
| 신고·문의 브라우저 | 1728px·390px 모두 통과 |
| 콘텐츠 관리 회귀 | 1728px·390px 모두 통과 |
| 회원 관리 회귀 | 1728px·390px 모두 통과 |
| 운영 fixture 배제 | 실제 운영 빌드·정적 산출물에 신고 fixture 없음, 운영 화면 쿼리 우회 불가 |
| 요청 및 실행 | 신고·문의 API 호출 0, 쓰기 요청 0, 브라우저 페이지 오류 0 |
| 변경 검토 | 신규 Blocker·Major 없음 |

신고 검증은 세 프레임과 상태 배지, 기간 4종, 검색·상태·기간 교집합, 페이지 이동·초기화, 본문 누락, 서비스/버그 문의, loading/401/403/404/error/empty/unavailable 표시, 표 키보드 스크롤, 화면 가로 넘침, 원본 에셋 크기를 포함한다. 패널의 순방향·역방향 포커스 순환, 배경 스크롤 잠금, Esc/닫기와 열었던 버튼으로의 포커스 복귀를 확인했다.

검증 중 발견한 패널의 마지막 링크 이후 포커스 이탈은 경계 순환 처리로 수정했다. 최초 TypeScript 실행은 이전 `.next/dev`와 빌드 타입 참조가 충돌했으며, Next의 Production Build가 `next-env.d.ts`를 갱신한 뒤 통과했다. 테스트 코드의 Windows 경로 비교 오류는 정규화 후 실패한 검사 1개만 재실행했다. 통과한 무변경 공통 회귀 검사는 반복하지 않았다.

브라우저 검증은 로컬 Production Build와 격리 데이터에 대한 결과다. 운영 DB나 실제 관리자 API 연결 성공을 의미하지 않는다. 최종 스크린샷과 JSON 결과는 ignored `.preview-pivot/admin-reports-1118/{results,content-regression,member-regression}`에 있다. 패널은 viewport 캡처로, 일반 화면은 스크롤을 초기화한 전체 페이지 캡처로 확인했다.

## 변경 파일

- `frontend/app/admin/reports/page.tsx`: 운영 미연결 진입점.
- `frontend/components/admin/reports-screen.tsx`, `report-period-filter.tsx`, `report-detail-panel.tsx`, `reports.module.css`: 목록·통계·필터·상세·패널과 반응형 스타일.
- `frontend/components/admin/admin-navigation.tsx`, `admin-status.tsx`: 공통 메뉴·브레드크럼·조회 상태 재사용.
- `frontend/lib/admin-reports.ts`: 화면 모델·문구·날짜·출처 URL 처리.
- `frontend/next-env.d.ts`: Next가 생성한 빌드 route type 참조.
- `frontend/public/admin/reports-active.svg`, `report-sources.json`, `.gitattributes`: 원본 에셋·출처·바이트 보존.
- `frontend/tests/admin-reports.test.cjs`, `admin-reports-browser-check.cjs`, `prepare-admin-reports-preview.cjs`: 단위·브라우저·격리 복사본 검사.
- `frontend/tests/fixtures/admin-reports.ts`, `admin-reports-page.tsx`, `assets/report-cover.png`: 테스트 전용 샘플·페이지·원본 이미지.
- `docs/ADMIN_REPORTS_FIGMA_2026-11-18.md`: 구현과 검증 기록.

재현은 `node tests/prepare-admin-reports-preview.cjs`로 격리 복사본을 준비한 뒤, 운영 소스와 복사본에서 각각 `npm run build -- --webpack`을 사용한다. 검증 서버는 loopback 3198(운영 미연결)과 3197(fixture)을 사용했다. 브라우저 스크립트는 기존 Playwright 설치 경로를 `PIVOT_PLAYWRIGHT_MODULE`로 지정해 실행한다. 새 의존성은 추가하지 않았다. Push·PR·Merge·수동 배포는 수행하지 않는다.
