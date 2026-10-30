# FE–BE 통합 테스트 인계 — 2026-10-27

목표는 `Home → 콘텐츠 상세 → 북마크 저장 → Saved 조회 → 저장 취소`가 실제 FastAPI와 테스트 DB를 거쳐 동작하는지 확인하는 것이다. 추천·Agent 완성을 전제로 하지 않는다. 이 문서의 확인 대기 항목은 실제 적용·검증 후 갱신한다.

코드 확인 기준은 BE `backend-labs@9afe3ddb521779bdf59e611a0db5189d70d69ded`, FE `feature/pivot-user-fe@d44f79023e8b36fc2a60cac8f8668d7fddc20748`이다. 배포된 코드와 FE 브랜치의 최신 커밋은 통합 테스트 직전에 다시 확인한다. 이번 준비 범위에는 Production과 `main` 변경이 포함되지 않는다.

## 1. 주소와 적용 상태

| 항목 | 주소·값 | 현재 상태 |
| --- | --- | --- |
| BE 테스트 origin | `https://api.test.me-nu.kr` | 기존 스테이징 주소 |
| API prefix | `https://api.test.me-nu.kr/api/v1` | FE 요청 경로에 `/api/v1` 포함 |
| Swagger | `https://api.test.me-nu.kr/docs` | 테스트 인증·API 계약 확인용 |
| OpenAPI | `https://api.test.me-nu.kr/openapi.json` | 실제 배포 명세와 대조 |
| FE 테스트 origin | `https://test.me-nu.kr` | Gabia CNAME 추가 및 Vercel `Valid Configuration` 확인. `feature/pivot-user-fe` Preview의 HTTPS `/pivot/login` 화면 접근 확인 |
| FE API 환경변수 | `NEXT_PUBLIC_API_BASE_URL=https://api.test.me-nu.kr` | 앞선 적용·재배포에서 확인한 값. 이번 Vercel 재확인에서는 FE 브랜치 전용 변수의 존재·범위를 확인했으며 값을 다시 표시하거나 변경하지 않음. 값에 `/api/v1`을 중복해서 붙이지 않음 |
| FE 테스트 API flag | `NEXT_PUBLIC_PIVOT_ENABLE_TEST_API=true` | 사용자 승인 후 `feature/pivot-user-fe`의 Preview에만 저장 완료. 새 빌드 반영 대기 |
| FE 로컬 API flag | `NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API=false` | 사용자 승인 후 `feature/pivot-user-fe`의 Preview에만 저장 완료. 새 빌드 반영 대기 |
| BE CORS | `FRONTEND_ORIGINS`에 `https://test.me-nu.kr` 추가 | 설정 적용 및 공개 HTTPS preflight 검증 완료. 기존 `https://api.test.me-nu.kr`, `https://me-nu.kr` 항목 유지 |
| OAuth FE 복귀 origin | `OAUTH_FRONTEND_ORIGIN=https://test.me-nu.kr` | 스테이징 적용 완료. 완료 회원은 `/pivot/home`, 온보딩 대기는 `/pivot/consent`, 오류는 `/pivot/login?error=...`로 복귀. 기존 온보딩 완료 계정의 실제 카카오·네이버 로그인·홈 복귀 확인 완료; FE adapter 연결은 후속 |
| 고정 테스트 콘텐츠 | 테스트 DB에 2개 적재 | `topicId=1`, `subtopicId=1`, `contentId=1,2`. 일회성 합성 사용자로 BE 피드·상세·저장 흐름 검증 완료 |

스테이징에서 확인된 인증 설정은 쿠키 `menu_staging_session`, `Secure=true`, `HttpOnly=true`, `SameSite=Lax`, Redis 접두사 `me_nu_staging`이다. `ENABLE_DEV_API=false`, `ENABLE_DEV_AUTH_BYPASS=false`를 유지한다. 개발용 로그인·인증 우회를 원격 테스트의 해결책으로 켜지 않는다. 계정·세션·OAuth 시크릿은 이 문서에 기록하지 않는다.

CORS와 OAuth FE 복귀 origin 변경은 기존 API 이미지를 재사용해 적용했고 API 컨테이너만 재생성했다. API의 healthy 상태 및 공개 HTTPS의 API·DB·Redis health `200`을 확인했다. DB·Redis 컨테이너와 공급자에 등록하는 BE callback URL은 변경하지 않았다.

2026-10-27 09:52:59 KST Gabia에서 `test`의 CNAME을 `1b4d5e1d5141e60c.vercel-dns-017.com`으로 추가했다(TTL 600). 기존 `api.test` A 레코드 `52.78.2.187`은 유지했다. Vercel의 도메인 연결과 HTTPS 로그인 화면 표시까지 확인했으며, 실제 FE OAuth·API adapter 연결 완료를 의미하지는 않는다.

팀 로그인 없는 브라우저에서 기본 `test.me-nu.kr` 접근은 Vercel 인증을 요구했다. 사용자 승인 후 이 도메인의 공유 범위를 `Anyone with the link`로 설정하고, 생성된 공유 링크로 별도 비로그인 브라우저의 `/pivot/login` 화면 접근을 확인했다. 공유 토큰을 포함한 전체 링크는 채팅으로 전달하고 이 문서에는 저장하지 않는다. 당시 Hobby 제한에 따라 이전 `d5e6384` 데모 공유 링크와 중간에 생성했던 `vercel.app` 링크는 교체됐다. 이후 재확인 화면에는 팀이 Pro로 표시되지만 이번 작업에서 플랜을 변경하지 않았다. 프로젝트 전체 배포 보호는 유지한다.

`test.me-nu.kr`은 FE 브랜치를 따라 갱신되는 고정 주소이며 특정 커밋에 영구 고정된 URL은 아니다. 이번 검증 대상은 `d44f790` / Preview `CFDHAUMBDNtagbELVHoStfFsrcHp`다. 이후 FE Push 때도 해당 배포에 대한 공유 접근을 확인한다.

### Vercel 재확인 및 FE flag 저장 — 2026-10-27 13:33 KST 기록 기준

| 점검 | 확인 결과 |
| --- | --- |
| FE 도메인·현재 코드 | `test.me-nu.kr`은 `Valid Configuration`, `feature/pivot-user-fe` 연결. 원격 브랜치와 현재 Preview는 여전히 `d44f790` / `CFDHAUMBDNtagbELVHoStfFsrcHp` |
| FE 담당자 전달 커밋 | `08ed87f`는 이번 재확인 시점에 아직 Push되지 않은 로컬 작업으로 전달됨. 원격 소스·테스트 결과·실제 배포 반영은 검증하지 않음 |
| Git·빌드 설정 | `Por0ri/me-and-nu` 연결, Root Directory `frontend`, 해당 루트의 변경이 없으면 배포 건너뛰기 활성, Ignored Build Step `Automatic` |
| 배포 보호 | Standard Protection의 로그인 요구 활성. 도메인 예외와 Automation Bypass 비밀값은 없음. 테스트 도메인 공유 링크 1개 확인 |
| 추가 별칭 접근 | Vercel 비로그인 브라우저에서 추가 FE 브랜치 별칭과 `backend-labs` 별칭 모두 Vercel 로그인으로 이동 |
| 기존 환경변수 | FE 브랜치 전용 API 주소 변수 및 `PIVOT_DEMO_ENABLED` 존재 확인. API 주소는 앞선 검증 값이 `https://api.test.me-nu.kr`이며 이번에는 다시 표시하지 않음 |
| 이번 저장 범위 | Config 유형으로 `NEXT_PUBLIC_PIVOT_ENABLE_TEST_API=true`, `NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API=false`를 FE 브랜치의 Preview에만 추가. Production은 선택에서 제외했고 기존 API 주소·Demo·`backend-labs` 변수는 수정하지 않음 |
| 저장·적용 상태 | 성공 알림 및 목록의 두 key가 `Preview / feature/pivot-user-fe` 범위로 추가된 것을 확인. Vercel은 새 배포가 필요하다고 안내함. 재배포는 실행하지 않았으며 다음 FE Push로 생성되는 새 빌드에 반영 예정 |
| Production·main | 기존 `main@9f14399` Production 유지. 이번 재확인·flag 추가에서 변경 없음 |

환경변수 저장은 새 FE 코드 배포 또는 API 연결 검증 완료를 의미하지 않는다. `08ed87f` Push 후 새 Preview의 커밋·빌드 성공·공유 접근을 확인하고, 실제 FE 로그인·세션·Home·상세·북마크·Saved 흐름을 다시 검증한다. 현재 배포에서 새 flag나 로컬 커밋의 기능이 동작한다고 가정하지 않는다.

## 2. 사용할 브라우저 경로

배포 통합 테스트는 `https://test.me-nu.kr`에서 `https://api.test.me-nu.kr`를 호출하는 같은 사이트의 HTTPS 구성을 사용한다. 두 주소는 서로 다른 origin이므로 정확한 FE origin을 CORS에 허용하고, 모든 세션 기반 `fetch`에 `credentials: "include"`를 지정해야 한다. 쿠키는 API 호스트가 발급·관리하며 FE JavaScript가 읽거나 복사하지 않는다.

`http://localhost:3000 → https://api.test.me-nu.kr` 직접 호출은 현재 인증 설계의 지원 경로가 아니다. `SameSite=Lax` 쿠키의 cross-site 전송 제약에 더해, 새 익명 세션 발급 경로는 `Sec-Fetch-Site: cross-site` 요청을 거절한다. CORS에 localhost를 추가하는 것만으로 해결되지 않는다. `*.vercel.app → api.test.me-nu.kr` 직접 호출에도 같은 주의가 필요하다.

로컬 FE를 검증할 때는 별도 로컬 BE와 `http://localhost:3000 → http://localhost:8000` 조합을 사용하고 호스트명을 통일한다. 원격 스테이징과 로컬 테스트의 DB·쿠키·세션 설정을 혼용하지 않는다. 원격 통합 검증은 준비된 같은 사이트 HTTPS 주소로 진행한다.

로컬 OAuth runner `backend/scripts/run_local_oauth.py`에 `--frontend-origin http://localhost:3000` 옵션을 추가했다. 이 옵션은 로컬 FE 복귀 주소와 CORS를 함께 설정하며, 경로·query 없는 `http://localhost:<port>`만 허용한다. 로컬 API callback은 `http://localhost:8000/api/v1/auth/login/{provider}/callback`이다. 로컬 공급자 callback 등록·자격 증명도 별도로 확인해야 한다.

현재 실제 `--check` 실행은 Docker Desktop의 Linux engine이 실행되지 않아 실패했다. 현재 설정의 PostgreSQL `localhost:5433` 및 Redis `127.0.0.1:6379`를 사용할 수 있어야 하며, 로컬 API 기동·브라우저 왕복은 아직 검증하지 못했다. 이번 확인으로 로컬 DB를 변경하지 않았다. runner의 `--prepare`는 전용 DB `me_nu_oauth_local`의 생성·migration만 수행하며 테스트 콘텐츠를 seed하지 않는다. 아래의 콘텐츠 ID `1,2`는 원격 스테이징 데이터이므로 로컬 DB에 있다고 가정하지 않는다.

로컬 DB·Redis를 준비한 뒤 `backend` 디렉터리에서 다음 명령을 사용한다. 첫 명령은 준비 상태만 확인하며, 두 번째 명령은 API를 실행한다. `.env` 파일을 다시 쓰지 않으며 공급자 시크릿을 FE 환경변수로 복사하지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts/run_local_oauth.py --provider all --frontend-origin http://localhost:3000 --check
.\.venv\Scripts\python.exe scripts/run_local_oauth.py --provider all --frontend-origin http://localhost:3000
```

## 3. 로그인·쿠키·CSRF 계약

인증은 실제 카카오 또는 네이버 OAuth를 사용한다. 기존 원격 로그인 검증 기록은 [OAuth 설정·검증 문서](OAUTH_T02_1_SETUP.md)의 2026-10-23 기록을 참고한다. 이 과거 기록은 새 FE 도메인의 연결 성공을 대신하지 않는다. 개인정보를 조회하지 않는 집계로 온보딩 완료·활성 상태이며 애니메이션 Topic을 사용하는 기존 계정 3개를 확인했다. 해당 계정 소유자가 직접 로그인하거나, FE 담당자의 테스트 계정·앱 테스터 등록 상태를 확인해 사용한다. 공용 비밀번호나 인증 우회 로그인은 만들지 않았다.

1. 같은 브라우저에서 `GET /api/v1/auth/session`을 호출한다. 비로그인도 `200`이며 익명 HttpOnly 쿠키와 `csrfToken`을 받는다. 응답을 캐시하지 않고 CSRF는 메모리에만 보관한다.
2. `POST /api/v1/auth/login/kakao` 또는 `POST /api/v1/auth/login/naver`를 호출한다. `credentials: "include"`, `Content-Type: application/json`, `X-CSRF-Token: <현재 세션의 csrfToken>`을 사용하고 본문은 `{"purpose":"signin"}`이다. `reauth`·`restore`는 아직 지원하지 않는다.
3. 응답 `200 {"authorizationUrl":"..."}`의 URL로 **같은 브라우저를 이동**한다. 시작 endpoint를 주소창에서 GET으로 여는 방식이 아니다. FE가 임의 `returnTo` URL을 전달하지 않는다.
4. 공급자는 BE의 `GET /api/v1/auth/login/{provider}/callback`으로 복귀한다. BE가 state·시작 세션을 확인하고 세션 쿠키를 교체한 뒤 `303`으로 이동한다.
5. 현재 스테이징은 `OAUTH_FRONTEND_ORIGIN=https://test.me-nu.kr`가 적용되어 온보딩 대기 회원은 `https://test.me-nu.kr/pivot/consent`, 온보딩 완료 회원은 `/pivot/home`, 실패는 `/pivot/login?error=...`로 이동한다. 복귀 경로 설정 완료와 FE 화면의 인증 처리 완료를 구분한다.
6. 복귀 후 `GET /api/v1/auth/session`을 다시 호출해 **새 CSRF**와 인증 상태를 확인한다. 온보딩 완료·로그인 등 세션 교체 후 이전 CSRF를 재사용하지 않는다.

BE callback 주소는 다음과 같으며 FE 도메인과 구분한다.

- 카카오: `https://api.test.me-nu.kr/api/v1/auth/login/kakao/callback`
- 네이버: `https://api.test.me-nu.kr/api/v1/auth/login/naver/callback`

신규 로그인 세션은 `authenticated: true`, `sessionState: "onboarding_pending"`, `onboardingCompleted: false`, **`user: null`**이다. 완료 세션은 `sessionState: "active"`, `onboardingCompleted: true`, `user.accountType: "user"`이다. 보호된 콘텐츠·북마크 API에는 온보딩 완료한 활성 세션과 해당 사용자의 활성 Topic이 필요하다.

Swagger 단독 검증은 같은 브라우저의 `/docs`에서 세션을 먼저 조회하고, Authorize의 `CsrfToken`에 토큰 값만 입력한 뒤 OAuth 시작 API를 호출한다. `Bearer` 접두어나 HttpOnly 쿠키 수동 입력은 필요 없다. 로그인 후 새 토큰으로 Authorize를 갱신한다. 공급자 로그인을 다른 브라우저 프로필에서 마치면 시작 세션과 일치하지 않는다.

사용자가 Edge에서 직접 카카오 로그인을 완료했고 `https://test.me-nu.kr/pivot/home`으로 복귀했다. 같은 브라우저에서 BE의 `GET /api/v1/auth/session`을 열어 `authenticated:true`, `sessionState:"active"`, `user.accountType:"user"`, `onboardingCompleted:true`를 확인했다. 기존 온보딩 완료 계정의 로그인 검증이며 사용자 ID·CSRF 값은 기록하지 않는다. 홈은 예상대로 콘텐츠 서비스 미연결 안내를 표시했다.

2026-10-27 네이버도 실제 브라우저 로그인을 검증했다. 먼저 앱 로그아웃 `204` 후 세션의 `authenticated:false`, `sessionState:"anonymous"`를 확인하고, Swagger에서 `POST /api/v1/auth/login/naver`를 실행해 `200`과 유효한 네이버 인증 URL을 받았다. 사용자가 같은 Edge에서 직접 로그인한 뒤 `https://test.me-nu.kr/pivot/home`으로 복귀했다. 동일 브라우저의 직접 session 조회에서 `authenticated:true`, `sessionState:"active"`, `user.accountType:"user"`, `onboardingCompleted:true`와 익명 세션 대비 CSRF 교체를 확인했다. 이어 직접 `GET /api/v1/topics/1/feed?limit=20`으로 Topic 1, 콘텐츠 ID `2,1`, 두 고정 테스트 제목, `emptyReason:null`을 확인했다. FE 홈은 콘텐츠 서비스 미연결 안내를 유지했다. 이 검증에는 Production·`main`·환경변수·코드 변경이 없었다.

카카오·네이버 모두 기존 온보딩 완료 계정의 BE OAuth·브라우저 직접 API 조회 검증이다. 신규 사용자 온보딩, FE JavaScript의 인증 포함 API 호출, 실제 브라우저의 북마크 저장·취소는 아직 검증하지 않았다. 공급자 앱의 테스트 사용자 제한이 나타나면 해당 계정의 앱 테스터 등록 여부를 확인하며, 테스트를 위해 실제 사용자 가입 상태를 임의 변경하지 않는다. 개인 계정 ID·이메일·CSRF·자격 증명은 문서에 기록하지 않는다.

## 4. 콘텐츠·북마크의 현행 API

아래 경로에는 공통 prefix `/api/v1`이 포함되어 있다. 요청·응답 JSON은 camelCase이며 `topicId`와 `contentId`는 실제 API/seed 결과의 양의 정수 ID를 사용한다. 이 문서의 자리표시자를 샘플 숫자로 대체해 고정하지 않는다.

| 순서 | 요청 | 본문·query | 확인할 응답 |
| --- | --- | --- | --- |
| 활성 Topic 확인 | `GET /api/v1/me/topics` | 없음 | 본인에게 활성화된 `topics[].id` |
| Home 피드 | `GET /api/v1/topics/{topicId}/feed?limit=20` | 선택: `subtopicId`, `cursor`, `limit` | `200`, `sections[].contents[]`에서 콘텐츠 2개 이상과 각 `id`, `topicId`, `saved` |
| 상세 | `GET /api/v1/contents/{contentId}?topicId={topicId}` | `topicId` query 필수 | `200`, 피드의 `id`와 상세의 `contentId` 동일, 제목·본문·대표 이미지 |
| 북마크 저장 | `PUT /api/v1/contents/{contentId}/bookmark` | JSON `{"topicId": <실제 topicId>}` | `200`, `savedItemId`, `contentId`, `topicId`, `tags`, `resurfaceEnabled`, `savedAt` |
| Saved 목록 | `GET /api/v1/users/me/bookmarks?topicId={topicId}&limit=20` | `topicId`는 선택 필터. 추가 선택: `q`, `cursor`, `limit` | `200`, `items[].contentId`에서 저장한 동일 콘텐츠 확인 |
| 북마크 취소 | `DELETE /api/v1/contents/{contentId}/bookmark?topicId={topicId}` | `topicId` query 필수, JSON body 없음 | `204`, 응답 본문 없음 |
| 취소 반영 확인 | 피드·상세·Saved 다시 GET | 동일 사용자·Topic | 피드 `saved=false`, 상세 `myReaction.bookmarked=false`, Saved에서 제외 |

PUT·DELETE에는 세션 쿠키와 `X-CSRF-Token`이 필요하다. PUT 반복은 중복 저장을 만들지 않고, DELETE 반복은 이미 취소한 경우에도 `204`다. DELETE의 ID는 `savedItemId`가 아니라 **`contentId`**다. UI는 요청 실패를 성공으로 표시하지 않고 서버 결과를 반영해야 한다.

피드는 추천·Agent를 실행해서 생성하는 응답이 아니라 활성 Topic의 공개 콘텐츠를 DB에서 최신순으로 조회한다. 테스트 DB에 고정 콘텐츠를 적재하면 같은 기존 API로 조회·저장 흐름을 검증할 수 있다. 피드의 `recommendationId`·`recommendationReason`이 `null`이어도 이 테스트의 실패 조건은 아니다. 단, 공개 상태·출처·Topic 조건에 맞지 않으면 콘텐츠가 제외될 수 있다.

현재 응답은 대표 `imageUrl` 1개 구조다. `slides[]`와 slide별 제목·설명·`bottom|left|right` 배치는 아직 현행 BE 계약에 없으므로 이번 고정 데이터 준비 완료와 혼동하지 않는다. 여러 장 확장은 별도 계약·구현·검증 대상이다.

| 테스트 데이터 | 실제 값 | 상태 |
| --- | --- | --- |
| 대상 테스트 DB·격리 확인 | `api.test.me-nu.kr` 스테이징 | 테스트 대상 확인 후 실행 완료 |
| Topic·Subtopic | `anime`, `topicId=1`; `subtopicId=1` | Subtopic: `FE 연동 테스트 (가상 애니메이션)` |
| 콘텐츠 A `contentId`·제목 | `1` — `[FE 연동 테스트 1] 별빛 우체국의 첫 번째 편지` | BE 인증 스모크에서 피드·상세·북마크 검증 완료 |
| 콘텐츠 B `contentId`·제목 | `2` — `[FE 연동 테스트 2] 구름 정원에서 만난 작은 로봇` | BE 인증 스모크에서 피드·상세·북마크 검증 완료 |
| 테스트 사용자 접근 가능 여부 | BE 스모크 완료; 실제 참여자 인증은 FE 후속 | 일회성 합성 사용자·Tap·Redis 세션으로 확인 후 모두 정리. 공유 로그인 계정을 생성한 것은 아님 |
| 기존 테스트 사용자 조건 | 온보딩 완료·활성 계정 3개 확인 | 개인정보 없이 활성 상태 및 Topic 1의 활성 Tap을 집계. 실제 FE 담당자의 공급자 계정은 직접 로그인하여 확인 |

고정 데이터는 테스트 환경에만 넣는다. 기존 운영 DB나 운영 사용자에 데이터를 추가하거나, 고정 응답으로 운영 endpoint를 덮어쓰지 않는다. 테스트 콘텐츠 식별 및 재실행 결과는 실제 seed 검증 기록에 남긴다.

이번 seed는 스테이징에서 첫 실행 시 신규 콘텐츠 2개를 추가했고, 두 번째 실행에서는 신규 0개·기존 2개 및 같은 ID를 확인했다. 테스트 DB의 콘텐츠 수는 0개에서 2개가 되었다. 사용자·정책·구독·북마크는 생성하지 않았다. 두 자료는 가상 작품의 고정 테스트 본문이며 실제 Agent 실행 결과가 아니다. 이미지·여러 장의 카드뉴스 데이터는 포함하지 않아 이미지 대체 표시를 처리해야 한다.

## 5. FE 연결 시 남아 있는 작업

2026-10-27 13:33 KST 기록 기준 원격 FE HEAD와 현재 Preview는 `d44f79023e8b36fc2a60cac8f8668d7fddc20748`이다. 아래의 미연결 설명은 이 원격·배포 코드에 대한 확인 결과이며, FE 담당자가 전달한 미Push 로컬 커밋 `08ed87f`의 구현 상태를 판정한 것이 아니다. 해당 로컬 커밋의 소스·테스트 결과는 아직 검증하지 않았다. 두 FE flag는 Vercel에 저장했지만 새 빌드 전이므로 현 배포 코드는 유지된다.

`d44f790`에는 `/pivot/login`, `/pivot/consent`, `/pivot/home` 경로가 존재하지만 실제 인증·데이터 연결은 아직 준비되지 않았다. 별도 FE OAuth callback route는 없으며 공급자 callback은 BE가 처리한다. 다음 항목은 FE 변경이 Push된 뒤 구현·동작 여부를 재검증할 목록이다. 이번 BE·Vercel 설정 작업에서는 FE 코드를 수정하지 않았다.

1. **테스트 origin에서 API 호출 허용:** `lib/pivot/services.ts`의 runtime guard는 loopback FE와 loopback API 조합에서만 세션·정책 조회를 허용한다. 승인된 테스트 origin 조합을 지원하도록 수정해야 한다. `NEXT_PUBLIC_API_BASE_URL`만 설정해서는 해결되지 않는다.
2. **SNS 로그인 시작·복귀 연결:** `startSnsLogin()`은 아직 HTTP 요청 없이 안내 오류만 반환한다. 세션 조회 → 현재 CSRF를 포함한 OAuth POST → `authorizationUrl` 이동 → 복귀 후 새 세션·CSRF 조회를 구현하고 `/pivot/login?error=...`의 실패·취소 상태를 표시한다. 공급자 제한이 발생하면 실제 테스트 계정의 앱 테스터 등록을 확인한다.
3. **세션 판별·화면 이동 수정:** 기존 `isPendingConsumer()`는 `user.accountType === "consumer"`와 사용자 ID를 요구한다. 현행 BE의 대기 세션은 `user:null`, 완료 세션은 `accountType:"user"`이므로 `sessionState`·`onboardingCompleted`를 포함한 현행 계약에 맞춰야 한다. 로그인 화면에서 active 상태는 현재 안내만 표시하므로 홈 이동도 연결한다.
4. **Home·상세·Saved adapter 연결:** `components/pivot/pivot-session.tsx`는 `/pivot/home`을 API 세션과 분리한다. 실서비스 경로의 세션·만료 처리를 연결하고, `unconfigured` 상태인 `home-service.ts`, `detail-service.ts`, `saved-service.ts`에 기존 feed·detail·bookmarks API와 응답 변환을 구현한다. `saved.ts`·`home-detail-experience.tsx`의 Demo 전용 북마크 제어와 구분하여 실제 PUT·DELETE 및 세 화면 갱신을 연결한다.
5. **온보딩 계약·정책 확정 후 연결:** 공통 `lib/api.ts`에는 과거 `/api/v1/onboarding`과 consumer/creator payload가 남아 있다. 현행 경로는 **`POST /api/v1/onboarding/complete`**이며 새 Pivot 제출 adapter도 미연결이다. 생년월일·동의는 다음 절의 정책 결정에 따르며 임의 값으로 가입을 통과시키지 않는다.

`/pivot/demo`와 이전 `d5e6384` 데모는 FE Mock 검수용이다. 메모리 북마크가 보인다는 사실은 실제 BE 저장 성공의 근거가 아니다. `PIVOT_DEMO_ENABLED`는 데모 접근용이며 API 연결을 활성화하지 않는다. 공유 링크는 앞 절의 최신 접근 안내를 사용한다.

## 6. 생년월일·온보딩

**2026-10-27 최신 사용자 지시:** 생년월일을 어떻게 받을지는 통합 완료 후 결정한다. 수집·저장·연령 확인 방식은 미확정이며 생년월일 관련 프론트 변경과 DB 마이그레이션 추가 적용은 보류한다. 아래는 환경별 코드·검증 기록이며 최종 정책을 뜻하지 않는다.

이 통합 테스트를 수행한 당시 배포 계약은 `nickname`, `birthDate`, `accountType:"user"`, `consents`, `topicId`, `answers:{q1,q2}`를 요구했다. 아래 원격 검증 결과는 이 당시 코드 기준이다.

**2026-10-27 후속 로컬 계약:** 현재 `POST /api/v1/onboarding/complete`는 `birthDate`를 제거했다. `nickname`, `accountType:"user"`, `consents`, `topicId`, `answers:{q1,q2}`만 사용하며 `birthDate`·`profileImageId`·`subtopicIds`는 null도 포함해 422다. Q1·Q2는 각각 허용 문자열 1개이며 DB Topic/Subtopic ID가 아니다. 별도 연령 확인 필드는 없고 프로필 응답에도 `birthDate`가 없다. 새 코드와 기존 프론트 생년월일 전송의 불일치는 기록하되 입력·검사·타입·전송값의 변경 방향은 통합 완료 후 결정한다. [최신 정합화 기록](ERD_API_ALIGNMENT_2026-10-27.md)을 따른다. 이 기록은 원격 DB 적용·배포 또는 실제 화면 검증 완료를 뜻하지 않는다.

실제 사용자의 가입을 통과시키려고 임의 동의·임의 온보딩 완료 값을 넣지 않는다. 실제 참여자의 브라우저 통합 테스트에는 정당한 절차로 온보딩을 마친 사용자가 필요하며, 준비되지 않았다면 해당 단계는 미완료로 표시한다.

이번 BE 검증에서는 실제 사용자와 별개인 일회성 합성 UserAccount·활성 Tap·정상 Redis 세션을 만들어 콘텐츠 API만 확인한 뒤 정리했다. 개인 계정·동의·생년월일은 변경하지 않았고, 인증 우회 endpoint도 추가하지 않았다. 이 합성 fixture 검증을 실제 OAuth 로그인이나 신규 사용자 온보딩 성공으로 보고하지 않는다.

## 7. 실제 적용·검증 기록

| 점검 | 상태 | 실제 결과·시각 |
| --- | --- | --- |
| Vercel Preview에 `test.me-nu.kr` 연결 | 완료 | `feature/pivot-user-fe`, `Valid Configuration` 확인 |
| Gabia CNAME 및 DNS 연결 | 완료 | 2026-10-27 09:52:59 KST CNAME 추가, TTL 600; 기존 `api.test` A 레코드 유지 |
| FE 테스트 도메인 TLS·화면 | 확인 완료 | `https://test.me-nu.kr/pivot/login` 화면 열림. FE 인증 기능 검증과 구분 |
| 팀 로그인 없는 공유 접근 | 확인 완료 | `test.me-nu.kr`의 `Anyone with the link` 링크로 비로그인 브라우저 접근 성공. 기존 공유 링크 교체 승인 반영 |
| BE CORS에 테스트 FE origin 추가 | 검증 완료 | 공개 HTTPS에 `Origin: https://test.me-nu.kr`을 보내 OPTIONS `200`, 정확한 allow-origin 및 credentials `true` 확인 |
| OAuth FE 복귀 origin 설정 | 적용 완료 | `https://test.me-nu.kr`. active → `/pivot/home`은 기존 회원의 실제 카카오·네이버 로그인으로 확인. pending → `/pivot/consent`는 코드·단위 테스트 확인; 오류 → `/pivot/login?error=...`는 공개 HTTP 검증 완료 |
| FE API 환경변수·재배포 | 완료 | `feature/pivot-user-fe@d44f790`, Preview `CFDHAUMBDNtagbELVHoStfFsrcHp`, 2026-10-27 10:00 KST Ready. `test.me-nu.kr` 연결 확인 |
| FE 테스트·로컬 API flag 추가 | 저장 완료·새 빌드 대기 | `NEXT_PUBLIC_PIVOT_ENABLE_TEST_API=true`, `NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API=false`를 `Preview / feature/pivot-user-fe`에만 저장. 기존 변수·Production·main 변경 및 재배포 없음 |
| FE 전달 커밋 `08ed87f` | 원격·배포 미반영 | 2026-10-27 13:33 KST 기록 기준 미Push 로컬 작업. 담당자 전달 내용이며 소스·테스트 결과는 직접 검증하지 않음. 현 Preview `d44f790` 유지 |
| 고정 콘텐츠 2개 seed | 완료 | 첫 실행 신규 2개, 재실행 신규 0개·기존 2개. Topic 1·Subtopic 1·콘텐츠 1,2 |
| 컨테이너 상태 | 확인 완료 | OAuth 복귀 origin 적용 시 동일 API 이미지로 API 컨테이너만 재생성·healthy, DB·Redis 컨테이너 변경 없음 |
| 공개 API·DB·Redis health·명세 | 검증 완료 | HTTPS `/health`, `/health/db`, `/health/redis`, `/docs`, `/openapi.json` 모두 `200` |
| 공개 익명 세션·CSRF | HTTP 검증 완료 | 로컬 Python HTTPS 요청에서 익명 세션 `200`, Secure·HttpOnly·Lax 쿠키 및 CSRF 발급 확인. FE 브라우저 왕복은 후속 |
| 공개 인증 거절·OAuth 시작 | HTTP 검증 완료 | 익명 feed `401`, CSRF 없는 로그인 시작 `403`, 카카오·네이버 시작 `200`, `kauth.kakao.com`·`nid.naver.com` 및 callback 주소 일치, 로그아웃 `204` |
| OAuth 복귀 origin 적용 후 공개 HTTPS 재검증 | HTTP 검증 완료 | `/health`, `/health/db`, `/health/redis`, `/docs` `200`; 테스트 FE origin의 CORS·credentials 및 익명 Secure·HttpOnly·Lax 쿠키 확인 |
| 두 공급자 CSRF·취소·state·재사용 | HTTP 검증 완료 | 카카오·네이버 각각 CSRF 없음 `403`; callback state 없음 `303 AUTH_FAILED`; 유효 state의 `access_denied`는 `303 AUTH_CANCELLED`; state 재사용은 `303 AUTH_FAILED`; FE 로그인 경로로 이동 및 로그아웃 `204` 확인 |
| 기존 OAuth 단위 테스트 | 통과 | 99개 통과. 실제 공급자 브라우저 로그인 완료와 구분 |
| 로컬 runner의 FE origin 옵션 | 구현·테스트 완료 | `--frontend-origin http://localhost:3000` 추가. 해당 변경을 포함한 테스트 118개 통과 |
| 로컬 runner 실제 `--check` | 실행 불가 확인 | Docker Desktop Linux engine 미실행으로 로컬 DB·Redis 준비 확인 실패. 로컬 DB 변경 없음; API 기동·브라우저 왕복 미검증 |
| 실제 카카오 브라우저 로그인 | 기존 회원 검증 완료 | 사용자 직접 로그인 후 `/pivot/home` 복귀. 같은 Edge의 BE session 조회에서 authenticated·active·accountType user·onboardingCompleted 확인. 홈의 콘텐츠 서비스 미연결 안내는 FE 후속 작업 |
| 카카오 로그인 브라우저의 피드 직접 조회 | 읽기 검증 완료 | 같은 Edge 세션에서 `GET /api/v1/topics/1/feed?limit=20`의 JSON 표시 확인. `selectedTopicId:1`, 콘텐츠 ID `2,1`과 두 테스트 제목, `nextCursor:null`, `emptyReason:null`. 주소창의 직접 API GET이며 FE fetch 또는 브라우저 북마크 변경 검증은 아님 |
| 실제 네이버 브라우저 로그인 | 기존 회원 검증 완료 | 2026-10-27. 로그아웃 `204`·익명 세션 확인 → Swagger 로그인 시작 `200` → 사용자 직접 로그인 → `/pivot/home` 복귀. 같은 Edge의 BE session 조회에서 authenticated·active·accountType user·onboardingCompleted 및 CSRF 교체 확인 |
| 네이버 로그인 브라우저의 피드 직접 조회 | 읽기 검증 완료 | 같은 Edge에서 `GET /api/v1/topics/1/feed?limit=20`의 Topic 1·콘텐츠 ID `2,1`·두 테스트 제목·`emptyReason:null` 확인. FE fetch·브라우저 북마크 변경 검증과 구분 |
| 신규 사용자 온보딩 | 미검증 | 카카오·네이버 실제 로그인은 기존 온보딩 완료 계정으로 확인. 신규 계정의 가입 완료는 별도 확인 필요 |
| 합성 사용자 인증·피드·상세 | BE 스모크 통과 | 실서버 컨테이너 loopback HTTP와 기존 Redis 정상 세션 사용. 콘텐츠 ID `1,2` 및 `full_body` 확인 |
| 북마크 PUT·Saved·DELETE | BE 스모크 통과 | CSRF 없는 PUT `403`; 두 콘텐츠 저장·Saved 조회; 반복 PUT 동일 ID; 반복 DELETE `204`; Saved 빈 목록·상세 미저장; 로그아웃 `204` |
| 합성 fixture 정리 | 통과 | 검증용 사용자·세션·Tap·Saved 모두 정리. 개인 사용자 데이터 변경 없음 |
| 실제 FE 로그인·Home·Saved 왕복 | 새 FE 배포 후 검증 필요 | 현재 배포 `d44f790`의 인증·화면 adapter 미연결. 미Push `08ed87f`의 구현·테스트 상태와 구분하며 위 BE 스모크로 FE 통합 성공을 주장하지 않음 |
| 공개 Preview의 카카오 버튼 | `d44f790` 관찰 기록 | 해당 배포에서 버튼 클릭 시 `카카오 로그인을 준비하고 있어요. 연결이 완료되면 이용할 수 있어요.` 안내 표시. 새 FE 코드 배포 후 다시 확인 |
| Production·`main` 변경 여부 | 변경 없음 | 기존 Production `9HRC2UrJGEKTgeyCkHCFbScMeQMu` / `main@9f14399` 유지. FE 기능 브랜치 Preview만 재배포 |

공개 HTTPS 검증은 Python HTTP 클라이언트로 수행했고, 인증 콘텐츠 스모크는 실서버 컨테이너의 loopback HTTP에서 수행했다. 전자는 공개 TLS·CORS·쿠키 속성·인증 시작 및 취소·실패 복귀 계약, 후자는 기존 API·Redis 세션·테스트 DB 저장 동작을 확인한다. 추가로 기존 온보딩 완료 계정의 실제 카카오·네이버 로그인·FE 홈 복귀 및 각 로그인에 사용한 같은 Edge 브라우저의 BE session 조회와 Topic 1 피드의 콘텐츠 `2,1` 직접 GET을 확인했다. 신규 사용자 온보딩, FE JavaScript의 인증 포함 fetch 및 Home·Saved 왕복, 실제 브라우저의 북마크 변경은 아직 검증 완료가 아니다. 이번 1단계 BE 설정·두 공급자 로그인 검증 완료와 FE 연결 완료를 구분한다.

seed의 격리 SQLite 테스트는 `backend/tests/test_fe_integration_seed.py` 25개가 통과했다. 테스트 대상 검사, 재실행 시 동일 ID, 충돌 시 중단, 전체 롤백 및 기존 피드·상세·북마크 서비스 동작을 포함한다. 실행 모듈은 `backend/app/seeds/fe_integration.py`이며 기본 실행은 읽기 전용이다.

서버 코드는 `backend-labs@53a2bf2`와 기존 이미지 `sha256:f20cdcd5c9ee7438f67765cea8061c1e3f5b15e81ef8d6717886eff451eb6f30`을 유지했다. seed 파일만 `/home/ubuntu/fe-integration-20261027/fe_integration.py`로 전달하여 기존 이미지의 일회성 컨테이너에서 실행했다. CORS 변경 전 설정 백업은 같은 디렉터리의 `deploy.env.before-cors`이며 권한은 600이다. 백업에는 비밀값이 있으므로 공유 문서나 Git에 첨부하지 않는다.

근거 코드: `backend/app/api/v1/auth/{router,oauth}.py`, `backend/app/api/deps.py`, `backend/app/core/security.py`, `backend/app/api/v1/{topics,contents}/router.py`, `backend/app/api/v1/users/me/bookmarks.py`, `backend/app/api/v1/onboarding/router.py`, `backend/app/schemas/{onboarding,feed,reaction,bookmark}.py`.

OAuth FE 복귀 origin 변경 전 백업은 `/home/ubuntu/fe-integration-20261027/deploy.env.before-fe-redirect`이며 권한은 600이다. 이 백업도 비밀값을 포함하므로 공유하거나 Git에 넣지 않는다. 이번 확인에 사용한 로컬 임시 SSH 키 복사본은 삭제했으며 원본 키는 유지했다.
