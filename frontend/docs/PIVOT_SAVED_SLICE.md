# Pivot Saved 구현과 My Page 디자인 차이

2026년 10월 21일, 최신 Saved 시안을 Pivot Demo의 HOME/SAVE/MY와 연결했다. 저장 카드에서 같은 콘텐츠의 Detail로 이동하며, Home·Detail의 북마크 선택을 Demo 메모리 목록에 반영한다. 실제 북마크 API·영속 저장·인증 기능은 추가하지 않았다. 최신 My Page 세부 화면은 읽기 전용으로 비교했으며 이번 Slice의 구현 범위를 확대하지 않았다.

## Saved 화면과 동작

기준은 [범수님 Saved 1363:8604](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-8604)이다.

| 항목 | 구현과 검증 결과 |
| --- | --- |
| 375px 레이아웃 | 좌우 24px 영역 안에 2열. 카드 158×203px, 열·행 간격 10px, 모서리 4px, 보라색 그림자 |
| 카드 위치 | 첫 열 x=24.5px, 둘째 열 x=192.5px. 327px 영역 안의 남는 1px을 양쪽에 나눔 |
| 390px 레이아웃 | 카드 폭 165.5px, 높이 약 212.63px. 158:203 비율과 10px 간격 유지 |
| 헤더·하단 메뉴 | 공통 로고·알림·HOME/SAVE/MY 재사용. SAVE에 현재 페이지 표시 |
| 카드 선택 | 키보드·포인터로 선택한 `postId`와 `contentId`의 Pivot Detail 진입 |
| 북마크 | Home·Detail의 기존 저장 아이콘을 Demo에서 조작. 선택 상태는 `aria-pressed`와 보라색 배경으로 표시 |
| 빈 목록 | Demo에서 전부 해제하면 빈 목록 안내와 `HOME에서 둘러보기` 표시 |
| 이미지 실패 | 카드 크기를 유지하고 `이미지 준비 중`과 실제 제목 표시. 이 상태에서도 같은 Detail로 이동 |
| Service | 저장 목록 연결 준비 상태. Demo 카드·가상 사용자·가짜 빈 목록으로 대체하지 않음 |

초기 목록은 기존 Home·Detail과 일치하는 세 콘텐츠다. Figma의 동일 샘플 10개를 복제해 중복 ID를 만들지 않았다. 첫 두 항목은 기존 Home과 같은 표지를 사용하고, 세 번째는 해당 콘텐츠의 협업 소품 이미지를 사용한다. 제목은 버튼의 접근 가능한 이름에 포함한다. 시안처럼 이미지 자체를 카드로 표시하며 별도 제목 영역은 추가하지 않았다.

Saved의 표지·로고·알림 에셋을 Figma에서 다운로드해 기존 파일과 SHA-256을 비교했고 모두 일치했다. 같은 파일을 재사용하므로 새 이미지 파일이나 임시 Figma URL은 런타임에 추가하지 않았다. 세 번째 콘텐츠 이미지도 기존 공통 Mock 원본을 사용한다. `public/pivot/saved/sources.json`에 레이아웃과 에셋 해시를 기록했다.

Figma 대비 남은 차이는 샘플 카드 수 10개 대신 고유 콘텐츠 3개, Demo 설명 문구, 기존 공통 내비게이션의 바닥 20px 간격이다. 시안의 45px iOS 상태바는 재현하지 않고 기존 44px Demo 안내를 유지해 카드 시작은 y=101 대신 y=100이다. 시안 카드 내부 이미지 높이 204px와 외곽 203px 중 외곽 203px를 기준으로 클리핑했다.

## URL과 복귀

| URL | 용도 |
| --- | --- |
| `/pivot/demo?view=saved` | Demo Saved 직접 진입 |
| `/pivot/demo?view=home` | 기존 Demo Home |
| `/pivot/demo?view=my` | 기존 Demo My Page |
| `/pivot/demo?view=detail&postId=…&contentId=…&from=saved` | Saved에서 연 콘텐츠의 Detail |
| `/pivot/home?view=saved` | 실제 Service용 연결 준비 화면 |

기존 `view` 기반 라우팅을 확장했고 새 App Router 디렉터리는 만들지 않았다. Home에서 시작하는 Detail URL은 기존 형태를 유지한다. Saved 경유 Detail의 상단 뒤로가기·SAVE는 Saved로, 하단 HOME은 Home으로 이동한다. 신뢰할 수 있는 복귀 히스토리가 없는 직접 Detail URL에서도 같은 경로의 Saved로 안전하게 돌아간다.

Home은 숨긴 채 유지하며 검색 입력·각 캐러셀 인덱스·세로 위치를 히스토리 스냅샷으로 복원한다. SAVE로 나왔다면 복귀 포커스도 SAVE 버튼이다. 검증에서 검색어 `Saved 왕복 보존`, 인덱스 `[2, 1, 0]`, 세로 위치 554px이 왕복·앞으로/뒤로·새로고침 후 유지됐다. 북마크 조작 자체도 Home의 위치와 캐러셀을 초기화하지 않았다.

Saved에서 연 Detail은 뒤로가기 시 선택했던 카드로 포커스를 돌린다. Detail에서 해당 항목을 저장 해제해 카드가 사라지면 Saved 제목에 포커스를 둔다. Home에서 연 Detail→SAVE→브라우저 뒤로가기에서는 기존 피드백 선택과 읽던 위치를 유지했다. 375px 화면의 읽기 위치 390px, 390px 화면의 377px이 동일하게 복원됐다.

Next가 `replaceState`의 인수에 내부 라우터 값을 추가하므로 이후 `pushState`에 같은 객체를 재사용하지 않는다. 북마크 안내 문구가 다른 카드의 버튼 조작을 가리지 않도록 안내 영역은 포인터 이벤트를 받지 않는다.

## Demo와 Service 경계

`saved.ts`는 FE 목록 모델과 읽기 인터페이스다. 계약 미확정인 북마크 엔드포인트·HTTP 메서드·서버 DTO를 정의하지 않는다. 공통 로더가 출처, 콘텐츠 ID 중복, 잘못된 ID, 빈 제목을 검사하고 로딩·빈 상태·오류·연결 준비를 구분한다. 취소되거나 대체된 요청의 늦은 결과는 무시한다.

`saved-mock.ts`는 DemoFlow 인스턴스마다 별도 메모리 저장소를 만든다. 목록·Home·Detail이 같은 저장소를 사용하고 `magazine-mock.ts`의 제목·대표 이미지·두 ID를 공유한다. 알 수 없는 ID나 일치하지 않는 게시물/콘텐츠 조합은 추가하지 않는다. 목록 변경은 이번 시연 동안만 유지되며 새로고침·시연 재시작 시 초기 세 항목으로 돌아간다. 서버·쿠키·localStorage·sessionStorage에 저장하지 않는다.

`saved-service.ts`는 연결 준비 결과만 제공한다. Service 어댑터와 공유 화면은 Demo 출처를 거부하고, Home과 Saved 소스 종류가 다를 때도 Demo 조작을 차단한다. Service 오류를 Mock으로 대체하지 않는다. URL에 `savedState=empty`나 Demo 콘텐츠 ID를 넣어도 Service가 가짜 저장 목록을 표시하지 않는다.

## 최신 My Page와 현재 FE의 차이

| 화면 | 최신 Figma 확인 내용 | 현재 FE와 미구현 범위 |
| --- | --- | --- |
| [기본 1363:5725](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-5725) | 프로필 카드·내 정보·알림·이용 안내·계정 버튼. 닉네임은 `미뉴` | 기본 구성은 구현됨. 현재는 `미뉴님`과 가상 이메일, Demo 안내를 유지. 본문 정렬·내비게이션 간격의 기존 차이도 유지 |
| [닉네임 1363:5820](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-5820), [6315](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-6315), [6352](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-6352) | 프로필 요약, 닉네임 입력, 2~10자·특수문자 제외 안내, 길이 카운터, 저장 버튼 상태 | 프로필 수정은 준비 중 안내. 입력·검증·변경 저장 화면 미구현. 실제 검증/수정 계약도 미확정 |
| [내 활동 1363:6139](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-6139), [시청 내역 6260](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-6260) | 좋아요한 콘텐츠·시청 내역 탭, 카드 목록. 6194·6227에 각각 빈 상태와 콘텐츠 보러가기 버튼 | 내 활동은 준비 중 안내. 탭·목록·빈 상태·조회 연동 미구현. Demo 북마크를 좋아요/시청 기록으로 간주하지 않음 |
| [관심분야 1363:6543](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-6543) | 애니메이션 선택 중, 추가 예정 카드, 이미 탐색 중이라는 도움말 | 관심분야 관리는 준비 중 안내. 관리 화면·선택 변경·설정 연동 미구현. 온보딩 화면을 실제 설정 화면으로 연결하지 않음 |
| [문의 1363:5857](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-5857) 등 | 서비스 문의·버그/오류 신고·콘텐츠 신고, 필수 유형·본문, 글자 수 표시, 접수 버튼의 입력 상태 변형 | 준비 중 안내. 폼·입력 제한·전송·접수 결과 미구현. 5904·5951·5998·6045·6092·7298·7377·7543·7622도 읽기 전용 확인 |
| [약관 1363:7879](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-7879) | 297×695px 팝업, 시행일, 이용약관·운영정책 본문 영역 | 준비 중 안내. 확정 정책 원문·버전·스크롤 팝업 연동 미구현 |
| [개인정보 1363:8132](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-8132) | 297×423px 팝업, 시행일, 본문 영역 | 준비 중 안내. 확정 원문·팝업 미구현 |
| [로그아웃 1363:8253](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-8253) | 297×213px 확인 창, 취소·로그아웃 버튼 | Demo 안내 창만 구현됨. 실제 OAuth/세션 종료와 후속 이동 미구현 |
| [탈퇴 1363:8374](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-8374) | 297×245px 확인 창, 취소·탈퇴, 6개월 복구 문구 | Demo 안내 창만 구현됨. 삭제·유예·복구·재인증 전체 흐름 미확정·미구현 |

최신 탈퇴 팝업의 **6개월 복구**와 같은 최신 약관·개인정보 초안의 **30일 유예**가 일치하지 않는다. 실제 탈퇴·복구 정책과 문구를 먼저 통합해야 한다. Figma의 정책 팝업은 2026년 11월 2일 시행을 표시하지만 이를 프로젝트의 확정된 서비스 정책으로 채택하지 않았다. 법적 문구를 임의 작성하거나 기존 Demo 안내를 실제 삭제 성공으로 바꾸지 않았다.

## 검증 결과

| 검사 | 결과 |
| --- | --- |
| `npm run test` | 222개 통과. 기존 209개와 Saved 13개 포함 |
| `npm run typecheck` | 통과 |
| `npm run build` | Next 16.3.5 프로덕션 빌드 통과 |
| `git diff --check` | 통과. 새 파일의 후행 공백도 별도 확인 |
| Saved 브라우저 | 375×812·390×844 × 일반/감소 모션, 4개 통과 |
| My Page 회귀 | 같은 4개 조합 통과. 기존 SAVE 준비 안내 검사를 실제 Saved 왕복 검사로 갱신 |
| Home 캐러셀·H2-B·Detail | 각각 같은 4개 조합 통과 |
| Splash WebGPU | 14개 통과. 일반/감소 모션·초기화 실패·watchdog·device lost·자원 해제 포함 |
| 온보딩 | 375×812·320×568, 감소 모션 10개 통과. 중복 클릭·재시작·Consumer 격리 포함 |
| 로컬 프로덕션 차단 | 375×812·390×844, 2개 통과. `VERCEL_ENV=production`, `PIVOT_DEMO_ENABLED=true`에도 Demo Saved/Detail은 404, Service Saved는 준비 상태 |

Saved 브라우저 검사는 2열 치수와 에셋 크기, 세 콘텐츠 ID·제목·이미지 정합성, 모든 카드의 포인터/키보드 Detail 진입, 뒤로가기·SAVE·HOME, 직접 URL·새로고침·브라우저 앞뒤 이동, Home 상태 유지, Detail 피드백/읽기 위치 유지, 북마크 양방향 반영, 전부 해제한 빈 목록, 한 항목 재추가, 새로고침 초기화, 이미지 실패 시 진입, Service 격리를 확인했다. 테스트 브라우저의 API·외부 요청·저장소/쿠키 변경·페이지/콘솔 오류는 0건이다. 프로덕션의 의도된 HTTP 404 콘솔 항목은 별도로 기록한다.

로컬 Edge 155.0.4283.62 모바일 에뮬레이션 결과다. 실제 모바일 기기·Safari·원격 Vercel Preview·실제 BE 북마크 연동은 검증하지 않았다. 긴 Saved 목록의 실제 페이지네이션·정렬·대량 데이터 성능도 미검증이며 API 계약으로 가정하지 않았다. 이번 온보딩 브라우저 회귀는 감소 모션 기준이다.

검증 중 발견한 Home의 URL/화면 불일치는 History 인수 재사용을 제거해 해결했다. 북마크 안내의 포인터 가림도 해결한 뒤 Saved 4개 조합을 모두 다시 통과했다. 최종 결과와 스크린샷은 무시된 `agent/out/pivot-saved/`, 회귀 결과는 `agent/out/pivot-saved-regression/`에 있다. 재현 스크립트는 `tests/pivot-saved-browser-check.cjs`이며 네 번째 인자 `production`으로 접근 차단 검사를 실행한다. 기존 Playwright 설치를 사용했고 패키지는 설치하지 않았다.

## 변경 파일 전체

기존 미커밋 파일 13개에 Saved 연결을 확장했다.

- `frontend/app/pivot/demo/page.tsx`
- `frontend/components/pivot/demo-flow.tsx`
- `frontend/components/pivot/home-controller.tsx`
- `frontend/components/pivot/home-screen.tsx`
- `frontend/components/pivot/home.module.css`
- `frontend/components/pivot/home-detail-experience.tsx`
- `frontend/components/pivot/detail-controller.tsx`
- `frontend/components/pivot/detail-screen.tsx`
- `frontend/components/pivot/detail.module.css`
- `frontend/components/pivot/my-page-controller.tsx`
- `frontend/components/pivot/my-page-screen.tsx`
- `frontend/lib/pivot/detail-navigation.ts`
- `frontend/tests/pivot-my-page-browser-check.cjs`

추가 파일은 11개다.

- `frontend/components/pivot/saved-controller.tsx`
- `frontend/components/pivot/saved-screen.tsx`
- `frontend/components/pivot/saved.module.css`
- `frontend/lib/pivot/saved.ts`
- `frontend/lib/pivot/saved-mock.ts`
- `frontend/lib/pivot/saved-service.ts`
- `frontend/lib/pivot/saved-navigation.ts`
- `frontend/public/pivot/saved/sources.json`
- `frontend/tests/pivot-saved.test.cjs`
- `frontend/tests/pivot-saved-browser-check.cjs`
- `frontend/docs/PIVOT_SAVED_SLICE.md`

## Git 보호와 로컬 검증 환경

작업 전후 브랜치는 `feature/pivot-user-fe`, HEAD는 `2366397454503e83105d4ce2dbaf9e4661cce627`이다. 작업 전 변경 파일과 `next-env.d.ts` 총 86개를 해시로 기록했다. 위 13개는 필요한 연결만 확장했고 나머지 73개는 동일하다. 누락된 파일은 없다. Gesture Spike 10개와 `next-env.d.ts`는 작업 전 SHA-256과 동일하다.

빌드가 생성 타입 경로를 자동 변경하는 `next-env.d.ts`는 검증 후 원래 바이트로 복원했다. 기존 Home 캐러셀 제스처 코드, Splash WebGPU/HMR 설정, 온보딩, 공통 Mock 콘텐츠 원본은 변경하지 않았다. Consumer·BE·Agent·DB 소스, 패키지/잠금 파일도 수정하지 않았다. 새 브랜치·Worktree·Git reset/clean·commit·push·PR·merge·배포는 수행하지 않았다.

검증용 로컬 프로덕션 서버는 3101 포트를 사용한다. Ctrl+C와 PowerShell 종료가 실패한 뒤 추가 종료 권한 요청이 거절되어 실행 상태로 남겼다. 기존 3100 개발 서버는 유지했다. Vercel 배포나 환경 파일 변경은 하지 않았다.

## 10월 23일 Preview와 별도 추가 작업

| 구분 | 남은 작업과 일정 영향 |
| --- | --- |
| Preview 전 점검 | 실제 Preview 호스트에서 Demo 접근 설정, Saved 직접 접속·새로고침, HOME/SAVE/MY→Detail 왕복과 이미지/폰트 제공을 짧게 재검증해야 한다. 실제 기기·Safari 확인도 필요하다. |
| Demo 표현 확인 | 초기 예시 3개·북마크 선택·새로고침 초기화·서버 저장 없음 안내와 Figma 반복 샘플 수의 차이를 팀이 확인하면 된다. 추가 API 작업은 필요하지 않다. |
| Saved의 후속 Service 작업 | 인증, 저장 목록 조회·정렬/페이지네이션, 북마크 추가/해제 계약을 확정한 뒤 별도 어댑터와 실제 오류·만료·저장 결과를 10월 30일 통합 테스트에서 검증해야 한다. |
| My Page의 추가 화면 | 닉네임 편집·활동 탭·관심분야 관리·문의 폼·정책 팝업·실제 로그아웃/탈퇴는 이번 Saved Slice 외 작업이다. Preview에 포함하려면 별도 범위·일정 결정이 필요하다. |
| 정책 선행 결정 | 탈퇴 6개월/30일 불일치, 확정 정책 원문/버전, 신고/문의 처리 범위와 인증·복구 정책을 정리해야 한다. 시안만 보고 기능을 확정하지 않는다. |
