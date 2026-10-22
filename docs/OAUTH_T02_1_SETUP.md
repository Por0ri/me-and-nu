# T02 OAuth 백엔드 설정과 연결 계약

기준일: 2026-10-20, 로컬 검증 갱신: 2026-10-21. T02-1에서 시작해 프론트 작업 없이 OAuth 백엔드와 Q1·Q2 온보딩 저장을 구현했다. 별도 로컬 테스트 DB에 마이그레이션을 적용했으며, 실제 카카오 인증 → 온보딩 완료 → PostgreSQL 저장값 대조 → 로그아웃 후 재로그인까지 확인했다. 원격 OAuth 배포 검증은 별도다. 사용자 지시에 따라 이후 커밋·푸시는 `backend-labs`에서 진행하며, 머지는 별도 지시 전까지 수행하지 않는다.

## 2026-10-21 로컬 1단계 검증 결과

목표는 실제 카카오 인증과 서비스 세션 발급 확인이다. 이번 브라우저 검증에서는 온보딩 완료 요청을 제출하지 않았다.

- 실행 환경: `http://localhost:8000`, 전용 DB `me_nu_oauth_local`, Redis 세션. `run_local_oauth.py --check` 통과.
- Edge의 Swagger에서 `GET /api/v1/auth/session`이 `200`, `authenticated=false`, `sessionState=anonymous`를 반환했다.
- 해당 세션의 CSRF를 적용한 `POST /api/v1/auth/login/kakao`가 `200`과 인가 URL을 반환했다. callback 주소는 `http://localhost:8000/api/v1/auth/login/kakao/callback`이었다.
- 같은 브라우저에서 인가 URL을 열어 실제 카카오 인증을 진행했다. 기존 카카오 로그인 상태로 이어져 별도 계정 입력 화면은 나타나지 않았다.
- 최종 주소는 오류 query가 없는 `/api/v1/auth/session`이었으며 `authenticated=true`, `sessionState=onboarding_pending`, `onboardingCompleted=false`, `user=null`을 확인했다.
- 로그인 전후 CSRF가 바뀌었고, 최종 페이지를 새로고침해도 로그인 상태가 유지됐다. 토큰·쿠키·인가 URL 원문은 기록하지 않는다.
- `onboarding_pending`은 온보딩 미완료 상태라는 뜻이며, 이번 요청에서 새 계정이 생성됐다는 뜻은 아니다. 이후 단계에서 실제 약관·Topic을 조회하고 온보딩을 제출한다.
- 직전 준비 단계에서 관련 자동 테스트 78개가 통과했다. 자동 테스트의 공급자 응답 대체 검증과 위 실제 브라우저 검증은 구분한다.

## 1단계 Swagger 시연 순서

1. `http://localhost:8000/docs`를 연다. 서버가 이미 실행 중이면 실행 스크립트를 중복으로 시작하지 않는다.
2. **인증·세션 → GET `/api/v1/auth/session` → Try it out → Execute**를 실행한다.
3. 응답의 `csrfToken`을 **Authorize → CsrfToken**에 값만 입력하고 적용한다. `SessionCookie`는 입력하지 않는다.
4. **POST `/api/v1/auth/login/{provider}`**에서 `provider`에 `kakao`, 본문에 `{"purpose":"signin"}`을 넣고 실행한다.
5. 반환된 `authorizationUrl`을 같은 브라우저 프로필의 주소창에서 연다. 계정 입력·동의 화면이 나오면 사용자가 직접 진행한다. 기존 카카오 세션이 있으면 바로 callback으로 돌아올 수 있다.
6. 최종 주소에 `error`가 없고 응답이 `authenticated=true`인지 확인한다. 미완료 회원은 `onboarding_pending`, 완료 회원은 `active`가 정상이다. callback의 리다이렉트만으로 성공으로 판단하지 않는다.
7. 온보딩을 이어갈 때는 새 `csrfToken`으로 Swagger Authorize를 갱신한다. 이번 1단계에서는 `POST /api/v1/onboarding/complete`를 호출하지 않는다.

이미 온보딩을 완료한 계정은 초기화하지 않는다. 신규 온보딩 과정을 시연하려면 온보딩 미완료 카카오 테스트 계정을 사용한다. 오래된 인가 URL은 재사용하지 않고 2~5단계부터 다시 진행한다.

## 2026-10-21 온보딩 제출과 저장 검증 결과

앞서 합의한 단계표의 2단계는 가입 상태 조회, 3단계는 온보딩 제출이다. 두 단계를 완료하고, 사용자가 요청한 저장 결과 대조(4단계)도 수행했다. 이어서 사용자 요청으로 진행한 로그아웃 후 재로그인(5단계)은 아래 별도 기록을 따른다.

- 실제 카카오 로그인 세션에서 `onboarding_pending`을 확인하고 최신 CSRF로 Swagger의 `POST /api/v1/onboarding/complete`를 실행했다.
- 사용자가 승인한 테스트 프로필은 닉네임 `온보딩테스트`, 생년월일 `2000-01-01`, 분야 애니메이션이다. Q1은 `ost`, Q2는 `events`를 선택했다.
- 현재 로컬 정책 `v1`은 실제 서비스 약관이 아닌 테스트용 임시 안내다. 사용자의 선택에 따라 이용약관·개인정보 동의는 `true`, 광고·마케팅 동의는 `false`로 제출했다.
- 응답은 `201`, `onboardingCompleted=true`, `activeTopic.code=anime`, `answers={"q1":"ost","q2":"events"}`였다. `activeTopic.subtopicIds`는 빈 배열이었다.
- 후속 `GET /api/v1/auth/session`은 `authenticated=true`, `sessionState=active`, `onboardingCompleted=true`였다. CSRF가 교체됐고 페이지 새로고침 후에도 같은 계정의 완료 상태가 유지됐다.
- 응답의 회원 ID와 Topic ID로 범위를 제한해 PostgreSQL을 읽기 전용 트랜잭션으로 조회했다. `me_nu_oauth_local`과 전용 DB 표식을 먼저 확인했다.
- DB에서 테스트 닉네임·생년월일 일치, `onboarding_completed_at` 저장, 활성 `tap` 1개와 연결된 `onboarding_answers` 1행을 확인했다. 같은 행의 `q1_choice=ost`, `q2_choice=events`가 제출값과 일치했다. 해당 `tap`의 `tap_topic` 행은 0개였다.
- 동의 이력은 4종류 각각 1행, 모두 `v1`이었다. 이용약관·개인정보는 동의 시각과 `true`, 광고·마케팅은 `false`로 저장됐다. 알림 설정의 `marketing`도 `false`였다. Q2의 `events` 선택은 광고·마케팅 동의를 뜻하지 않는다.
- 이번에는 기존 구현의 실제 실행과 데이터 저장을 검증했으며 추가 코드·스키마 변경은 없었다. 프론트 파일, 원본 `me_nu`, 배포 DB는 변경하지 않았다. 완료한 테스트 계정 데이터는 보존한다.

## 온보딩 Swagger 시연 순서

현재 계정은 이미 완료 상태다. 결과만 다시 보려면 로그인했던 같은 브라우저에서 `GET /api/v1/auth/session`을 실행해 `active`와 `onboardingCompleted=true`를 확인한다. 세션이 만료됐다면 위 카카오 로그인 절차를 다시 진행한다.

신규 온보딩을 처음부터 시연하려면 온보딩 미완료 카카오 테스트 계정으로 로그인한 뒤 다음 순서로 진행한다.

1. `GET /api/v1/auth/session`에서 `onboarding_pending`을 확인한다. 반환된 `csrfToken`으로 Swagger **Authorize → CsrfToken**을 갱신한다. 토큰 값만 입력하고 `SessionCookie`는 비워 둔다.
2. `GET /api/v1/policies`, `GET /api/v1/topics`로 현재 정책 버전과 Topic ID를 확인한다. 아래 `v1`, `topicId=2`는 이번 PC의 로컬 테스트 값이며 다른 환경에서는 조회 결과로 바꾼다.
3. `POST /api/v1/onboarding/complete`에서 **Try it out**을 누르고 다음 본문을 입력한다. 닉네임·테스트 생년월일·Q1·Q2·동의는 시연자가 선택한다. 필수 2종은 동의해야 하고 선택 2종은 미동의로도 완료할 수 있다.

```json
{
  "nickname": "온보딩테스트",
  "birthDate": "2000-01-01",
  "accountType": "user",
  "consents": [
    {"type": "terms", "policyVersion": "v1", "agreed": true},
    {"type": "privacy", "policyVersion": "v1", "agreed": true},
    {"type": "advertising", "policyVersion": "v1", "agreed": false},
    {"type": "marketing", "policyVersion": "v1", "agreed": false}
  ],
  "topicId": 2,
  "answers": {"q1": "ost", "q2": "events"}
}
```

4. **Execute** 후 `201`, `onboardingCompleted=true`, 제출한 `answers`가 반환되는지 확인한다. `profileImageId`와 `subtopicIds`는 요청에 넣지 않는다.
5. `GET /api/v1/auth/session`을 다시 실행해 `active`를 확인한다. 다음 변경 요청 전에 새 응답의 CSRF로 Authorize를 갱신한다.
6. DB 클라이언트에서 **`me_nu_oauth_local`**에 연결한다. 아래 조회에서 `REPLACE_WITH_USER_ID`를 완료 응답 또는 현재 세션의 `user.id`로 바꾸고 실행한다. 동일 계정의 활성 분야가 여러 개라면 완료 응답의 `activeTopic.id`에 해당하는 행을 확인한다.

```sql
BEGIN TRANSACTION READ ONLY;
SELECT current_database();
SELECT t.topic_id, a.q1_choice, a.q2_choice, a.created_at,
       u.onboarding_completed_at,
       (SELECT count(*) FROM tap_topic AS st WHERE st.tap_id = t.tap_id) AS subtopic_count
FROM user_account AS u
JOIN tap AS t ON t.user_id = u.user_id AND t.deleted_at IS NULL
JOIN onboarding_answers AS a ON a.tap_id = t.tap_id
WHERE u.user_id = REPLACE_WITH_USER_ID;
ROLLBACK;
```

이번 테스트의 예상값은 `topic_id=2`, `q1_choice=ost`, `q2_choice=events`, 두 시각은 null이 아님, `subtopic_count=0`인 행 1개다. `ROLLBACK`은 읽기 전용 조회 트랜잭션을 끝내며 이미 저장한 온보딩 데이터는 그대로 유지한다.

이미 완료한 계정으로 온보딩을 다시 제출하면 계약상 `409 ONBOARDING_ALREADY_COMPLETED`다. 이를 재현하기 위해 계정이나 완료 상태를 초기화하지 않는다. `403`이면 최신 CSRF와 만 14세 이상인 테스트 생년월일을 확인하고, `422`이면 요청 형식과 Q1·Q2 허용값을 확인한다.

## 2026-10-21 로그아웃 후 카카오 재로그인 검증 결과

- 완료 회원의 현재 세션을 확인한 뒤 최신 CSRF로 Swagger의 `POST /api/v1/auth/logout`을 실행해 `204`와 빈 응답 본문을 확인했다.
- Swagger의 `GET /api/v1/auth/session`은 `200`, `authenticated=false`, `sessionState=anonymous`, `user=null`을 반환했다.
- 새 익명 세션의 CSRF로 `POST /api/v1/auth/login/kakao`를 `{"purpose":"signin"}`과 함께 실행해 `200`과 새 인가 URL을 받았다. 같은 Edge 브라우저에서 실제 카카오 인증을 거쳐 callback으로 복귀했다.
- 기존 카카오 로그인 상태가 유지돼 별도 로그인 입력 화면은 나타나지 않았다. 서비스 로그아웃은 카카오 자체 로그아웃과 별개다.
- callback 후 `/api/v1/auth/session`은 곧바로 `authenticated=true`, `sessionState=active`, `onboardingCompleted=true`였다. 회원 ID가 로그아웃 전과 같았고 온보딩 제출을 다시 하지 않았다. 새로고침 후에도 같은 상태가 유지됐다.
- 로그인 후 CSRF는 익명 세션 및 로그아웃 전 활성 세션의 값과 모두 달랐다. 토큰·쿠키·인가 URL 원문은 기록하지 않는다.
- 전용 PostgreSQL DB에서 재로그인 전후를 읽기 전용으로 대조했다. 동일 카카오 식별자의 계정은 1개이며 회원 ID, 테스트 닉네임·생년월일, 온보딩 완료 시각이 동일했다.
- 활성 `tap` 1개와 답변 1행의 ID·생성 시각·Q1 `ost`·Q2 `events`가 모두 동일했다. 해당 `tap`의 세부 관심사 행은 0개, 동의 이력은 기존 4행의 ID·값·시각 그대로였다. 답변이나 동의가 다시 생성되지 않았다.
- 위 대조는 온보딩 데이터 보존에 대한 검증이다. 로그인 시각, OAuth 시도, 세션 등 인증 처리에 필요한 데이터까지 불변이라고 의미하지 않는다.
- 보조 확인 중 브라우저 자동 이동으로 익명 `/auth/session`을 직접 열었을 때 출처 검증 `403`이 한 번 발생했다. 같은 출처의 Swagger 요청은 정상 `200`이었다. 시연에서는 아래와 같이 Swagger에서 익명 세션을 조회한다. 출처 검증 설정은 변경하지 않았다.
- 화면 캡처는 브라우저 캡처 기능의 시간 초과로 저장하지 못했다. 결과는 실제 화면 응답 텍스트와 별도 DB 조회로 확인했다. 코드·스키마 변경 없이 검증을 완료했다.

### 재로그인 Swagger 시연 순서

1. 같은 브라우저에서 `/docs`를 열어 `GET /api/v1/auth/session`을 실행한다. `active` 상태와 현재 회원 ID를 확인하고 `csrfToken`을 **Authorize → CsrfToken**에 적용한다.
2. `POST /api/v1/auth/logout` → **Try it out → Execute**를 실행해 `204`를 확인한다.
3. Swagger에서 `GET /api/v1/auth/session`을 다시 실행한다. `anonymous`를 확인하고 새 CSRF로 Authorize를 갱신한다. 기존 인증이 설정돼 있으면 **Remove authorization** 후 새 값을 적용한다.
4. `POST /api/v1/auth/login/{provider}`에서 `provider=kakao`, 본문 `{"purpose":"signin"}`으로 실행한다. 반환된 인가 URL을 같은 브라우저 프로필에서 열고, 로그인 입력 화면이 나오면 직접 인증한다.
5. callback 후 동일 회원 ID, `active`, `onboardingCompleted=true`를 확인한다. 온보딩 완료 API는 다시 제출할 필요가 없다. 다음 변경 요청 전에는 새 세션의 CSRF로 Authorize를 갱신한다.
6. 위 온보딩 시연의 읽기 전용 SQL을 다시 실행해 Q1·Q2와 답변 생성 시각, 온보딩 완료 시각이 그대로인지 확인한다. 세션 조회 응답 자체에는 Q1·Q2가 포함되지 않는다.

## 확정 사항과 환경

- 카카오·네이버 로그인, T01의 단일 `user` 회원 유형을 사용한다.
- 식별 기준은 `(auth_provider, provider_user_id)`다. 이메일로 계정을 자동 연결하지 않는다.
- **사용자 확정:** 신규 소셜 계정의 이메일이 기존 계정과 겹치면 가입을 중단하고 기존 가입 수단으로 로그인하도록 안내한다.
- 프론트 작업·주소 결정은 보류한다. 프론트 없이 백엔드에서 인증 결과를 확인할 수 있다.
- API 주소는 `https://api.test.me-nu.kr`이다. 2026-10-20 DNS A 조회와 TLS 검증을 포함한 `/health` 호출이 성공했고 `200 {"status":"ok"}`를 받았다. 신규 OAuth 코드의 배포 확인은 아니다.
- 카카오 개발자 앱은 사용자가 생성했다. PC의 `backend/.env`에 키 두 개가 설정되어 있고 localhost callback이 등록됐다. 2026-10-21 실제 로컬 로그인 성공으로 현재 설정의 동작을 확인했다. 네이버 등록은 후속 작업이다.
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

신규 계정은 소셜 식별만 끝난 상태다. `POST /api/v1/onboarding/complete`가 닉네임·생년월일·동의·Topic 및 필수 `answers: {q1,q2}`를 검증하고 저장한다. Q1·Q2는 각각 허용된 문자열 1개이며 `subtopicIds`와 `profileImageId`는 입력하지 않는다. 완료 전에는 보호된 서비스 기능에 접근할 수 없다.

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

- 테스트 DB: 기존 로컬 PostgreSQL 안의 `me_nu_oauth_local`, revision `c0e5f3b8a124`.
- 기존 DB `me_nu`는 revision `e7a4c2d9b812` 그대로 유지했다.
- Redis 접두사 `me_nu_oauth_local`, 쿠키 `menu_oauth_local`을 사용한다.
- `localhost:8000`으로 실행하고 카카오만 활성화한다. 개발용 인증 우회와 개발용 API는 비활성화한다.
- 프론트 복귀 주소는 비워 두고, callback의 code/state가 콘솔에 남지 않도록 접근 로그를 끈다.
- 원격 DB·Redis 및 연결 대상 변경에 쓰일 수 있는 URL query 옵션을 거부한다. 테스트 DB 관리 표식이 없으면 마이그레이션·실행을 거부한다.
- 이 실행 모드에서는 데이터와 로그인 세션이 기존 로컬 API와 분리된다. 온보딩 테스트용 약관 4개와 활성 `anime` Topic을 준비했다. 콘텐츠나 데모 회원은 이 준비 작업에 포함하지 않았다. Topic ID·정책 버전은 API 조회 결과를 사용한다.

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

2026-10-21 후속 revision `c0e5f3b8a124`는 `tap`별 Q1·Q2를 저장하는 `onboarding_answers`를 추가한다. 전용 로컬 테스트 DB에는 적용됐고, 원본 `me_nu`는 `e7a4c2d9b812`로 유지했다. `--prepare`는 전용 DB 마이그레이션만 수행하며 약관·Topic을 자동 생성하지 않는다.

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

2026-10-22 로그 보완: 앱 생성 시 `uvicorn.access`에 필터를 설치해
`/api/v1/auth/login/{provider}/callback`의 쿼리 전체를 로그에서 제거한다.
메서드·경로·응답 상태와 일반 API의 접근 로그는 유지하며, OAuth 검증에 사용하는
원본 요청의 `code`·`state`는 변경하지 않는다. 별도 실행 옵션은 필요 없다.
로그 필터와 OAuth API·설정·온보딩 답변 관련 테스트 149개가 통과했고,
임시 localhost 포트의 실제 Uvicorn HTTP 요청에서도 가짜 콜백 값 제거를 확인했다.

프록시는 별도로 확인한다. Caddy의 사이트 접근 로그가 비활성이어도 런타임 오류
로그에 요청 URI가 포함될 수 있으므로 기본 오류 로그의 `request>uri` 필터도 점검한다.
이 백엔드 수정만으로 서버 Caddy 설정까지 변경되지는 않는다.

## 후속 연결과 검증 범위

- 프론트 연결 시에만 `OAUTH_FRONTEND_ORIGIN`을 채우고 `FRONTEND_ORIGINS`에 등록한다. 기존 SameSite=Lax 쿠키 방식은 같은 사이트의 HTTPS 구성에 맞춘다.
- 화면 복귀 경로는 Pivot 설계의 신규 `/pivot/consent`, 완료 `/pivot/home`, 실패 `/pivot/login?error=...`다. 이번에 프론트 파일을 변경하거나 원격 브랜치를 병합하지 않았다.
- 기존 자동 테스트는 공급자 통신을 대체하고 DB 동작은 SQLite 메모리로 확인한다. 실제 카카오 OAuth의 로컬 브라우저 검증은 위 2026-10-21 결과를 따른다. 실제 PostgreSQL 동시 요청 경합과 원격 쿠키·프록시는 추가 검증 대상이다.
- 검증: 기존 OAuth·인증 자동 테스트 268개 통과, 로컬 실행 스크립트의 DB·Redis 격리 및 원본 환경파일 보존 테스트 18개 통과.
- 초기 로컬 PostgreSQL·Redis 점검에서는 API/DB/Redis 상태, Swagger, 익명 세션, CSRF 거절, 카카오 인가 URL 생성, 로그인 취소 시 state 소비, state 재사용 거절, 로그아웃을 확인했다. 이후 실제 회원 로그인 확인은 위 2026-10-21 검증 기록에 추가했다.
- 재인증·탈퇴 복구, 30일 로그인 유지, 네이버 실계정 검증 및 프론트 연결은 후속 작업이다. 실제 카카오 로그인 세션에서 온보딩 완료와 Q1·Q2 PostgreSQL 저장, 완료 회원의 로그아웃 후 재로그인 및 저장값 보존까지 확인했다.

계약 근거: [API-001](https://app.notion.com/p/3f704c45a9618129aac0fb2b55c85e71), [API-002](https://app.notion.com/p/3f704c45a96181208b87d1d534f49876), [카카오 REST](https://developers.kakao.com/docs/ko/kakaologin/rest-api), [네이버 인증 API](https://developers.naver.com/docs/login/api/api.md). 원본의 프론트 경로는 최종 확인 대상이며, 백엔드 단독 복귀 방식은 이번 작업에서 추가한 계약이다.
