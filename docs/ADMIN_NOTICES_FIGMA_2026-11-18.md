# 관리자 알림 및 공지 Figma 구현

`/admin/notices` 하나에서 목록·작성·예약 입력·공지 상세 상태를 제공한다. 관리자 조회·쓰기 계약이 없어 운영 화면은 미연결이며, 실제 데이터 조회·저장·발행·예약·수정·삭제·알림 발송은 활성화하지 않았다.

## 기준과 디자인

clean 작업 트리에서 `git fetch origin Checking`으로 최신 `66494e1406e7be20ae45df2f47f5b130a7e9ba7b`를 확인한 뒤 `codex/admin-notifications-figma-1118`을 생성했다. PR #78·#80·#85가 포함된 기준점이다.

| Figma 프레임 | 구현 상태 |
| --- | --- |
| [2344:14578](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV/me-nu?node-id=2344-14578) | 전체·게시 중·예약 통계, 상태 탭, 제목 검색, 7행 목록·페이지네이션, 새 공지 작성 |
| [2633:11960](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV/me-nu?node-id=2633-11960) | 예약 스위치 on, 날짜·시간 입력, 예약 하기 버튼 |
| [2633:11568](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV/me-nu?node-id=2633-11568) | 선택한 공지의 제목·본문·게시 일시를 보여주는 오른쪽 512px 상세 패널 |

세 프레임 모두 최신 고정밀 디자인 컨텍스트와 스크린샷을 확인했다. 프레임·하위 노드의 prototype reactions는 모두 비어 있었다. 동일한 1728×1117 화면의 상태 변형으로 판단해 별도 라우트나 임의 확인 팝업을 추가하지 않았다. 예약 스위치와 상세 버튼이 해당 화면 상태를 전환한다.

기존 관리자 레이아웃·메뉴·브레드크럼, 탭·검색·테이블·페이지네이션·상태 메시지, 신고 화면의 통계 카드·패널·버튼·상세 drawer 스타일을 재사용했다. 공통 수정은 알림·공지 메뉴 활성화와 `AdminStatus`의 알림·공지 문구 추가다. 기존 공통 CSS와 기존 화면의 기능 코드는 변경하지 않았다.

## 상호작용과 운영 경계

- 제목 검색은 Enter/검색 버튼을 지원한다. 상태 필터·검색·페이지 이동은 조합되며 조건 변경 시 첫 페이지로 돌아간다. 선택했던 상세가 다른 필터에 남지 않는다.
- 제목/상세 버튼으로 패널을 열고 닫기/Escape로 닫는다. 배경 스크롤 잠금, Tab/Shift+Tab 포커스 순환, 열었던 버튼으로 포커스 복귀를 지원한다. 본문이 없으면 미연결 문구로 표시한다.
- 제목·본문·예약 스위치·날짜·시간은 현재 페이지의 메모리에서만 편집된다. 저장 성공·예약 성공을 흉내 내지 않는다. 작성 내용은 새로고침·이동 시 사라지며 저장되지 않는다는 문구를 표시한다. 검색어·초안은 URL 또는 브라우저 스토리지에 기록하지 않는다.
- 날짜·시간은 Figma 원본 아이콘을 가진 필드와 브라우저 기본 date/time 입력으로 제공한다. 한국 시간 표기를 사용한다. 운영 화면에는 샘플 날짜·시간을 미리 채우지 않는다.
- 운영 통계·건수는 `—`, 조회는 `조회 연결 준비 중`이다. 성공한 빈 목록과 미연결/로딩/로그인/권한/실패 상태를 구분한다.
- 임시 저장·게시하기·예약 하기·수정하기·삭제하기는 항상 비활성화한다. 관리자 알림·공지 transport나 API를 만들지 않는다. Consumer 알림 조회 API를 관리자 계약으로 재사용하지 않는다.
- #71 권한·감사 DB, 기본 거부 Permission Provider 및 운영 관리자 Router 미등록 상태를 유지한다. Backend·Consumer·Agent·Worker·DB Migration은 변경하지 않는다.
- fixture는 `frontend/tests/fixtures`와 ignored `.preview-pivot/admin-notices-1118/frontend` 복사본에만 연결한다. 운영 라우트에 fixture 쿼리 분기·환경 변수 우회가 없다. 프런트 타입은 화면 표현 모델이며 BE/DB 계약이 아니다.

390px에서는 기존 모바일 관리자 메뉴를 재사용하고 목록과 작성 패널을 세로 배치한다. 표만 내부 가로 스크롤을 허용한다. 상세 패널은 화면 너비에 맞추고 본문 영역을 스크롤한다. 모바일 전용 Figma가 없어 동일 요소를 반응형 배치했다.

## 원본 에셋

`frontend/public/admin/notice-sources.json`은 원본 노드·프레임·다운로드 URL·SHA-256·root 크기를 기록한다. 인증서 검증을 유지한 curl 다운로드로 기존 파일과 비교했다. SVG 17개 중 11개는 기존 파일과 바이트가 같아 재사용했고, 다음 6개를 추가했다.

- `notices-active.svg`: 20×20 활성 메뉴 아이콘.
- `notice-forward.svg`: 15×24 목록 상세 화살표.
- `notice-switch-off.svg`, `notice-switch-on.svg`: 44×25.9487 예약 스위치.
- `notice-calendar.svg`, `notice-clock.svg`: 24×24 날짜·시간 아이콘.

신규 SVG는 `.gitattributes`의 `-text`로 원본 바이트를 보존한다. 런타임에 임시 Figma URL을 사용하지 않는다.

## 검증

| 검사 | 결과 |
| --- | --- |
| 관련 FE 테스트 | 49개 통과: 콘텐츠 27, 회원 6, 신고·문의 8, 알림·공지 8 |
| TypeScript | `npm run typecheck` 통과, 최종 두 Production Build의 TypeScript 검사 통과 |
| Webpack Production Build | 운영 소스·격리 fixture 복사본 모두 통과 |
| 알림·공지 브라우저 | 1728px·390px 모두 통과, 세 화면 상태·필터·검색·페이지 이동·예약 입력·상세 패널 검증 |
| 공통 화면 회귀 | 콘텐츠·회원·신고 관리 모두 1728px·390px 통과 |
| 원본 에셋 | 17개 파일의 SHA-256/root 크기와 실제 표시 크기 통과 |
| 운영 fixture 배제 | 운영 라우트 import graph, 빌드 chunks·server 산출물·trace, 쿼리 우회 검사 통과 |
| 요청·오류 | 알림·공지 API 호출 0, 쓰기 요청 0, 페이지 오류 0 |
| 범위·검토 | Backend·Consumer·Agent·Worker·DB Migration 변경 없음, 신규 Blocker·Major 없음 |

검증 중 390px에서 스크린리더용 마지막 열 헤더가 표 스크롤 영역 밖을 기준으로 배치되어 가로 넘침이 발생했다. 알림·공지 표에 `position: relative`를 지정해 수정했고, 최종 빌드와 두 해상도 브라우저 검사에서 해결을 확인했다. 최초 공통 회귀의 Edge 동시 실행은 스크린샷 시간 초과로 중단되어 Chrome 순차 실행으로 완료했다. 통과한 무변경 단위 테스트는 반복하지 않았다.

최종 스크린샷을 직접 확인했다. 결과 JSON과 화면 캡처는 ignored `.preview-pivot/admin-notices-1118/{results,content-regression,member-regression,report-regression}`에 있다. 로컬 검증은 운영 DB 또는 실제 관리자 API 연결 성공을 뜻하지 않는다.

재현 명령:

```powershell
node --test frontend/tests/admin-notices.test.cjs frontend/tests/admin-reports.test.cjs frontend/tests/admin-members.test.cjs frontend/tests/admin-content.test.cjs frontend/tests/admin-content-input.test.cjs
cd frontend
npm run build -- --webpack
npm run typecheck
node tests/prepare-admin-notices-preview.cjs
```

격리 복사본에서도 `npm run build -- --webpack`을 실행한다. 운영 빌드는 loopback 3202, fixture 빌드는 3201에서 실행한다. `PIVOT_PLAYWRIGHT_MODULE`에 설치된 Playwright 경로와 `PIVOT_BROWSER_CHANNEL=chrome`을 지정한 뒤 `tests/admin-notices-browser-check.cjs`를 실행한다. 새 의존성은 추가하지 않았다. Push·PR·Merge·수동 배포는 수행하지 않는다.

## 변경 파일

- `frontend/app/admin/notices/page.tsx`: 단일 운영 진입점.
- `frontend/components/admin/notices-screen.tsx`, `notice-compose-panel.tsx`, `notice-detail-panel.tsx`, `notices.module.css`: 목록·통계·필터·작성·예약·상세와 반응형 스타일.
- `frontend/components/admin/admin-navigation.tsx`, `admin-status.tsx`: 기존 공통 메뉴·브레드크럼·조회 상태 확장.
- `frontend/lib/admin-notices.ts`: 화면 모델과 한국 시간 표시.
- `frontend/public/admin/notices-active.svg`, `notice-forward.svg`, `notice-switch-off.svg`, `notice-switch-on.svg`, `notice-calendar.svg`, `notice-clock.svg`: 원본 SVG 6개.
- `frontend/public/admin/notice-sources.json`, `.gitattributes`: 출처·크기·해시와 바이트 보존.
- `frontend/tests/admin-notices.test.cjs`, `admin-notices-browser-check.cjs`, `prepare-admin-notices-preview.cjs`: 단위·브라우저·fixture 격리 검증.
- `frontend/tests/fixtures/admin-notices.ts`, `admin-notices-page.tsx`: 테스트 전용 예시 데이터와 화면 연결.
- `docs/ADMIN_NOTICES_FIGMA_2026-11-18.md`: 구현·검증 기록.
