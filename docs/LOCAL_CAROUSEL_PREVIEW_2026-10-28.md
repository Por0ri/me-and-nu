# 캐러셀 로컬 미리보기 — 2026-10-28

실제 작품 카탈로그 30개와 전용 테스트 계정·애니 Tap을 준비했다. **실제 에이전트 콘텐츠는 0개**이며 생성·독립 검수는 보류 중이다. 사용자가 허용한 **화면 확인용 테스트 캐러셀 3개(3장·5장·10장, PNG 18개)**를 전용 DB에 적재했고, Chrome 모바일·데스크톱에서 홈·상세·저장 연결을 확인했다. 제목·본문·출처에 `[테스트]`를 표시한다. 아래 최초 확인 당시의 0개 기록과 구분하며, 데이터 식별자와 실제 콘텐츠 교체 절차는 [테스트 데이터 인계](CAROUSEL_TEST_DATA_HANDOFF_2026-10-28.md)에 기록한다.

API 연결을 유지한 기존 디자인 복원 단계에서는 단위 테스트 294개·타입 검사·빌드·health와 Chrome 390×844·1280×900 재검증을 통과했다. 이후 로그아웃 연결을 수정했고 프론트 단위 테스트 297개·타입 검사·`--webpack` 빌드·실제 OAuth 모드 startup health가 통과했다. 보조 가입 전 세션부터 로그아웃까지의 390px·1280px 검증에 이어, 사용자가 직접 Kakao 로그인·V2 온보딩을 완료한 뒤 실제 세션의 홈·상세·북마크·로그아웃·재접근 차단까지 확인했다. Naver는 로그인 폼 도달까지만 확인했다. 아래 복원 단계 기록과 이번 [서비스 전체 흐름 검증](SERVICE_FLOW_E2E_2026-10-28.md)을 구분한다.

이 문서의 실행 중인 서버·helper·화면 증거는 **`C:/Users/USER/.codex/worktrees/service-integration/me_nu`**의 자료다. 서비스 반영 후보 `C:/Users/USER/.codex/worktrees/backend-labs-service/me_nu`에는 ignored 런타임·PNG·테스트 결과를 복사하지 않았다. 아래 실행 명령도 기존 통합 작업 공간에서만 사용한다.

기존 에이전트 키는 401 응답으로 생성이 막혔다. 다른 backend 키는 소유권·과금 주체가 확인되지 않아 사용하지 않았으며, 실제 생성·검수·적재를 보류했다. 로컬 API 자식 프로세스의 `LLM_KEY`, `TMDB_KEY`, `OPENAI_API_KEY`는 빈값으로 설정한다. 원본 `.env`는 변경하지 않는다.

## 접속

- 온보딩을 마친 테스트 사용자로 홈 열기: <http://localhost:8211/__preview__/enter>
- 실제 소셜 로그인 시작(`-RealOAuth` 실행 때): <http://localhost:3331/pivot/login>
- 로그인 후 홈: <http://localhost:3331/pivot/home>
- API DB 상태: <http://localhost:8211/health/db>

현재 로컬 서버는 실제 OAuth 모드로 유지하며, 실제 Kakao 로그아웃·홈 재접근 차단을 확인한 뒤 인앱 브라우저는 로그인 화면에 있다. 다시 로그인하려면 해당 화면의 공급자 버튼으로 새 인증을 시작한다. 이전 공급자 URL은 재사용하지 않는다. 로컬 `backend-labs` 반영은 후보 검증·원본 작업 보존 후 진행할 단계이며 원격 push·배포는 계속 금지한다.

진입 링크는 이 컴퓨터의 `localhost`에서 온보딩이 완료된 전용 테스트 사용자에게 활성 세션을 발급하고 홈으로 이동한다. 별도 HttpOnly 쿠키 `menu_actual_content_preview`를 사용하며, SameSite=Lax·유효 기간 8시간이다. 만료하거나 로그아웃한 뒤에는 진입 링크를 다시 연다. `localhost`와 `127.0.0.1`을 섞지 않는다. **이 링크는 실제 소셜 로그인이나 신규 가입·온보딩을 검증하는 경로가 아니다.**

임시 진입 경로는 ignored 런타임 wrapper에만 있으며 서비스 API·OpenAPI에 추가하지 않았다. 기본 실행은 OAuth 비활성이고, `-RealOAuth`를 명시한 실행만 Kakao·Naver OAuth를 활성화한다. 개발용 인증 우회·개발 API는 두 모드 모두 비활성이다. API/FE는 loopback에만 바인딩하며, 서비스 세션과 데이터는 전용 미리보기 DB에 저장한다.

## 격리 범위

| 항목 | 값 |
| --- | --- |
| PostgreSQL 컨테이너 | `me-nu-carousel-preview-20261028` |
| PostgreSQL 바인딩 | `127.0.0.1:63362` |
| DB | `carousel_preview` |
| 스키마 revision | `f4b8c1d9a620` |
| 실제 작품 카탈로그 | 30개 |
| 실제 에이전트 콘텐츠 | 0개 — 생성·독립 검수 보류 |
| 화면 확인용 테스트 캐러셀 | 3개(3장·5장·10장), PNG 18개 적재 완료 |
| 기본 모드 API / FE | `localhost:8211` / `localhost:3331` |
| 실제 OAuth 모드 프론트 API·callback | `localhost:8000` — `-RealOAuth`로 빌드·실행 |
| 실제 OAuth 모드 보조 API | `localhost:8211` — 8000과 같은 API PID·DB·세션으로 제공 |
| 기존 서비스 | PostgreSQL 5433·Redis 6379 변경 없음 |

생년월일 정책은 계속 보류한다. 실제 서비스 DB 마이그레이션·main 머지·배포는 하지 않는다. frontend는 선택한 모드의 API 주소로 빌드한 로컬 production 서버를 사용한다. 최신 전체 흐름 검증용 빌드·재시작 결과는 별도 검증 보고서에 기록한다.

## 시작·종료

아래 명령은 새 서비스 후보가 아니라 기존 통합 작업 공간에서 PowerShell로 실행한다. 기본 모드는 기존 미리보기를 종료하고 API 8211을 대상으로 빌드한 뒤 시작한다.

```powershell
Set-Location 'C:/Users/USER/.codex/worktrees/service-integration/me_nu'
& ./frontend/out/actual-carousel-preview/stop.ps1
& ./frontend/out/actual-carousel-preview/build.ps1
& ./frontend/out/actual-carousel-preview/start.ps1
```

실제 OAuth를 확인할 때는 아래 순서로 API 8000을 대상으로 다시 빌드하고 시작한다. build와 start 양쪽에 같은 `-RealOAuth` 옵션을 넣는다. 기본 실행과 동시에 띄우지 않는다.

```powershell
Set-Location 'C:/Users/USER/.codex/worktrees/service-integration/me_nu'
& ./frontend/out/actual-carousel-preview/stop.ps1
& ./frontend/out/actual-carousel-preview/build.ps1 -RealOAuth
& ./frontend/out/actual-carousel-preview/start.ps1 -RealOAuth
```

이 모드는 기존 backend 로컬 설정의 Kakao·Naver client ID/secret 네 값만 API 자식 프로세스 메모리로 읽는다. 원본 `.env`는 수정하지 않으며 키·토큰을 문서나 결과물에 기록하지 않는다. 기존 등록 callback 주소를 유지하기 위해 8000과 8211에서 같은 앱·DB·세션을 사용하는 API를 실행하고 두 포트의 PID가 같은지 확인한다. **실제 OAuth 모드의 프론트 API 주소는 8000이고, 기본 모드만 8211이다.** 첫 Kakao 진입 때 발견한 프론트 API·callback 주소 불일치의 차단 검사는 유지했으며, 런타임의 빌드 주소를 맞췄다. 시작 성공이나 로그인 화면 표시가 실제 공급자 인증 성공을 뜻하지는 않는다.

`build.ps1`은 3331 서버가 실행 중이면 빌드 교체를 거절한다. 성공한 build ID·API 주소·모드를 `build-profile.json`에 기록하며, `start.ps1`은 현재 build ID·요청 모드와 기록이 다르면 시작을 거절한다. 실제 OAuth 모드에는 이 빌드 기록이 필수다. 모드를 바꿀 때는 이전 빌드를 그대로 사용하지 말고 위의 종료·빌드·시작 순서를 따른다. 종료만 할 때는 `stop.ps1`을 실행한다.

`start.ps1`은 DB 준비를 재실행해도 기존 작품·사용자를 중복 생성하지 않는다. 실제 콘텐츠는 생성하거나 삭제하지 않는다. API와 Next를 숨긴 독립 프로세스로 실행하며 health 확인 후 계속 유지한다. 이미 8211/3331 포트(실제 OAuth 모드에서는 8000도 포함)를 사용 중이면 해당 프로세스를 건드리지 않고 중단한다.

`stop.ps1`은 기록된 시작 시각·실행 파일과 일치하는 API 서버·실행기·Next PID만 종료하고 포트 해제를 확인한다. 기록된 실행이 실제 OAuth 모드이면 8000 해제도 확인한다. 해당 라벨을 가진 전용 DB 컨테이너만 정지하며 데이터는 보존한다. 다음 `start.ps1`으로 다시 시작할 수 있다. Docker Desktop이 꺼지거나 컴퓨터가 재시작되면 수동으로 시작해야 한다.

런타임 파일은 통합 작업 공간의 Git에서 제외된 `frontend/out/actual-carousel-preview/`에 있다. 후보에는 이 폴더가 없다.

- `processes.json`: 실제 API/실행기/FE PID와 시작 시각, 진입 URL, `realOAuth` 실행 모드
- `build-profile.json`, `full-flow-build.log`: 모드별 build ID·API 주소 및 로컬 빌드 결과
- `api.*.log`, `frontend.*.log`: 서버 로그
- `prepared.json`, `browser-result.json`, `empty-home.png`: 준비·브라우저 확인 결과
- `test-fixtures/import-report.json`: 테스트 3개 적재·동일 입력 재실행 결과
- `carousel-browser-results.json`: 테스트 캐러셀의 후속 브라우저 확인 결과
- `design-restoration-browser-results.json`, `design-restored-*.png`: 기존 디자인 복원 후 최종 확인 결과·viewport 화면
- `pending-flow-browser-results.json`, `pending-flow-db-results.json`, `pending-flow-*.png`: 보조 가입 전 세션부터 실제 서비스 가입·로그아웃까지의 브라우저·DB 결과. 실제 공급자 인증은 제외
- `actual-oauth-browser-results.json`: 사용자가 직접 Kakao 인증·온보딩을 완료한 뒤 실제 세션·DB·서비스 UI를 확인한 결과
- `runtime.json`, `postgres.env`: 전용 DB 비밀값. 출력·공유·커밋하지 않는다.

공식 기사 5개 후보와 그중 3개 페이지 수집 결과도 같은 런타임 폴더에 있다. `source-candidates.json`은 후보 기록이며 `source-captures/`는 `unverified_capture` 원본과 요약이다. 기사 수집, 테스트 데이터 적재, 실제 콘텐츠 생성·검수·승인은 서로 다른 단계다. 현재 기사 수집 결과를 실제 캐러셀로 변환하거나 승인하지 않았다.

최초 빈 피드 확인 기록: 시작·종료·재시작과 DB/API/FE health 확인 완료. 실제 Chrome에서 진입 링크의 홈 이동, HttpOnly 8시간 세션, 게시물 0개 빈 피드, 새로고침 후 로그인 유지가 통과했고 페이지 오류는 0개였다. 이 기록은 테스트 캐러셀 적재 전의 결과다. 서버는 사용자 확인을 위해 실행 상태로 유지한다.

테스트 적재 확인: `carousel_preview`에 콘텐츠 3개·슬라이드 18개를 생성했고, 같은 입력을 다시 적재하면 변경 없이 3개를 유지했다. 실제 에이전트 생성이나 LLM 검수 결과가 아니며 모델 호출은 0회다. 테스트 공개 만료 시각은 최초 생성 때 고정한 `2026-11-26T18:30:51+00:00`으로, 재실행으로 연장하지 않는다.

복원 전 테스트 UI의 화면 확인: Chrome **390×844·1280×900**에서 테스트 3개 표시, 보호 PNG와 `private/no-store`, 키보드 탐색, 모바일 터치, 긴 카드 문구의 세로 스크롤, 상세 독립 본문·테스트 출처 링크, 북마크의 새로고침 후 유지·저장 목록 표지, 뒤로 이동 후 위치 복원을 확인했다. 위치 복원 오차는 1px 미만이며 페이지 오류는 0개다. 저장 목록 표지는 PNG 썸네일을 사용하고 홈의 HTML 글자를 이미지에 합성하지 않는다. 결과는 `carousel-browser-results.json`, 화면은 `home/detail/detail-body/saved-{390,1280}.png`에 남겼다. 실제 OAuth·신규 온보딩 통과를 의미하지 않는다.

## 기존 디자인 복원 상태

홈은 원래 `327:408` 카드 안의 우측 세로 반응·`자세히`·하단 pagination 구성으로 복원했다. 홈에서 3장·5장·10장을 넘길 수 있고 API 텍스트와 보호 PNG를 유지한다. 별도 카드 아래 metadata·가로 반응 행·추가 제목·흰 요약 상자는 제거했다.

상세는 첫 장 표지 한 개와 바로 이어지는 흰 본문을 표시한다. 상세 안의 이전·다음·장수·점 표시와 별도 큰 제목은 보이지 않으며, 출처·게시일·AI 고지는 본문 하단에 유지한다. API에는 전체 슬라이드 배열이 그대로 있다. 상세 구현·원본 커밋 기준은 [프론트 복원 기록](FRONTEND_CAROUSEL_INTEGRATION_2026-10-28.md#5-api를-유지한-기존-디자인-복원--2026-10-28)을 따른다.

복원 단계의 최종 브라우저 확인은 Chrome **390×844·1280×900 모두 통과**, 페이지 오류 0개다. 3장·5장·10장의 비율과 버튼 표시·세로 반응·장수/점/텍스트/자세히 겹침 없음, 상세 단일 표지와 흰 본문 연결·하단 고지, 보호 PNG·키보드/모바일 터치·긴 문구 스크롤·출처 링크·북마크 유지·저장 표지·홈 복귀 오차 1px 미만을 확인했다. 결과는 `design-restoration-browser-results.json`이며 기존 결과를 덮어쓰지 않았다. 이 복원 단계에서는 실제 OAuth·신규 온보딩·실제 에이전트 생성을 검증하지 않았다.

후속 보조 가입 E2E에서는 390px의 작품/태그 1/1, 1280px의 5/12 저장, 여섯 번째 작품 선택 차단, 작품 변경 시 태그 초기화, 소비 유형 단일 선택, 가입 전→활성 세션 회전, 홈·상세·저장·새로고침, 실제 로그아웃과 보호 데이터·blob URL 정리를 확인했다. 두 테스트 사용자의 DB 생년월일은 null, 로그아웃 후 활성 세션은 0개이며 페이지 오류도 0개다. 통합 작업 공간의 [브라우저 결과](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/pending-flow-browser-results.json)와 [DB 결과](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/pending-flow-db-results.json)는 `providerLoginTested: false`를 명시한다. 이 보조 결과와 별도로 실제 Kakao 인증·온보딩 완료 후 서비스 흐름도 통과했으며 [실제 OAuth 결과](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/actual-oauth-browser-results.json)에 구분해 남겼다.

## 사용자 화면 확인 순서

1. 진입 링크로 홈을 열고, 표시된 콘텐츠가 화면 확인용 테스트 자료임을 확인한다.
2. 홈의 3장·5장·10장 캐러셀을 넘겨 첫 장과 마지막 장, 제목·본문, 이미지 배치와 카드 안의 반응·`자세히`·점 표시가 겹치지 않는지 확인한다. 이전·다음 버튼은 hover 또는 키보드 focus 시 나타난다.
3. `자세히`로 첫 표지와 바로 이어지는 흰 독립 본문을 열고, 본문 하단의 출처·고지·게시일을 확인한다. 상세에서는 슬라이드를 넘기지 않는다. 뒤로 돌아와 홈의 위치와 슬라이드 상태를 확인한다.
4. 북마크를 켜고 저장 목록·상세·홈에서 같은 상태인지 확인한다. 새로고침 후에도 저장 상태가 남는지 확인한 뒤 해제한다.
5. 로그아웃 후 보호 화면이 로그인으로 이동하는지 확인한다. 다시 확인하려면 로컬 진입 링크를 연다.

실제 Kakao 로그인부터 온보딩·서비스 이용·로그아웃까지의 결과는 [서비스 전체 흐름 검증](SERVICE_FLOW_E2E_2026-10-28.md)에 기록한다. 사용자가 로그인과 온보딩을 직접 완료했고, 이후 세션·DB·UI를 확인했다. 가입 순서는 **실제 로그인 → 약관·닉네임 → 작품 1~5개 → 선택 작품의 장르·소재 1개 이상 → 소비 유형 1개 → 관심사 저장 → 피드 → 상세 → 북마크 → 로그아웃**이다. `/__preview__/enter` helper와 별도 pending-session 검증은 실제 OAuth 결과와 구분한다. 생년월일 입력·전송은 계속 보류하며 Naver 실제 인증은 이번에 검증하지 않았다. 로컬 `backend-labs` 반영과 원격 push·배포는 별개의 단계다.
