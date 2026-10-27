# 서비스 전체 흐름 검증 — 2026-10-28

이 보고서는 실제 소셜 로그인, 가입·온보딩, 피드·상세·북마크·로그아웃의 검증 범위를 구분한다. **통합 작업 공간에서 실제 Kakao 로그인·callback·V2 온보딩·서비스 이용·로그아웃·보호 화면 재접근 차단까지 통과했다.** Naver는 실제 로그인 폼 도달까지만 확인했다. 별도로 서비스 파일 136개를 `codex/backend-labs-service` 후보에 선별했으며, 로컬 `backend-labs` 반영은 후보 검증 후 진행할 단계다. 통합 작업 공간의 E2E와 후보 재검증을 혼동하지 않는다.

## 1. 현재 확인 상태

| 구분 | 상태 | 확인 범위·제한 |
| --- | --- | --- |
| 통합 작업 공간 프론트 단위 테스트 | 통과 | 297개 통과, 실패 0개. 로그아웃 연결 수정 후 결과 |
| 통합 작업 공간 프론트 타입 검사 | 통과 | 최종 E2E 실행 코드 기준 |
| 통합 작업 공간 프론트 production build | 통과 | `--webpack` 빌드 성공. 실제 OAuth 모드는 API 8000으로 재빌드 완료 |
| 실제 OAuth 모드 서버 재시작 | 통과 | API·프론트 startup health, 3331 실행, 8000·8211 동일 API PID, API 8000·실제 OAuth 모드 빌드 기록 일치 확인 |
| 통합 작업 공간 백엔드 기본 테스트 | 통과 | 937개 통과, 최초 실행에서 17개 skip, 경고 1개 |
| 통합 작업 공간 별도 PostgreSQL 테스트 | 통과 | 전용 DB 2개에서 제외되었던 14개 통과, 경고 1개. 기본 실행과 합쳐 서로 다른 테스트 951개 통과 |
| 서비스 후보 백엔드 재검증 | 통과 | 기본 935개 통과·15개 skip·경고 1개, 별도 PostgreSQL 12개 통과·경고 1개. 서로 다른 947개 통과. d2 삭제 전용 테스트 4개를 후보 범위에서 제외한 차이 |
| 서비스 후보 스키마 문서 | 통과 | f4 단일 head로 ORM DDL 재생성 및 exporter `--check` 통과 |
| 서비스 후보 프론트 재검증 | 통과 | 297개 통과, 타입 검사·`--webpack` 빌드 성공. 빌드의 `next-env.d.ts` 생성 잡음은 복원 |
| 검증 코드·원본 보존 대조 | 통과 | 후보 앱 코드 64개가 실제 E2E를 실행한 통합 코드와 동일. 원본 dirty 37개 파일의 SHA256 일치 |
| 남은 백엔드 미실행 테스트 | 미실행 | Redis 2개·Windows 관련 1개. 통과 수에 포함하지 않음 |
| Kakao 실제 공급자 인증·가입 | 통과 | 사용자 본인 로그인 후 callback·세션 생성 및 V2 온보딩 완료. 작품 1개·태그 2개·소비 유형 `ost` 저장 확인 |
| Naver 실제 공급자 인증 | 폼 진입만 확인 | 실제 버튼에서 `nid.naver.com`의 me-nu 로그인 폼까지 정상 이동. 실제 인증·callback·토큰 교환은 이번에 미검증 |
| 보조 pending-session 신규 가입 E2E | 통과 | 390px·1280px에서 가입 전 세션부터 실제 서비스 API의 가입·로그아웃까지 확인. 실제 공급자 인증은 제외 |
| 실제 Kakao 이후 서비스 흐름 | 통과 | 홈·새로고침·상세 본문/보호 PNG·저장·새로고침·상세·저장 해제·빈 저장 목록·MY 로그아웃·홈 직접 접근 차단, 브라우저 오류 0개 |
| 기존 테스트 사용자 로그아웃 UI | 통과 | 실제 인앱 브라우저에서 새 로그아웃 UI를 눌러 `/pivot/login` 이동 확인. 신규 가입 전체 E2E와 별도 |
| 기존 캐러셀 디자인 복원 검증 | 이전 단계 통과 | Chrome 390×844·1280×900, 페이지 오류 0개. 실제 OAuth·신규 온보딩은 이때 검증하지 않음 |
| `backend-labs` 로컬 반영 | 후보 준비 중 | 실제 Kakao E2E 완료. 서비스 후보 검증·원본 dirty 보존 후 fast-forward 예정이며 아직 실행하지 않음 |

이번 점검에서 프론트 로그아웃 버튼과 실제 세션 종료의 연결 누락을 발견해 수정했다. 기존 미리보기 사용자와 보조 가입 전 세션의 테스트 사용자에서 로그아웃을 확인한 뒤, 실제 Kakao로 가입한 세션에서도 MY 로그아웃을 검증했다. DB 활성 세션은 로그아웃 전 1개에서 이후 0개로 바뀌었고, 로그인 화면 이동과 홈 직접 재접근 차단을 확인했다. 이메일·회원 식별자·토큰·키는 이 보고서에 기록하지 않는다.

Kakao와 Naver 모두 실제 로그인 버튼에서 공급자 폼으로 이동한 뒤, Kakao는 사용자가 직접 인증해 callback·가입·서비스 검증까지 완료했다. Naver 폼 도달은 인증 완료로 처리하지 않는다. 처음의 Kakao 프론트 API 8211·callback 8000 불일치는 보안 검사가 정상 차단했고, 해당 검사를 완화하지 않은 채 실제 OAuth 빌드를 API 8000으로 맞췄다. 최종 로그아웃·접근 차단 확인 후 인앱 브라우저는 로그인 화면에 있으며 로컬 서버는 유지한다.

## 2. 로컬 실행 경계

기존 [로컬 캐러셀 미리보기](LOCAL_CAROUSEL_PREVIEW_2026-10-28.md)의 격리 DB와 화면 확인용 콘텐츠를 사용한다. 실행 중인 서버와 ignored 결과·이미지·helper는 **`C:/Users/USER/.codex/worktrees/service-integration/me_nu` 통합 작업 공간**의 자료다. 새 후보 `C:/Users/USER/.codex/worktrees/backend-labs-service/me_nu`에는 이 런타임 폴더를 복사하지 않았다. 실제 작품 카탈로그는 30개이며, 캐러셀은 `[테스트]` 표시가 있는 합성 자료 3개·총 18장이다. 실제 에이전트 생성 콘텐츠와 실제 LLM 검수 완료 콘텐츠는 모두 0개다.

| 항목 | 값 |
| --- | --- |
| 프론트 | `http://localhost:3331` |
| 기본 모드 프론트 API | `http://localhost:8211` |
| 실제 OAuth 모드 프론트 API·callback | `http://localhost:8000` — `-RealOAuth` 실행 때 활성 |
| 실제 OAuth 모드 보조 API | `http://localhost:8211` — 8000과 같은 앱·DB·세션 |
| 미리보기 DB | `127.0.0.1:63362` / `carousel_preview` |
| 미리보기 컨테이너 | `me-nu-carousel-preview-20261028` |
| 미리보기 스키마 | `f4b8c1d9a620` |

`frontend/out/actual-carousel-preview/start.ps1 -RealOAuth`는 기존 backend 로컬 설정의 Kakao·Naver client ID/secret 네 값만 API 자식 프로세스 메모리로 읽는다. 이 보고서에는 설정값·토큰·개인정보를 포함하지 않는다. 원본 `.env`를 수정하지 않으며, 개발용 인증 우회와 개발 API는 비활성, LLM 관련 호출도 비활성이다.

실제 OAuth 모드에서는 8211과 기존 등록 callback 주소인 8000을 같은 API 프로세스·앱·DB·세션으로 실행한다. **이 모드의 프론트 API 주소도 8000으로 빌드한다.** 첫 Kakao 진입에서 8211·8000의 불일치를 차단한 제품 보안 검사는 유지했으며, ignored 런타임의 빌드 설정을 맞췄다. 시작 스크립트는 두 API 포트의 PID 일치를 확인한다. 기본 실행에는 OAuth를 활성화하지 않고 프론트 API 8211을 사용하며, 온보딩을 마친 전용 테스트 사용자로 들어가는 `/__preview__/enter` 보조 경로를 사용한다.

실제 OAuth 실행 순서는 `stop.ps1` → `build.ps1 -RealOAuth` → `start.ps1 -RealOAuth`다. 기본 모드로 돌아갈 때는 build와 start 양쪽의 스위치를 모두 생략한다. `build-profile.json`은 build ID·API 주소·실행 모드를 기록하며, start는 현재 build ID와 요청한 모드가 기록과 다르면 시작을 거절한다. 자세한 명령은 [로컬 실행 문서](LOCAL_CAROUSEL_PREVIEW_2026-10-28.md#시작종료)를 따른다.

실행 스크립트·로그·브라우저 산출물은 Git에서 제외된 로컬 준비물이다. 운영 DB 마이그레이션, 외부 배포, main 반영은 이 검증에 포함하지 않는다. 기존 PostgreSQL 5433·Redis 6379도 변경하지 않는다.

## 3. 검증 증거를 구분하는 방법

**실제 공급자 인증**은 로그인 화면에서 해당 공급자 인증으로 이동하고, 실제 callback을 거쳐 서비스의 적절한 가입 또는 활성 세션으로 돌아온 결과를 말한다. 공급자별 성공·실패를 각각 기록한다. 인증 시작 URL이나 HTTP health 성공만으로 로그인 성공으로 처리하지 않는다.

**보조 pending-session 가입 E2E**는 로컬 테스트 계정에 공급자 인증 직후와 같은 가입 전 세션을 준비해 서비스 내부 가입 흐름을 검증한다. 실제 OAuth 성공과 별도다. 약관·닉네임 입력부터 작품 1~5개, 그 작품의 장르·소재 1개 이상, 기존 소비 유형 중 1개를 선택하고 관심사·온보딩 완료 상태를 저장하는 구간을 확인했다. 생년월일은 입력·전송하지 않았고 두 테스트 사용자의 DB 값은 null이다. 기존 DB 생년월일 열·데이터를 삭제하는 결정을 포함하지 않는다.

**이미 온보딩한 테스트 사용자 보조 경로**는 피드 이후의 화면 검증에만 사용한다. 이 경로에서 캐러셀·상세·북마크를 확인했더라도 신규 가입이나 실제 OAuth를 통과했다고 기록하지 않는다.

**단위 테스트와 PostgreSQL 검증**은 브라우저 결과와 별도다. 단위 테스트는 입력·상태 전이·오류 처리 등의 계약을 검증하고, 격리 PostgreSQL 테스트는 실제 DB 제약·마이그레이션·트랜잭션을 검증한다. 테스트 실행 중 제외된 항목이나 경고는 결과에 남긴다.

## 4. 전체 흐름 확인 항목

| 순서 | 확인 내용 | 현재 결과 |
| --- | --- | --- |
| 1 | 실제 공급자 인증 시작·callback·가입 전 또는 활성 세션 생성 | Kakao 통과. Naver는 로그인 폼 도달만 확인 |
| 2 | 약관·닉네임 제출, 생년월일 미입력·미전송 | 보조 세션에서 통과. DB 생년월일 null |
| 3 | 작품 최소 1개·최대 5개 선택과 검색 | 보조 세션에서 1개·5개 저장 통과. 여섯 번째 선택 차단 |
| 4 | 선택한 작품의 장르·소재 칩 1개 이상 선택 | 보조 세션에서 1개·12개 저장 통과. Q1 작품 변경 시 기존 태그 초기화 |
| 5 | 기존 소비 유형 선택지 중 1개 선택, 관심사·완료 상태 저장 | 보조 세션에서 단일 선택·schemaVersion 2 저장 통과 |
| 6 | 피드 진입과 새로고침 후 세션·관심사 유지 | 보조 세션에서 통과. 가입 전 세션→활성 세션 회전 확인 |
| 7 | 홈 3장·5장·10장, 보호 PNG, 상세 단일 표지·독립 본문·출처 | 기존 복원 검증 통과. 신규 보조 가입 사용자도 홈·상세 접근 통과 |
| 8 | 북마크 저장·해제와 홈·상세·저장 화면·새로고침 정합성 | 보조 세션에서 통과 |
| 9 | 실제 로그아웃 요청·세션 무효화·보호 화면 재진입 차단 | 보조 세션에서 통과. 두 사용자 활성 세션 0개·보호 데이터/blob 정리·로그인 이동 |

보조 가입 E2E는 390px·1280px 모두 통과했고 페이지 오류는 각각 0개다. 390px에서는 작품 1개·태그 1개, 1280px에서는 작품 5개·태그 12개를 저장했다. 두 경우 모두 소비 유형 `world`, `schemaVersion: 2`를 확인했다. 브라우저와 DB 결과 모두 **`providerLoginTested: false`**를 명시한다.

검증 산출물은 Git에서 제외된 로컬 미리보기 폴더에 있으며 저장소 커밋에 포함하지 않는다.

- 통합 작업 공간의 [보조 가입 브라우저 결과](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/pending-flow-browser-results.json)·[DB 확인 결과](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/pending-flow-db-results.json)
- 모바일 [가입 화면](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/pending-flow-profile-390.png)·[홈](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/pending-flow-home-390.png)·[로그아웃 후](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/pending-flow-logged-out-390.png)
- 데스크톱 [가입 화면](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/pending-flow-profile-1280.png)·[홈](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/pending-flow-home-1280.png)·[로그아웃 후](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/pending-flow-logged-out-1280.png)

같은 폴더의 `pending-flow-{result,detail,saved}-{390,1280}.png`에도 가입 완료·상세·저장 화면을 남겼다. 기존 디자인 복원 당시 캐러셀·상세·북마크의 모바일·데스크톱 결과는 [프론트 통합 기록](FRONTEND_CAROUSEL_INTEGRATION_2026-10-28.md)에 보존했다.

실제 Kakao 로그인과 V2 온보딩 화면 조작은 사용자가 직접 수행했다. 완료 후 DB의 작품 1개·태그 2개·소비 유형 `ost`와 활성 세션을 확인하고, 홈 새로고침 → 보호 PNG 3장이 있는 상세와 독립 본문 → 저장·새로고침·상세 → 저장 해제·빈 저장 목록 → MY 로그아웃 → 로그인 화면 → 홈 직접 재접근 차단을 확인했다. 브라우저 오류는 0개, DB 활성 세션은 1개에서 0개로 변경됐다. [실제 OAuth 검증 요약](C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview/actual-oauth-browser-results.json)과 [저장 화면](C:/Users/USER/.codex/visualizations/2026/10/27/01a17cdd-1dde-7ab0-bd2b-246c9011eb97/actual-oauth/saved.png)·[로그아웃 화면](C:/Users/USER/.codex/visualizations/2026/10/27/01a17cdd-1dde-7ab0-bd2b-246c9011eb97/actual-oauth/logged-out.png)은 후보 저장소 외부의 로컬 증거다.

## 5. `backend-labs` 반영 전 읽기 감사

아래 수치는 반영 준비 당시의 스냅샷이며, 실행 직전에 원격 SHA와 작업 상태를 다시 확인해야 한다. 감사에서는 commit·branch 생성·merge·push·원본 작업 파일 변경을 하지 않았다.

| 항목 | 감사 결과 |
| --- | --- |
| 원본 작업 공간 | `C:/Users/USER/me_nu`, `backend-labs` |
| 로컬·원격 `backend-labs` | `407039433efc0be1eb13d574f5ee7f69c5d9eb33` — GitHub connector 비교 결과 identical, ahead/behind 0 |
| 통합 작업 공간 | `codex/service-integration`, HEAD `e39a1942d2d8ef394283972cfee59e037efbc0bc` |
| 원본 미커밋 변경 | 37개 경로: 추적 파일 28개·미추적 9개 |
| 두 HEAD의 커밋 차이 | 통합이 12개 커밋 앞섬. 변경 146개 파일 중 145개가 에이전트 영역, 나머지 1개는 PR #11 되돌리기 기록 |
| 커밋된 서비스 트리 | `backend`, `frontend`, compose·deploy 경로는 두 HEAD에서 동일. 이번 서비스 구현은 미커밋 변경에 있음 |

따라서 통합 HEAD 전체를 `backend-labs`로 머지하면 이번 서비스 변경을 옮기는 대신 에이전트 원본 복원 이력이 들어갈 수 있다. 전체 E2E를 통과한 변경을 고정한 뒤, 깨끗한 `backend-labs` 기준 작업 공간에서 서비스 파일·수정 구간을 명시적으로 선별해야 한다. 원본의 미커밋 37개 경로를 덮어쓰거나 `git add .`로 함께 반영하지 않는다. 에이전트 영역, ignored 런타임·비밀값·테스트 산출물, 빌드가 바꾼 `next-env.d.ts` 같은 생성 잡음은 반영 대상과 분리한다.

### 생년월일 호환 주의

원본과 통합 작업 공간에는 이번 작업 이전의 생년월일 제거 변경도 남아 있다. 미추적 migration `d2f6a9c4e731_remove_user_birth_date.py`는 실제 열을 삭제한다. 이번 서비스 스키마 `e3`→`f4`는 `c0e5f3b8a124`에서 별도로 이어지므로 `d2`를 필수로 적용할 필요는 없다.

다만 `backend-labs` 기준 온보딩 API에는 필수 `birth_date`와 연령 검사·저장 처리가 있고, 최신 Pivot 프론트는 닉네임만 제출한다. 생년월일 관련 수정 전체를 무조건 제외하면 신규 온보딩이 422로 실패할 수 있다. **현재 요구인 입력·전송 보류를 지키면서 기존 DB 열·데이터는 보존하는 범위**와, 과거 물리적 삭제 변경을 분리해야 한다. `d2`를 제외할 때는 migration heads를 두 개로 기대하거나 `d2`를 적용하는 기존 테스트도 최종 후보에 맞게 정리하고 다시 검증해야 한다. 최종 반영 후보가 검증한 통합 작업 공간과 달라지면 해당 후보를 재검증한다.

### push와 배포의 경계

저장소의 `backend-labs`에는 `.github/workflows`가 없고, 백엔드 labs 스크립트는 명시적으로 실행하는 방식이었다. 그러나 기존 환경 기록에는 `backend-labs`의 Vercel Preview 도메인 `labs.me-nu.kr`가 있으며, GitHub의 해당 SHA 상태에도 Vercel 성공 기록이 확인됐다. 현재 Vercel의 자동 배포 설정을 직접 재조회한 것은 아니므로 자동 배포가 반드시 발생한다고 단정하지 않지만, **프론트 변경을 포함한 `backend-labs` push를 배포와 무관한 작업으로 간주할 수 없다.**

실제 Kakao를 포함한 전체 흐름 확인을 마친 뒤 별도 서비스 후보를 준비했다. 로컬 `backend-labs` 반영은 아래 후보 검증·원본 보존 절차가 끝난 후 진행할 단계이며, 원격 push·main merge·배포는 계속 금지한다. 실행 중인 로컬 미리보기 서버는 통합 작업 공간의 소스를 그대로 사용한다.

## 6. 서비스만 선별한 반영 후보

후보는 `C:/Users/USER/.codex/worktrees/backend-labs-service/me_nu`, 브랜치 `codex/backend-labs-service`이며 기준은 위 `backend-labs`의 `4070394`다. 명시한 서비스 경로 136개만 복사했고, 에이전트 코드 145개와 생년월일 삭제 d2 파일, 생성 잡음 `frontend/next-env.d.ts`, ignored 런타임·비밀값·PNG·테스트 결과는 복사하지 않았다. 후보는 f4 단일 migration head이며 기존 생년월일 컬럼·데이터 보존 검증을 유지한다.

d2 삭제 전용 단위 테스트 2개와 PostgreSQL `birth_removed=True` 경로 2개를 후보 범위에서 제외하고, 기본 935개와 격리 PostgreSQL 12개를 합쳐 서로 다른 947개 테스트가 통과했다. 생년월일 기존 값의 upgrade·downgrade 보존 검증은 유지했다. 후보 ORM 스키마를 재생성하고 exporter `--check`도 통과했다. Redis 2개·Windows symlink 1개는 여전히 미실행이다. 후보 프론트는 297개 테스트·타입 검사·`--webpack` 빌드를 통과했으며, 실제 통합 E2E와 후보의 앱 코드 64개가 동일함을 해시로 대조했다.

원본 `C:/Users/USER/me_nu`의 dirty 37개 파일은 현시점 해시가 모두 동일하다. 같은 `4070394` 기준 `codex/backend-labs-preserved-20261028` 보존 브랜치로 전환한 뒤 바이트·상태 보존을 다시 확인할 예정이다. 이 준비가 끝나기 전 원본이 사용하는 `backend-labs` 참조를 강제로 옮기지 않는다. 후보 자체 검증과 원본 보존이 완료되면 깨끗한 작업 공간에서 로컬 `backend-labs`만 fast-forward할 예정이다. **현재는 후보 검증 완료·로컬 반영 전 단계이며 반영 완료를 기록하지 않는다.** 원격 push는 Vercel 배포 위험 때문에 수행하지 않는다.
