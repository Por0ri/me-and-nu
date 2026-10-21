# Pivot My Page 구현과 검증

2026년 10월 21일, 최신 Figma의 My Page 기본 화면을 Pivot Demo에 연결했다. 가상 프로필, 메뉴, 알림 토글, 계정 안내 UI를 제공한다. 실제 프로필·인증·설정 API와 로그아웃·회원탈퇴는 미연동 상태다. 10월 23일 Mock Preview를 위한 구현이며 배포는 수행하지 않았다.

## 화면과 버튼

| 구성 | 이번 Slice의 동작 |
| --- | --- |
| 프로필 카드 | 가상 닉네임 `미뉴님`, 마스킹 이메일 `demo***@example.com` 표시. 실제 개인정보·세션을 읽지 않는다. |
| 프로필 수정 | 준비 중 안내. 닉네임 변경 화면과 저장 기능은 추가하지 않았다. |
| 내 활동·관심분야 관리 | 접근 가능한 버튼과 준비 중 안내. 실제 연결 화면·API 없음. |
| 알림 허용·21시 이후 알림 허용 | Demo React 로컬 상태만 변경. 초기값은 Figma와 같은 켜짐·꺼짐이며, 화면을 떠나거나 새로고침하면 초기화한다. |
| 문의 및 신고·이용약관·개인정보처리방침 | 준비 중 안내. 외부 URL·법적 문구·문의 전송을 추가하지 않았다. |
| 로그아웃·회원탈퇴 | Demo 전용 안내 창. 취소·확인·Escape는 창을 닫으며 실제 로그아웃·계정 삭제·개인정보 삭제를 수행하지 않는다. |
| HOME·MY | Home·Detail에서 MY 이동, My Page에서 HOME 복귀. 현재 메뉴에 `aria-current` 적용. |
| SAVE·헤더 알림 | 기존 준비 중 안내. URL을 바꾸지 않는다. |

알림 토글은 `role="switch"`와 `aria-checked`로 선택 상태를 전달한다. 두 토글 사이의 실제 정책, 브라우저 알림 권한 요청, 영속 저장은 정하지 않았다. 안내 창은 네이티브 `dialog`로 배경 접근을 막고, 취소 버튼에 초기 포커스를 주며, 닫으면 진입 버튼에 포커스를 돌려준다.

저장소 전체에서 정책 파일명과 관련 문구를 검색했다. `docs/FIGMA_API_V1_1_IMPLEMENTATION_BACKLOG.md`는 전체 약관·개인정보처리방침의 제공처가 미정이라고 기록한다. 기존 Pivot의 동의 문구는 Preview/Demo 예시다. Figma 작업중 페이지에 법적 문구 초안은 있으나 프로젝트의 확정된 서비스 정책 통합본으로 간주하지 않았다.

## URL과 화면 이동

| URL | 결과 |
| --- | --- |
| `/pivot/demo?view=my` | 서버 Demo 가드가 허용한 환경에서 My Page 직접 진입 |
| `/pivot/demo?view=home` | 기존 Demo Home |
| `/pivot/demo?view=my&postId=…&contentId=…` | Detail에서 MY를 열 때 콘텐츠 식별자를 유지하는 URL |
| `/pivot/home?view=my` | Service 연결 준비 상태. 가상 프로필·설정 토글을 표시하지 않음 |

새 App Router 디렉터리는 만들지 않았다. 기존 `view=home/detail` 구조에 `view=my`를 추가했다. Demo를 Splash부터 진행한 뒤에도 Home의 MY로 진입할 수 있다.

Home은 마운트를 유지하고 숨긴다. 진입 직전에 세로 위치, 검색 입력, 게시물별 캐러셀 인덱스를 브라우저 히스토리에 기록한다. HOME·브라우저 뒤로가기·새로고침 후 복귀 시 같은 스냅샷을 복원한다. Home에서 MY로 진입한 경우 복귀 포커스는 MY 버튼이다. 직접 My URL 접속 후 HOME은 같은 경로의 `view=home`으로 안전하게 이동한다.

Detail에서 MY로 이동하면 Detail의 컴포넌트를 숨겨 유지한다. 브라우저 뒤로가기 시 피드백 선택·읽기 위치·진입 포커스를 유지한다. MY의 HOME으로 이동하면 Home을 복원하고, 이후 다른 Detail을 열 때는 이전 글의 읽기 위치를 적용하지 않는다. 새로고침은 Detail 피드백과 My 알림 토글을 영속 저장하지 않는다.

검증에서는 Home 검색어 `MY 왕복 보존`, 캐러셀 `[2, 1, 0]`, 세로 위치 554px이 복원됐다. Detail 읽기 위치는 375px 화면에서 390px, 390px 화면에서 377px이었으며 MY 왕복 후 동일했다.

## Mock과 Service 격리

`my-page.ts`는 FE 표시 모델과 데이터 소스 인터페이스다. `my-page-mock.ts`만 가상 프로필을 만들며 DemoFlow에서 주입한다. 이메일은 예시용 `example.com` 도메인을 사용한다. `my-page-service.ts`는 연결 준비 결과만 반환한다.

공통 로더는 요청 취소·늦은 응답 무시·재시도·프로필 출처 확인을 처리한다. Service에 Demo 프로필이 주입되거나 반대 출처가 반환되면 오류로 차단한다. 공유 화면도 출처를 재확인하며, 화면 조합에서 Home과 My 소스 종류가 다르면 Service 준비 상태로 차단한다. 실제 API 오류를 Mock 사용자로 대체하지 않는다.

Service 어댑터는 네트워크·쿠키·브라우저 저장소·Consumer 세션에 접근하지 않는다. 향후 인증과 `users/me` 등의 계약이 확정되면 별도 어댑터에서 응답을 FE 모델로 변환하면 된다. V1.1의 엔드포인트·DTO·알림 정책을 최신 확정 계약으로 가정하지 않았다. 현재 토글·계정 안내는 서버 쓰기를 수행할 수 없다.

## Figma 대응과 남은 차이

우선 기준은 [범수님 My Page 1299:4600](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1299-4600)이다. [작업중 My Page 1244:2108](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1244-2108), [닉네임 변경 1335:3534](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1335-3534), [로그아웃 1353:5440](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1353-5440)을 읽기 전용으로 대조했다.

배경 `#fff9fd`, 흰 카드, 모서리 8px, 카드 간격 12px, 보라색 그림자, 프로필 16/24px·이메일 14/20px·메뉴 12/18px·그룹 제목 11/16px을 적용했다. 375px에서는 프로필 카드 327×68px, 390px에서는 342×68px이다. 내 정보·알림 카드 높이는 96px이다.

화살표 네 종류와 켜짐·꺼짐 토글은 Figma 원본 SVG를 로컬에 저장했다. 표시 크기는 각각 16×24px, 33×18px이다. 로고와 알림 아이콘은 My Page 원본 다운로드와 기존 Home 파일의 SHA-256이 일치해 기존 파일을 재사용했다. 에셋 출처·크기·해시는 `public/pivot/my/sources.json`에 기록했고 12개 표시 이미지의 로딩과 실제 크기를 브라우저에서 확인했다.

남은 차이는 다음과 같다.

- iOS 상태바를 재현하지 않고 기존 44px Demo 안내를 유지한다. Figma의 45px 상태바와 차이로 본문 시작은 1px 위다.
- 본문은 기존 Home·Detail의 좌우 24px 정렬을 따른다. Figma My Page의 본문 x=22px과 2px 차이가 있다.
- 하단 내비게이션은 공통 200×40px와 바닥 20px 간격을 따른다. My 시안의 바닥 12px보다 8px 위다.
- 이용 안내·계정 카드는 행 높이의 합인 128px·64px이다. 시안의 129px·65px보다 각각 1px 작다.
- 실제 이메일 대신 가상 이메일과 Demo 안내를 표시한다. 하단에 토글 초기화 설명을 추가했다.
- 계정 안내 창은 시안의 둥근 카드·버튼 형태를 따르되 Demo 의미에 맞게 문구와 높이를 조정했다. 실제 닉네임 편집·로그아웃·회원탈퇴 플로우는 확정하지 않았다.

## 자동 검사와 브라우저 결과

| 검사 | 실제 결과 |
| --- | --- |
| `npm run test` | 209개 통과. 기존 200개와 My Page 9개 포함 |
| `npm run typecheck` | 통과 |
| `npm run build` | Next 16.3.5 프로덕션 빌드 통과 |
| `git diff --check` | 통과. Git의 기존 LF→CRLF 안내는 남음 |
| My Page | 375×812·390×844 × 일반 모션·감소 모션, 4개 통과 |
| Home 캐러셀 | 같은 4개 조합 통과. 터치·플릭·키보드·독립 인덱스·Detail 복귀 포함 |
| Home H2-B | 같은 4개 조합에서 에셋·레이아웃·포즈·내비게이션 검사 통과 |
| Detail | 같은 4개 조합 통과. 직접 URL·새로고침·앞으로/뒤로·빈 상태·오류·이미지 실패·Service 격리 포함 |
| Splash WebGPU | 14개 통과. 초기화 실패·watchdog·device lost·자원 해제·모션 변경 포함 |
| 온보딩 | 375×812·320×568, 감소 모션의 10개 시나리오 통과. 중복 클릭·재시작·Consumer 격리 포함 |
| 로컬 프로덕션 | 375×812·390×844, 2개 통과. `VERCEL_ENV=production`, `PIVOT_DEMO_ENABLED=true`여도 Demo My/Preview는 HTTP 404이고 Service My는 준비 상태 |

My Page 검사는 프로필·이미지 크기, 두 토글의 포인터/키보드 조작, 새로고침·재진입 초기화, 모든 준비 중 안내, SAVE의 URL 유지, 계정 안내의 두 버튼·Escape·포커스 복귀·배경 접근 차단, HOME→MY→HOME, Home 복귀 후 새로고침, Detail→MY→뒤로가기, 다른 Detail 신규 진입을 검증한다. My Page 시나리오에서 API·외부 요청·브라우저 알림 권한 요청·저장소 변경·페이지/콘솔 오류는 모두 0건이다.

로컬 Edge 155.0.4283.62의 모바일 에뮬레이션 결과다. 실제 모바일 기기·Safari·원격 Vercel Preview·실제 BE 통합은 검증하지 않았다. 온보딩 일반 모션 전체 흐름은 이번 브라우저 회귀에 포함하지 않았고, Splash와 Home·Detail·My는 두 모션을 모두 검사했다. HMR 재컴파일 자체의 별도 재현 검사는 수행하지 않았다.

초기 병렬 실행 중 기존 Detail 검사 375px 일반 모션에서 한 차례 진입 대기 시간 초과가 있었고, 같은 스크립트 재실행에서는 4개 조합 모두 통과했다. 원인은 확정하지 않았다. 기존 `blocked-production` 검사는 의도한 HTTP 404의 콘솔 항목까지 오류 0건으로 요구해 실패했다. 이번 My Page 브라우저 스크립트의 production 모드에서는 HTTP 404 상태를 검증하고 예상된 404 콘솔 항목을 별도 기록해 2개 조합이 통과했다. 기존 검사 파일은 수정하지 않았다.

증거는 무시된 `agent/out/pivot-my-page/`의 결과 JSON·스크린샷·명령 로그와 `agent/out/pivot-my-regression/`의 각 회귀 결과에 있다. My Page 재현 스크립트는 `tests/pivot-my-page-browser-check.cjs`이며 네 번째 인자 `production`으로 프로덕션 차단 검사를 실행한다. `PIVOT_PLAYWRIGHT_MODULE`에 기존 Playwright 설치 경로를 지정할 수 있다.

## 이번 작업에서 수정한 파일

기존 미커밋 변경 위에 연결 코드를 추가한 파일은 7개다.

- `frontend/app/pivot/demo/page.tsx`
- `frontend/components/pivot/demo-flow.tsx`
- `frontend/components/pivot/home-controller.tsx`
- `frontend/components/pivot/home-screen.tsx`
- `frontend/components/pivot/home-detail-experience.tsx`
- `frontend/components/pivot/detail-controller.tsx`
- `frontend/components/pivot/detail-screen.tsx`

추가한 파일은 16개다.

- `frontend/components/pivot/my-page-controller.tsx`
- `frontend/components/pivot/my-page-screen.tsx`
- `frontend/components/pivot/my-page.module.css`
- `frontend/lib/pivot/my-page.ts`
- `frontend/lib/pivot/my-page-mock.ts`
- `frontend/lib/pivot/my-page-service.ts`
- `frontend/public/pivot/my/chevron.svg`
- `frontend/public/pivot/my/chevron-last.svg`
- `frontend/public/pivot/my/chevron-muted.svg`
- `frontend/public/pivot/my/chevron-muted-last.svg`
- `frontend/public/pivot/my/switch-on.svg`
- `frontend/public/pivot/my/switch-off.svg`
- `frontend/public/pivot/my/sources.json`
- `frontend/tests/pivot-my-page.test.cjs`
- `frontend/tests/pivot-my-page-browser-check.cjs`
- `frontend/docs/PIVOT_MY_PAGE_SLICE.md`

## Git 보호와 Preview 전 점검

작업 전후 브랜치는 `feature/pivot-user-fe`, HEAD는 `2366397454503e83105d4ce2dbaf9e4661cce627`이다. 작업 전 변경 파일과 `next-env.d.ts` 70개를 해시로 기록했다. 연결을 확장한 위 7개 외의 63개는 바이트가 동일하고 누락된 파일은 없다. Gesture Spike 미추적 파일 10개는 모두 동일하다. Home 캐러셀 로직·Home CSS·Mock 콘텐츠·Splash WebGPU/HMR 설정은 별도로 수정하지 않았다.

Next 빌드가 `next-env.d.ts`의 생성 타입 경로를 일시 변경했으나, 원래 바이트로 복원하고 작업 전 SHA-256 일치를 확인했다. 이후 빌드 시에도 자동 변경 여부를 확인해야 한다. Consumer·Backend·Agent·Algorithm·DB 코드는 수정하지 않았다. 새 브랜치·Worktree·패키지 설치·commit·push·PR·merge·pull·reset·clean·rebase·배포는 수행하지 않았다.

10월 23일 Vercel Hobby Preview 전에는 다음을 확인해야 한다.

1. Preview 환경에만 기존 Demo 접근 설정을 적용하고 Production 차단을 유지한다. `/pivot/demo?view=my` 직접 접속과 새로고침을 실제 Preview에서도 확인한다.
2. 가상 프로필·로컬 알림 선택·준비 중 메뉴·계정 안내 문구와 Figma의 소폭 여백 차이를 팀에서 확인한다.
3. 서비스 정책 통합본, 닉네임 수정, 알림 저장·야간 정책, 로그아웃·회원탈퇴 범위를 팀에서 확정한다. 확정 전에는 현재 준비 상태를 유지한다.
4. 실제 모바일 Safari/기기와 온보딩 일반 모션 전체 흐름을 확인하고, Detail 초기 진입의 간헐적 대기 실패를 Preview에서 재확인한다.
5. 10월 30일 통합 테스트 전 인증·세션·프로필 조회/수정·설정 API 계약을 확정해 Service 어댑터를 연결하고 실제 오류·만료·저장 결과를 검증한다.
