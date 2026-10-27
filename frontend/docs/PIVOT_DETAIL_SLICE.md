# Pivot Detail 구현과 검증

2026년 10월 21일 기준으로 Home H2-B의 `자세히 보기`를 매거진 Detail에 연결했다. Home과 Detail은 게시물 ID와 콘텐츠 ID를 함께 사용하며, 같은 제목·대표 이미지·첫 본문을 공유한다. 10월 23일 Mock Preview와 10월 30일 API 통합 테스트를 위한 FE Slice이며, 실제 API 연결은 대기 상태다. 배포는 수행하지 않았다.

## 화면과 URL

기존 Demo의 Splash → 로그인 → 약관 → 온보딩 → 로딩 → Home 흐름에서 Detail을 열 수 있다. 직접 Home을 열려면 `/pivot/demo?view=home`을 사용한다.

| 게시물 | 직접 Detail URL |
| --- | --- |
| 코트 밖 6개 학교, 이번 승부는 밥상에서 | `/pivot/demo?view=detail&postId=demo-home-story&contentId=demo-content-story` |
| 하이큐!!의 색과 표정으로 다시 보는 밥상 | `/pivot/demo?view=detail&postId=demo-home-discovery&contentId=demo-content-discovery` |
| 하이큐!! 협업 소품에서 발견한 작은 이야기 | `/pivot/demo?view=detail&postId=demo-home-reading&contentId=demo-content-reading` |

Demo 접근은 기존 서버 가드를 그대로 따른다. 로컬 개발 또는 `VERCEL_ENV=preview`와 `PIVOT_DEMO_ENABLED=true`가 설정된 Preview에서 접근할 수 있다. Production 배포에서는 플래그와 관계없이 차단한다.

정상 Service 경로는 `/pivot/home?view=detail&postId=…&contentId=…`이다. 아직 API 계약이 없어 연결 준비 상태를 표시하며 Mock으로 대체하지 않는다.

Demo의 정상 Detail URL에 `&detailState=empty` 또는 `&detailState=error`를 붙이면 QA 상태를 확인할 수 있다. 이 옵션은 서버 가드가 적용된 Demo에서만 데이터 소스를 선택하며, Service 경로에는 적용되지 않는다. 오류 예시는 재시도해도 오류를 유지한다. 오류 후 성공 전환은 단위 테스트의 주입 소스로 검증한다.

## 이동과 복원

`HomeDetailExperience`는 Home을 유지한 채 `hidden`으로 숨기고 Detail을 표시한다. Next Native History API와 `useSearchParams`로 URL을 동기화한다. Home 카드의 기존 터치·스크롤 관찰 로직은 그대로 사용한다.

Detail 진입 직전에 브라우저 히스토리에 Home 세로 위치, 게시물별 슬라이드 인덱스, 검색 입력, 진입 버튼의 콘텐츠 ID를 기록한다. 상단 뒤로가기와 하단 HOME은 같은 복귀 처리를 사용한다. Home에서 진입한 히스토리가 있으면 뒤로 이동하고, 직접 URL 접속에는 같은 경로의 `?view=home`으로 대체한다.

Detail 진입 시 맨 위에서 제목에 포커스를 준다. 복귀 시 캐러셀과 스크롤을 복원한 다음 진입 버튼에 `preventScroll`로 포커스를 돌린다. 직접 URL의 Home 복귀는 Home 제목으로 포커스를 옮긴다. 새로고침 후에도 현재 히스토리의 복원 정보를 사용할 수 있다. 브라우저가 히스토리 정보를 제거했거나 새로운 탭에서 직접 열면 Home 상단으로 이동한다. 피드 길이·뷰포트가 달라지면 브라우저의 스크롤 가능 범위 안에서 복원한다.

## Mock과 API 연결 경계

`magazine-mock.ts`가 Demo 편집 콘텐츠의 단일 원본이다. `home-mock.ts`와 `detail-mock.ts`가 각각 이를 Home 카드와 Detail 읽기 모델로 제공한다. 기존 Home의 게시물 ID와 슬라이드 ID, 5·3·2장의 구성 및 다섯 원본 이미지를 유지했다.

`detail.ts`의 `DetailArticle`과 `DetailDataSource`는 FE 표시 모델과 로딩 인터페이스다. 서버 DTO나 엔드포인트를 정의하지 않는다. API 확정 후 `detail-service.ts`에서 인증·요청·응답 매핑을 구현하면 된다. 요청 취소, 늦게 도착한 응답 차단, 데이터 출처와 게시물·콘텐츠 ID 검증을 공통 로더에서 처리한다.

본문이 비었을 때, 없는 ID, 불러오기 오류, 연결 미설정, 로딩을 구분한다. 이미지 실패 시에는 본문을 유지하고 대표 이미지 대체 상태를 표시한다.

`더 볼래요 / 그만 볼래요`는 하나를 고르거나 다시 눌러 해제하는 Demo 동작이다. 글을 닫으면 초기화하며 저장소나 서버에 전송하지 않는다. 실제 추천·저장·전송이 없다는 문구를 버튼 아래에 표시한다. 공유·좋아요·저장·SAVE·MY는 준비 안내를 표시한다. AI 버튼은 질문 바를 펼치며 질문을 전송하지 않는다. 질문 바가 내비게이션을 덮는 동안에는 아래 내비게이션의 키보드 접근을 막는다.

## Figma 대응과 차이

원본은 [범수님 Detail 1272:3720](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1272-3720), [AI 질문 바 1272:4128](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1272-4128)이다.

- 375px에서 대표 이미지 327×408, 좌우 여백 24px, 본문 14/20px, 모서리 8px, 피드백 상단 간격 48px, 하단 내비게이션 200×40px을 적용했다. 390px에서는 이미지 비율을 유지하며 폭을 늘린다.
- 표지·공유·좋아요·저장은 기존 Home 에셋과 SHA-256이 같아 재사용했다. 뒤로가기와 Detail AI 아이콘을 추가했으며 `public/pivot/detail/sources.json`에 원본 노드와 해시를 기록했다. 임시 Figma URL은 런타임에서 사용하지 않는다.
- 시안의 표지는 하이큐!!, 본문은 달빛천사 OST로 섞여 있다. Home과 Detail의 정합성을 위해 하이큐!! Mock 본문으로 교체했다. 현재 행사·가격 정보로 해석되지 않도록 예시임을 명시했다.
- OS 상태바는 재현하지 않고 기존 44px Demo 안내를 유지한다. 시안의 45px 상태바와 차이로 대표 이미지 시작점은 y=93 대신 y=92이다.
- 고정 좌표의 잘린 본문 대신 문서 스크롤을 사용한다. 스크롤 중에도 뒤로가기를 사용할 수 있도록 헤더를 고정하고, 하단 컨트롤과 본문이 겹치지 않도록 끝 여백을 둔다.
- Demo 피드백 설명·선택 상태와 오류·빈 상태를 추가했다. 실제 공유·저장·추천·AI 응답 기능은 연결되지 않았다.

## 검증 결과

로컬 Edge 155.0.4283.62의 모바일 에뮬레이션에서 확인했다. 실제 모바일 단말·모바일 Safari 검증은 포함하지 않는다.

| 검사 | 실제 결과 |
| --- | --- |
| `npm run test` | 200개 통과, 실패 0개. 기존 186개와 Detail 14개 포함 |
| `npm run typecheck` | 통과 |
| `npm run build` | 통과. Next 16.3.5 프로덕션 빌드 및 정적 페이지 생성 성공 |
| Detail 브라우저 | 375×812, 390×844 × 일반 모션·모션 감소, 4개 통과 |
| Home H2-B 시각 검사 | 같은 4개 조합 통과 |
| Home 캐러셀 | 같은 4개 조합 통과. 가로·세로·대각선 터치, 플릭, 키보드, 이미지 실패, 독립 인덱스, Detail 왕복 |
| Splash와 WebGPU | 14개 통과. 모션·포즈·초기화 실패·watchdog·늦은 device·device lost·오류·자원 해제·모션 변경 포함 |
| 온보딩 회귀 | 375×812, 320×568에서 10개 시나리오 통과. 중복 클릭·로딩 재시작·Consumer 격리·Service 차단 포함 |

Detail 브라우저 검사는 세 콘텐츠 ID의 직접 접속·새로고침, 브라우저 앞으로/뒤로, Home 복귀 후 새로고침, 긴 본문·피드백 접근성, 잘못된 ID·잘못된 조합·잘못된 문자열, 오류 재시도·빈 상태·깨진 이미지·Service 분리를 확인한다. Detail 시나리오의 API·외부 요청과 페이지/콘솔 오류는 모두 0건이다.

375px에서는 Home 세로 위치 554px, 390px에서는 575px이 복귀 후 동일했다. 캐러셀 인덱스 `[2, 1, 0]`, 검색 입력과 진입 버튼 포커스도 복원했다.

재현 스크립트는 `tests/pivot-detail-browser-check.cjs`이다. 기존 Playwright를 `PIVOT_PLAYWRIGHT_MODULE`로 지정할 수 있으며 패키지는 설치하지 않는다. 로그·결과 JSON·스크린샷은 저장소의 무시된 `agent/out/pivot-detail/`에 남겼다.

## 수정 파일

기존 파일에 추가한 연결은 다음과 같다. 기존 Home H2-A/B와 Splash 관련 미커밋 변경 위에 적용했다.

- `app/pivot/demo/page.tsx`: 직접 Home·Detail URL 초기 진입과 Demo QA 옵션.
- `components/pivot/demo-flow.tsx`: Demo Home과 Detail 연결, 재시작 시 URL 정리.
- `components/pivot/home-controller.tsx`: Home 준비 후 복원 콜백과 검색 입력 복원.
- `components/pivot/home-route.tsx`: Service 소스 연결과 검색 파라미터 Suspense 경계.
- `components/pivot/home-screen.tsx`: Detail에서도 재사용하는 내비게이션의 HOME 콜백과 AI 숨김 옵션.
- `lib/pivot/home-mock.ts`: 공통 Mock 원본에 제목·대표 이미지·첫 본문 연결.
- `tests/pivot-home-browser-check.cjs`: 기존 Detail 준비 안내 검사를 실제 왕복 검사로 교체.

신규 파일은 다음과 같다.

- `components/pivot/detail-controller.tsx`, `detail-screen.tsx`, `detail.module.css`, `home-detail-experience.tsx`.
- `lib/pivot/detail.ts`, `detail-mock.ts`, `detail-service.ts`, `detail-navigation.ts`, `magazine-mock.ts`.
- `public/pivot/detail/back.svg`, `ai.svg`, `sources.json`.
- `tests/pivot-detail.test.cjs`, `pivot-detail-browser-check.cjs`.
- `docs/PIVOT_DETAIL_SLICE.md`.

## My Page 작업 전 의존성

1. Pivot 사용자 인증·세션 확인, 만료 시 이동, Preview의 Mock 사용자 정책을 확정해야 한다. 기존 Consumer 세션·데이터를 Pivot의 실제 사용자 데이터로 재사용하지 않는다.
2. My Page의 최신 Figma 노드, 프로필 표시 항목, 편집·탈퇴 등의 범위와 HOME·SAVE·MY 라우팅 규칙을 확정해야 한다. 현재 MY는 준비 안내다.
3. 프로필·저장 목록·취향 정보의 API와 권한, 빈 상태·오류 코드·페이지네이션 계약이 필요하다. Detail의 FE 모델을 서버 DTO로 간주하지 않는다.
4. 좋아요·북마크·`더 볼래요 / 그만 볼래요`가 어떤 데이터에 반영되는지, 취소·중복 요청과 재접속 복원 정책을 정해야 한다. 이번 Demo의 선택은 My Page 데이터가 아니다.
5. 10월 23일 Preview에서는 Demo 접근 설정과 콘텐츠 사용 범위를 확인하고, 10월 30일에는 확정된 Detail API 어댑터를 연결해 인증·ID 매핑·오류·취소·실제 저장 결과를 통합 테스트해야 한다.

신규 패키지 설치, 브랜치 전환, commit·push·PR·merge·배포는 수행하지 않았다. BE·Agent·Algorithm·DB·Consumer 코드는 수정하지 않았다.
