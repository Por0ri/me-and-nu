# T02 OAuth 백엔드 설정과 연결 계약

기준일: 2026-10-20. T02-1에서 시작해 사용자의 후속 지시에 따라 프론트 작업 없이 가능한 OAuth 백엔드까지 구현했다. 아래 구현 상태는 로컬 코드 기준이다. 카카오 앱 등록과 로컬 키 입력·localhost callback 등록은 준비됐다. 별도 로컬 테스트 DB에는 마이그레이션을 적용했다. 원격 OAuth 배포와 실계정 로그인 검증은 아직 수행하지 않았다. 사용자 지시 전까지 커밋·푸시·머지는 보류한다.

## 확정 사항과 환경

- 카카오·네이버 로그인, T01의 단일 `user` 회원 유형을 사용한다.
- 식별 기준은 `(auth_provider, provider_user_id)`다. 이메일로 계정을 자동 연결하지 않는다.
- **사용자 확정:** 신규 소셜 계정의 이메일이 기존 계정과 겹치면 가입을 중단하고 기존 가입 수단으로 로그인하도록 안내한다.
- 프론트 작업·주소 결정은 보류한다. 프론트 없이 백엔드에서 인증 결과를 확인할 수 있다.
- API 주소는 `https://api.test.me-nu.kr`이다. 2026-10-20 DNS A 조회와 TLS 검증을 포함한 `/health` 호출이 성공했고 `200 {"status":"ok"}`를 받았다. 신규 OAuth 코드의 배포 확인은 아니다.
- 카카오 개발자 앱은 사용자가 생성했다. PC의 `backend/.env`에서 키 두 개의 입력 여부를 확인했고, localhost callback 추가 완료를 사용자가 확인했다. 키 유효성은 실제 로그인에서 확인한다. 네이버 등록은 후속 작업이다.
- 기존 세션 만료 30분은 유지한다. PRD의 30일 유지와 탈퇴용 재인증 정책은 후속 결정 사항이다.

## 구현된 계약

| 기능 | 동작 |
| --- | --- |
| 로그인 시작 | `POST /api/v1/auth/login/{provider}` → `200 {"authorizationUrl":"..."}` |
| 콜백 | `GET /api/v1/auth/login/{provider}/callback` → 세션 쿠키 발급 후 `303` |
| 공급자 통신 | 서버에서 코드 교환·회원 정보 조회. 시크릿·공급자 토큰은 브라우저 응답에 넣지 않음 |
| state | 해시만 저장, 시작 세션·공급자·만료 검증, 원자적 소비·커밋 후 공급자 호출 |
| 요청 제한 | 시작 브라우저 세션당 10분에 10회. 소비된 시도 포함, PostgreSQL 트랜잭션 잠금으로 직렬화 |
| 회원 | 신규는 온보딩 대기, 완료 회원은 활성 세션. 이메일 충돌·비활성·탈퇴 계정은 로그인 제한 |
| 세션 | 기존 PostgreSQL/Redis, HttpOnly 쿠키·CSRF·세션 교체·로그아웃 재사용 |
| 백엔드 단독 확인 | 프론트 origin이 비어 있으면 기존 `/api/v1/auth/session`으로 복귀 |

시작 API는 세션과 `X-CSRF-Token`이 필요하다. 먼저 `GET /api/v1/auth/session`을 호출한다. 본문 생략 시 `purpose=signin`이다. 외부 이동 주소를 요청 본문으로 받지 않는다.

```json
{"purpose": "signin"}
```

원본 API-001의 `reauth`·`restore`는 현재 `422 UNSUPPORTED_AUTH_PURPOSE`로 거절한다. 본인 재인증·탈퇴 복구는 T08에서 연결한다. 개발용 인증 우회 세션으로는 실제 OAuth를 시작할 수 없다.

신규 계정은 소셜 식별만 끝난 상태다. 닉네임·생년월일·동의·관심사 검증은 기존 온보딩 API가 담당하고, 완료 전에는 보호된 서비스 기능에 접근할 수 없다. 새 디자인의 Q1·Q2 저장은 아직 연결하지 않았다.

이메일은 공급자가 제공한 경우에 중복을 검사한다. 카카오는 유효·검증 완료 표시가 있는 이메일만 사용하며, 누락 시 기존 DB 계약대로 null을 허용한다. 이메일을 받지 못한 두 소셜 계정이 같은 사람인지 추측하지 않는다.

## 사용자가 준비할 개발자 앱

팀 테스트용 callback은 다음과 같다. **백엔드 수신 주소**이며, 끝 슬래시는 붙이지 않는다.

| 공급자 | callback |
| --- | --- |
| 카카오 | `https://api.test.me-nu.kr/api/v1/auth/login/kakao/callback` |
| 네이버 | `https://api.test.me-nu.kr/api/v1/auth/login/naver/callback` |

### 카카오

1. [Kakao Developers](https://developers.kakao.com/)에서 앱을 생성한다.
2. **카카오 로그인 → 사용 설정**을 활성화한다.
3. **앱 → 플랫폼 키 → REST API 키**에서 위 Redirect URI를 등록한다.
4. REST API 키를 `KAKAO_CLIENT_ID`, Client Secret을 `KAKAO_CLIENT_SECRET`에 설정한다. 현재 공식 문서에서 시크릿은 기본 활성화이며 이 구현도 시크릿을 사용한다.
5. 회원번호로 식별한다. 닉네임이 필요하면 `profile_nickname` 선택 동의를 설정한다. 이메일·생년월일을 필수 동의로 추가할 필요는 없다.

REST 방식이므로 JavaScript SDK 설정은 전제 조건이 아니다. 키는 서버 환경에만 보관한다.

근거: [앱 설정](https://developers.kakao.com/docs/ko/app-setting/app), [로그인 설정](https://developers.kakao.com/docs/ko/kakaologin/prerequisite), [사용자 정보·동의](https://developers.kakao.com/docs/ko/kakaologin/utilize).

### 네이버

1. [Naver Developers](https://developers.naver.com/)에서 앱을 생성하고 **네이버 로그인**을 선택한다.
2. PC 웹의 **서비스 URL**과 위 **Callback URL**을 등록한다. 서로 다른 항목이다. 프론트 주소가 미정이므로 최종 서비스 URL·공개 검수 자료는 후속 준비로 남긴다. 등록 화면에 임의의 프론트 주소를 확정하지 않는다.
3. Client ID·Client Secret을 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`에 설정한다.
4. 개발 테스트는 앱 등록자 또는 등록된 관리자·테스터로 진행한다.
5. 식별자 외 정보를 요청한다면 실제 사용 목적에 맞는 항목만 선택한다.

일반 사용자 공개 전에는 사전 검수가 필요하다. 실제 로그인·가입 화면 등 검수 자료는 프론트 연결 후 준비한다.

근거: [앱 등록](https://developers.naver.com/docs/common/openapiguide/appregister.md), [프로필 명세](https://developers.naver.com/docs/login/profile/profile.md), [사전 검수](https://developers.naver.com/docs/login/verify/verify.md).

## 서버 설정

로컬은 `backend/.env.example`, 배포는 `deploy/.env.example`을 참고한다. 실제 키는 예제 파일·채팅·Git에 넣지 않는다. 배포 Compose에 설정 전달 항목도 추가했다.

| 변수 | 백엔드 단독 팀 테스트 |
| --- | --- |
| `OAUTH_PUBLIC_API_ORIGIN` | `https://api.test.me-nu.kr` |
| `OAUTH_FRONTEND_ORIGIN` | 빈 값: `/api/v1/auth/session`으로 복귀 |
| `OAUTH_STATE_TTL_SECONDS` | `600` |
| `KAKAO_OAUTH_ENABLED`, `NAVER_OAUTH_ENABLED` | 준비 전 `false`, 각 앱 키·마이그레이션·배포가 준비되면 `true` |
| `KAKAO_CLIENT_ID`, `KAKAO_CLIENT_SECRET` | 카카오 서버 인증 값 |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 네이버 서버 인증 값 |
| `SESSION_COOKIE_SECURE` | HTTPS에서는 `true` (배포 Compose 고정) |
| `ENABLE_DEV_AUTH_BYPASS` | `false` |

활성 공급자의 키·시크릿 누락, 잘못된 origin, 원격 API의 Secure 쿠키 누락은 설정 오류로 거절한다. 두 공급자를 동시에 활성화할 필요는 없다. 원격 서버에 이 설정을 직접 적용하지는 않았다.

로컬 API를 사용하려면 `OAUTH_PUBLIC_API_ORIGIN=http://localhost:8000`, `SESSION_COOKIE_SECURE=false`로 설정하고 로컬 callback도 등록한다. 카카오는 공식 튜토리얼에서 localhost HTTP를 안내한다. 네이버 로컬 주소는 콘솔 등록 조건을 별도로 확인한다. 팀 테스트의 기준 주소는 위 HTTPS 도메인이다.

## PC에서 카카오 로그인 테스트

Windows PowerShell에서 아래 전용 실행 스크립트를 사용한다. 기존 `backend/.env`의 DB 설정은 그대로 두며, 스크립트가 프로세스 안에서 테스트 DB와 세션 설정을 적용한다.

```powershell
cd C:\Users\USER\me_nu\backend
.\.venv\Scripts\python.exe scripts/run_local_oauth.py
```

- 테스트 DB: 기존 로컬 PostgreSQL 안의 `me_nu_oauth_local`, revision `b9d4e2a7f013`.
- 기존 DB `me_nu`는 revision `e7a4c2d9b812` 그대로 유지했다.
- Redis 접두사 `me_nu_oauth_local`, 쿠키 `menu_oauth_local`을 사용한다.
- `localhost:8000`으로 실행하고 카카오만 활성화한다. 개발용 인증 우회와 개발용 API는 비활성화한다.
- 프론트 복귀 주소는 비워 두고, callback의 code/state가 콘솔에 남지 않도록 접근 로그를 끈다.
- 원격 DB·Redis 및 연결 대상 변경에 쓰일 수 있는 URL query 옵션을 거부한다. 테스트 DB 관리 표식이 없으면 마이그레이션·실행을 거부한다.
- 이 실행 모드에서는 데이터와 로그인 세션이 기존 로컬 API와 분리된다. 로그인 확인만을 위해 콘텐츠나 데모 회원을 적재하지 않았다.

키가 없을 때는 `backend/.env`의 `KAKAO_CLIENT_ID`에 REST API 키, `KAKAO_CLIENT_SECRET`에 Client Secret을 넣는다. 키 값은 출력하거나 Git에 넣지 않는다. 나머지 OAuth 실행 설정은 이 스크립트가 적용한다.

카카오 콘솔에는 아래 주소를 추가한다. 기존 테스트 서버 callback도 유지한다.

```text
http://localhost:8000/api/v1/auth/login/kakao/callback
```

상태 확인과 새 PC에서의 테스트 DB 준비 명령은 다음과 같다. `--prepare`는 기존 앱 DB를 변경하지 않고 전용 DB만 생성·마이그레이션한다.

```powershell
.\.venv\Scripts\python.exe scripts/run_local_oauth.py --check
.\.venv\Scripts\python.exe scripts/run_local_oauth.py --prepare
```

현재 PC에서는 DB 준비가 완료됐으므로 일반 실행만 하면 된다. 8000번 포트에 기존 API가 떠 있다면 해당 API를 실행한 터미널에서 종료한 후 실행한다. 서버 종료는 실행 터미널의 `Ctrl+C`를 사용한다.

브라우저에서 `http://localhost:8000/docs`를 열고 아래 수동 검증을 진행한다. 중간에 `127.0.0.1`이나 다른 브라우저 프로필로 바꾸지 않는다.

## DB 변경

새 revision `b9d4e2a7f013`은 `oauth_attempt` 테이블을 추가한다. 원본 state·공급자 토큰·시크릿은 저장하지 않는다. 만료행은 요청 제한 집계 기간이 지난 뒤 새 로그인 시작 시 정리한다.

세션이 Redis 모드여도 OAuth 시도는 PostgreSQL에서 처리하므로 마이그레이션이 필요하다. 회원 테이블의 기존 소셜 식별 컬럼·UNIQUE는 재사용한다.

직전 revision은 T01의 `a8c3d1f6e902`다. `upgrade head`에는 기존 `signup_channel` 제거도 포함되므로 서버의 현재 revision과 적용 범위를 확인한 뒤 배포 절차를 따른다. 별도 로컬 테스트 DB에만 적용했으며, 기존 로컬 DB와 원격 서버 DB에는 적용하지 않았다.

## 프론트 없이 확인하는 순서

신규 코드를 로컬 또는 팀 API 서버에 적용한 **이후**의 수동 검증 순서다.

1. 공급자 키와 마이그레이션을 준비한다. `OAUTH_FRONTEND_ORIGIN`은 비워 둔다.
2. 같은 브라우저에서 API의 `/docs`를 열고 `GET /api/v1/auth/session`을 실행해 쿠키와 `csrfToken`을 받는다.
3. Swagger Authorize의 `CsrfToken`에 토큰 값만 넣는다. 실제 요청 헤더 이름은 `X-CSRF-Token`이며, `Bearer` 접두어와 `SessionCookie` 수동 입력은 필요 없다.
4. `POST /api/v1/auth/login/kakao` 또는 `/naver`를 `{"purpose":"signin"}`으로 호출한다.
5. 반환된 `authorizationUrl`을 **같은 브라우저**의 주소창에 열고 공급자 로그인을 진행한다.
6. callback이 쿠키를 교체한 뒤 `/api/v1/auth/session`으로 이동한다. 신규는 `authenticated:true`, `sessionState:"onboarding_pending"`, `user:null`이다. 완료 회원은 `active`, `accountType:"user"`다.
7. 새 `csrfToken`으로 로그아웃을 확인한다. 실패는 복귀 URL의 고정 `error` 값으로 구분한다.

| 오류 | 의미 |
| --- | --- |
| `AUTH_CANCELLED` | 사용자가 소셜 동의를 취소 |
| `AUTH_FAILED` | 잘못된 state/code, 만료·재사용·다른 브라우저, 공급자 응답 형식 오류 |
| `AUTH_UNAVAILABLE` | 공급자 비활성·통신 장애·DB/Redis 장애 |
| `AUTH_ACCOUNT_EXISTS` | 이메일 충돌. 기존 가입 수단으로 로그인 |
| `AUTH_ACCOUNT_UNAVAILABLE` | 현재 로그인할 수 없는 계정. 자동 복구하지 않음 |

공급자 오류 원문·인가 코드·토큰은 브라우저 응답에 반영하지 않는다. 배포 시 서버·프록시의 callback 접근 로그도 쿼리 원문을 남기지 않도록 확인한다.

## 후속 연결과 검증 범위

- 프론트 연결 시에만 `OAUTH_FRONTEND_ORIGIN`을 채우고 `FRONTEND_ORIGINS`에 등록한다. 기존 SameSite=Lax 쿠키 방식은 같은 사이트의 HTTPS 구성에 맞춘다.
- 화면 복귀 경로는 Pivot 설계의 신규 `/pivot/consent`, 완료 `/pivot/home`, 실패 `/pivot/login?error=...`다. 이번에 프론트 파일을 변경하거나 원격 브랜치를 병합하지 않았다.
- 기존 자동 테스트는 공급자 통신을 대체하고 DB 동작은 SQLite 메모리로 확인한다. 실제 PostgreSQL 동시 요청 경합, 실계정 OAuth, 원격 쿠키·프록시는 추가 검증 대상이다.
- 검증: 기존 OAuth·인증 자동 테스트 268개 통과, 로컬 실행 스크립트의 DB·Redis 격리 및 원본 환경파일 보존 테스트 18개 통과.
- 실제 로컬 PostgreSQL·Redis에서 API/DB/Redis 상태, Swagger, 익명 세션, CSRF 거절, 카카오 인가 URL 생성, 로그인 취소 시 state 소비, state 재사용 거절, 로그아웃을 확인했다. 이 점검은 카카오 토큰 교환이나 실제 회원 로그인을 수행하지 않았다.
- 재인증·탈퇴 복구, 30일 로그인 유지, Q1·Q2 저장은 후속 작업이다.

계약 근거: [API-001](https://app.notion.com/p/3f704c45a9618129aac0fb2b55c85e71), [API-002](https://app.notion.com/p/3f704c45a96181208b87d1d534f49876), [카카오 REST](https://developers.kakao.com/docs/ko/kakaologin/rest-api), [네이버 인증 API](https://developers.naver.com/docs/login/api/api.md). 원본의 프론트 경로는 최종 확인 대상이며, 백엔드 단독 복귀 방식은 이번 작업에서 추가한 계약이다.
