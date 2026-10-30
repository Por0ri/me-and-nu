# T02 OAuth 백엔드 설정과 연결 계약

기준일: 2026-10-20, 카카오 로컬 검증 갱신: 2026-10-21, 네이버 실행기·로컬 및 카카오·네이버 원격 전체 로그인 흐름 검증 갱신: 2026-10-23. T02-1에서 시작해 프론트 작업 없이 OAuth 백엔드와 Q1·Q2 온보딩 저장을 구현했다. 별도 로컬 테스트 DB에 마이그레이션을 적용했으며, 카카오와 네이버 모두 실제 인증 → 온보딩 완료 → PostgreSQL 저장값 대조 → 로그아웃 후 재로그인과 저장값 보존까지 확인했다. 원격 API도 기존 코드·이미지에 네이버 설정을 반영한 뒤 카카오와 네이버 각각 실제 로그인 → 온보딩 완료 → 로그아웃 후 동일 회원 재로그인과 새로고침 후 세션 유지를 확인했다. 원격에서는 API 응답으로 프로필·동의·관심 분야의 보존을 대조했으며, 이 후속 검증에서 DB를 직접 조회하지는 않았다. 사용자 지시에 따라 이후 커밋·푸시는 `backend-labs`에서 진행하며, 머지는 별도 지시 전까지 수행하지 않는다.

## 2026-10-27 생년월일·연령 확인 제거 계약

**2026-10-28 후속 상태:** 새 Pivot 흐름의 실제 Kakao 인증·V2 온보딩·피드·상세·북마크·로그아웃과 보호 화면 재접근 차단을 통합 작업 공간에서 확인했다. Naver는 이번 흐름에서 실제 로그인 폼 도달까지만 확인했다. [서비스 흐름 보고서](SERVICE_FLOW_E2E_2026-10-28.md)에 과거 공급자 검증과 최신 결과를 구분한다. 현재 서비스 반영 후보는 f4 단일 head이며 과거 d2 삭제 파일은 포함하지 않는다. 이 문서의 이전 원격 push 안내는 과거 작업 기록으로, 이번 후보의 원격 push·배포는 금지 상태다.

**최신 사용자 지시:** 생년월일을 어떻게 받을지는 통합 완료 후 결정한다. 수집·저장·연령 확인 방식은 미확정이며, 이 절의 제목과 아래 내용은 기존 로컬 구현·검증 기록이다. 이를 최종 수집 정책으로 해석하거나 생년월일 관련 프론트 변경·DB 마이그레이션 추가 적용의 지시로 사용하지 않는다.

현재 로컬 `POST /api/v1/onboarding/complete`에서는 생년월일 입력·저장과 만 14세 미만 검사가 제거되어 있다. 요청은 `nickname`, `accountType:user`, `consents`, `topicId`, `answers:{q1,q2}`를 사용한다. `birthDate`는 `null`도 포함해 전송하면 422 `VALIDATION_ERROR`이며 별도 연령 확인 필드도 없다. `GET/PATCH /api/v1/users/me` 응답에도 `birthDate`가 없다. Q1·Q2, 동의, 회원 ID, OAuth 식별자와 온보딩 완료 상태는 유지한다.

과거 원본·통합 작업 공간의 revision `d2f6a9c4e731`은 `c0e5f3b8a124` 다음에 `user_account.birth_date` 컬럼만 삭제했다. 이 파일은 현재 서비스 반영 후보에서 제외했다. 과거 downgrade로 nullable 컬럼은 복구할 수 있지만 삭제된 생년월일 값은 복원하지 못한다. 새 코드의 모델은 기존 nullable 컬럼이 남은 DB에서도 동작하며, 현재 후보는 해당 컬럼과 값을 보존한다. 이미 d2가 적용된 DB는 별도 migration 이력 검토가 필요하다. 최종 생년월일 정책은 계속 보류한다.

2026-10-27 검증: 관련 자동 테스트 149개가 통과했다. 전용 로컬 PostgreSQL `me_nu_oauth_local`에 다른 DB 연결이 없는 상태에서 `c0e5f3b8a124` → `d2f6a9c4e731`을 적용하고 `birth_date` 컬럼 부재를 확인했다. 적용 전후 생년월일을 제외한 행 데이터 지문이 `user_account` 2행, `tap` 2행, `onboarding_answers` 2행, `consent_history` 8행에서 동일해 기존 회원·TAP·Q1/Q2·동의가 보존되었다. 인증정보와 원본 생년월일은 출력하지 않았다. 원본 DB와 원격 테스트/Labs DB는 변경하지 않았다.

2026-10-27 당시에는 프론트를 변경하지 않아 `birthDate` 요청과 API 422 사이의 불일치를 기록했다. 후속 새 Pivot 가입 화면은 닉네임만 받고 생년월일 입력·전송을 보류하여 현재 API와 맞췄다. 레거시 Consumer와 아래 날짜별 2026-10-21~23 기록은 당시 계약이며 확정 수집 정책이나 이번 재검증 결과를 뜻하지 않는다. 실제 테스트에서는 대상 환경의 배포 계약을 확인한다.

## 2026-10-23 카카오 원격 로그인·온보딩·로그아웃 후 재로그인 검증 결과

- 같은 Edge 브라우저의 원격 Swagger에서 기존 네이버 서비스 세션(`user.id=2`)을 로그아웃해 `204`를 받았고, 후속 세션 조회의 `200`·익명 상태를 확인했다.
- 익명 세션의 최신 CSRF로 Swagger의 `POST /api/v1/auth/login/kakao`에 `{"purpose":"signin"}`을 제출해 `200`을 받았다. 인가 주소의 호스트가 `kauth.kakao.com`, callback이 `https://api.test.me-nu.kr/api/v1/auth/login/kakao/callback`임을 확인했다.
- 같은 브라우저의 기존 카카오 로그인 상태로 실제 인증이 자동 진행됐고, 오류 query 없이 원격 `/api/v1/auth/session`으로 복귀했다. `authenticated=true`, `sessionState=onboarding_pending`, `onboardingCompleted=false`, `user=null`이었으며 익명 세션과 비교해 CSRF가 교체됐다.
- 브라우저에서 `GET /api/v1/policies`와 `GET /api/v1/topics`를 직접 열어 현재 값을 조회했다. 정책 4종은 `test-v1`의 테스트용 임시 안내였고, 활성 애니메이션 Topic은 `code=anime`, `id=1`이었다. 실제 서비스 약관이나 광고 수신 동의 문안을 사용한 검증은 아니다.
- 기존에 승인한 테스트 프로필 닉네임 `온보딩테스트`, 생년월일 `2000-01-01`, `accountType=user`, Q1 `ost`, Q2 `events`를 사용했다. 이용약관·개인정보는 `true`, 광고·마케팅은 `false`로 제출했다.
- 최신 CSRF로 Swagger의 `POST /api/v1/onboarding/complete`를 실행해 `201`, `user.id=3`, `onboardingCompleted=true`를 확인했다. 응답의 `activeTopic.id=1`, `activeTopic.code=anime`, 빈 `subtopicIds`, `answers={"q1":"ost","q2":"events"}`가 제출값과 일치했다. 후속 세션 조회는 `authenticated=true`, `sessionState=active`, `onboardingCompleted=true`였고 CSRF가 다시 교체됐다.
- 로그아웃 전과 재로그인 후 브라우저에서 읽기 전용 API `GET /api/v1/users/me`, `GET /api/v1/users/me/consents`, `GET /api/v1/me/topics` 응답을 대조했다. 테스트 프로필이 제출값과 일치했고 전체 프로필 객체도 전후 동일했다. 정책 4종의 `test-v1` 버전·동의값·기록 시각, 활성 `anime` 1개와 빈 세부 관심사 목록이 모두 유지됐다. 이메일 등 개인 프로필 원문은 검증 기록에 포함하지 않는다.
- 완료 세션의 최신 CSRF로 Swagger의 `POST /api/v1/auth/logout`을 실행해 `204`와 빈 응답 본문을 확인했다. 이어서 같은 Swagger에서 조회한 `GET /api/v1/auth/session`은 `200`, `authenticated=false`, `sessionState=anonymous`, `user=null`이었다.
- 새 익명 CSRF로 `POST /api/v1/auth/login/kakao`에 `{"purpose":"signin"}`을 제출해 `200`과 새 인가 URL을 받았다. 같은 브라우저에서 실제 카카오 인증이 자동 진행됐고, 오류 query 없이 원격 `/api/v1/auth/session`으로 복귀했다. 서비스 로그아웃은 카카오 자체 로그아웃과 별개다.
- 재로그인 직후와 새로고침 후 모두 기존 회원 `user.id=3`, `authenticated=true`, `sessionState=active`, `onboardingCompleted=true`를 확인했다. 로그인 후 CSRF는 익명 세션 및 로그아웃 전 활성 세션의 값과 모두 달랐다. 토큰·쿠키·인가 URL 원문은 기록하지 않는다.
- 재로그인 후 토큰이 포함되지 않은 `/api/v1/me/topics`의 관심 분야 응답 화면을 캡처해 검증 자료로 저장했다.
- 후속 수동 로그아웃에서 사용자가 `403`과 `CSRF 토큰이 올바르지 않습니다.` 응답을 보고했다. 같은 브라우저의 Swagger에서 `GET /api/v1/auth/session`을 재실행하고, **Authorize → CsrfToken**의 기존 인증을 해제한 뒤 최신 토큰 값만 다시 적용하도록 안내했다. `SessionCookie`는 비워 두며, 로그인·온보딩 후 바뀐 CSRF가 Swagger에 자동 갱신되지는 않는다. 이후 사용자가 로그아웃 `204`를 확인했다고 보고했다. 이 후속 해결 결과는 사용자 확인이며 오류 화면을 직접 관찰한 기록은 아니다.
- 온보딩을 다시 제출하거나 계정을 초기화하지 않았다. 이 검증에서는 서버 설정·코드·스키마·배포 상태를 변경하지 않았고 머지도 하지 않았다. DB 직접 조회는 수행하지 않았으므로 Q1·Q2의 재로그인 전후 DB 저장값과 행 수는 별도로 검증하지 않았다. 위 프로필·동의·관심 분야 보존 확인은 실제 API 응답 대조 결과다.

## 2026-10-23 네이버 원격 설정 적용과 로그인 검증 결과

- `https://api.test.me-nu.kr`의 공개 상태와 SSH 읽기 전용 점검으로 네이버 비활성·키 미설정을 확인했다. 이후 사용자 명시 승인으로 PC의 `backend/.env`에서 네이버 Client ID·Client Secret만 서버의 `deploy/.env`에 값 출력 없이 전달했다. 원격 환경파일은 보호된 백업을 남긴 뒤 권한 `0600`을 유지해 갱신했다.
- `NAVER_OAUTH_ENABLED=true`를 적용하고 CORS 오타를 수정해 `FRONTEND_ORIGINS=https://api.test.me-nu.kr,https://me-nu.kr`로 설정했다. 기존 카카오 설정은 유지했다. `OAUTH_PUBLIC_API_ORIGIN=https://api.test.me-nu.kr`, 빈 `OAUTH_FRONTEND_ORIGIN`으로 백엔드 단독 검증을 진행했다.
- 배포 커밋 `53a2bf2`와 기존 API 이미지 ID를 유지하고 API 컨테이너만 재생성해 `healthy`를 확인했다. PostgreSQL·Redis·migrate 컨테이너 ID는 그대로였으며 `git pull`, 머지, 이미지 빌드, 마이그레이션 실행은 하지 않았다.
- `/health`, `/health/db`, `/health/redis`가 모두 `200`으로 정상 응답했다. `Origin: https://me-nu.kr`의 CORS 사전 요청은 `200`, 해당 출처의 `Access-Control-Allow-Origin`, `Access-Control-Allow-Credentials: true`를 반환했다.
- 원격 Swagger에서 `GET /api/v1/auth/session`의 `200`·익명 상태를 확인하고 해당 CSRF로 `POST /api/v1/auth/login/naver`를 실행해 `200`을 받았다. 인가 URL의 callback은 `https://api.test.me-nu.kr/api/v1/auth/login/naver/callback`이었다.
- 같은 브라우저에서 실제 네이버 인증이 자동으로 이어져 오류 query 없이 원격 `/api/v1/auth/session`으로 복귀했다. `authenticated=true`, `sessionState=onboarding_pending`, `onboardingCompleted=false`, `user=null`을 확인했다. 새로고침 후에도 유지됐고 로그인 전 익명 세션과 비교해 CSRF가 갱신됐다. 토큰·쿠키·인가 URL·공급자 프로필 원문은 기록하지 않는다.
- 원격 DB revision은 이미 최신 `c0e5f3b8a124`였고, 현재 정책 버전은 `test-v1`, 활성 `anime` Topic ID는 `1`이었다. 로컬의 `v1`·Topic ID `2`를 원격 요청에 그대로 사용하지 않는다.
- 최초 원격 설정 적용 단계에서는 실제 네이버 로그인과 세션 유지까지만 확인했다. 이후 사용자 요청으로 진행한 원격 온보딩·로그아웃 후 재로그인 결과는 다음 기록을 따른다.

## 2026-10-23 네이버 원격 온보딩·로그아웃 후 재로그인 검증 결과

- 같은 Edge 브라우저의 `https://api.test.me-nu.kr/docs`에서 `GET /api/v1/policies`와 `GET /api/v1/topics`를 실행했다. 정책 4종은 모두 실제 서비스 약관이 아닌 `test-v1`의 테스트용 임시 안내였으며, 활성 애니메이션 Topic은 `code=anime`, `id=1`이었다.
- 기존에 승인한 테스트 프로필 닉네임 `온보딩테스트`, 생년월일 `2000-01-01`, Q1 `ost`, Q2 `events`를 사용했다. 이용약관·개인정보는 `true`, 광고·마케팅은 `false`로 제출했다. 실제 서비스 약관이나 광고 수신 동의 문안을 사용한 검증은 아니다.
- 최신 CSRF로 Swagger의 `POST /api/v1/onboarding/complete`를 실행해 `201`, `user.id=2`, `onboardingCompleted=true`를 확인했다. 응답의 `activeTopic.id=1`, `activeTopic.code=anime`, 빈 `subtopicIds`, `answers={"q1":"ost","q2":"events"}`가 제출값과 일치했다. 후속 세션 조회는 `authenticated=true`, `sessionState=active`, `onboardingCompleted=true`였고 CSRF가 교체됐다.
- 로그아웃 전과 재로그인 후 읽기 전용 API `GET /api/v1/users/me`, `GET /api/v1/users/me/consents`, `GET /api/v1/me/topics` 응답을 대조했다. 전체 프로필 객체가 일치했고, 정책 4종의 `test-v1` 버전·동의값·기록 시각이 모두 유지됐다. 관심 분야 응답에도 활성 `anime` 1개와 빈 세부 관심사 목록이 동일하게 유지됐다. 이메일 등 개인 프로필 원문은 검증 기록에 포함하지 않는다.
- 완료 세션의 최신 CSRF로 Swagger의 `POST /api/v1/auth/logout`을 실행해 `204`와 빈 응답 본문을 확인했다. 이어서 같은 Swagger에서 조회한 `GET /api/v1/auth/session`은 `200`, `authenticated=false`, `sessionState=anonymous`, `user=null`이었다.
- 새 익명 CSRF로 `POST /api/v1/auth/login/naver`에 `{"purpose":"signin"}`을 제출해 `200`과 새 인가 URL을 받았다. 같은 브라우저의 기존 네이버 로그인 상태로 실제 인증이 자동 진행됐고, 오류 query 없이 원격 `/api/v1/auth/session`으로 복귀했다. 서비스 로그아웃은 네이버 자체 로그아웃과 별개다.
- 재로그인 직후와 새로고침 후 모두 기존 회원 `user.id=2`, `authenticated=true`, `sessionState=active`, `onboardingCompleted=true`를 확인했다. 로그인 후 CSRF는 익명 세션 및 로그아웃 전 활성 세션의 값과 모두 달랐다. 토큰·쿠키·인가 URL 원문은 기록하지 않는다.
- 재로그인 후 토큰이 포함되지 않은 `/api/v1/me/topics`의 관심 분야 응답 화면을 캡처해 검증 자료로 저장했다.
- 온보딩을 다시 제출하거나 계정을 초기화하지 않았다. 이 후속 검증에서는 서버 설정·코드·스키마·배포 상태를 변경하지 않았고 머지도 하지 않았다. DB 직접 조회는 수행하지 않았으므로 Q1·Q2의 재로그인 전후 DB 저장값과 행 수는 별도로 검증하지 않았다. 위 프로필·동의·관심 분야 보존 확인은 실제 API 응답 대조 결과다.

## 2026-10-23 네이버 로컬 로그인 검증 결과

- 사용자가 네이버 동의를 완료한 뒤 브라우저가 오류 query 없이 `http://localhost:8000/api/v1/auth/session`으로 복귀했다.
- 응답에서 `authenticated=true`, `sessionState=onboarding_pending`, `onboardingCompleted=false`, `user=null`을 확인했다. 이는 소셜 인증을 마친 온보딩 미완료 상태다.
- 페이지를 새로고침한 뒤에도 같은 인증 상태가 유지됐고, 로그인 전 익명 세션과 비교해 CSRF가 갱신됐음을 확인했다. 토큰·쿠키·인가 URL 원문은 기록하지 않는다.
- 첫 로그인 단계에서는 네이버 OAuth 로그인과 로컬 세션 유지를 확인했다. 이어서 사용자 요청으로 진행한 온보딩과 로그아웃 후 재로그인 결과는 다음 기록을 따른다.

## 2026-10-23 네이버 로컬 온보딩·로그아웃 후 재로그인 검증 결과

- 같은 Edge 브라우저의 Swagger에서 `GET /api/v1/policies`와 `GET /api/v1/topics`를 실행했다. 정책 4종은 모두 `v1`의 로컬 테스트용 임시 문안이었으며, 활성 애니메이션 Topic은 `code=anime`, `id=2`였다.
- 기존에 승인한 테스트 프로필 닉네임 `온보딩테스트`, 생년월일 `2000-01-01`, Q1 `ost`, Q2 `events`를 사용했다. 이용약관·개인정보는 `true`, 광고·마케팅은 `false`로 제출했다. 실제 서비스 약관이나 광고 수신 동의 문안을 사용한 검증은 아니다.
- 최신 CSRF로 `POST /api/v1/onboarding/complete`를 실행해 `201`, `onboardingCompleted=true`, 제출한 Topic·답변을 확인했다. 이후 세션은 `authenticated=true`, `sessionState=active`, `onboardingCompleted=true`였고 CSRF가 교체됐다.
- 완료 세션의 CSRF로 `POST /api/v1/auth/logout`을 실행해 `204`와 빈 응답 본문을 확인했다. 이어서 Swagger의 `GET /api/v1/auth/session`은 `200`, `authenticated=false`, `sessionState=anonymous`, `user=null`을 반환했다.
- 새 익명 CSRF로 `POST /api/v1/auth/login/naver`에 `{"purpose":"signin"}`을 제출해 `200`과 새 인가 URL을 받았다. 같은 브라우저에서 실제 네이버 인증을 거쳐 오류 query 없이 로컬 `/api/v1/auth/session`으로 복귀했다. 기존 네이버 로그인 상태가 유지돼 별도 입력 화면은 나타나지 않았다. 서비스 로그아웃은 네이버 자체 로그아웃과 별개다.
- 재로그인 직후와 새로고침 후 모두 동일 회원 ID, `authenticated=true`, `sessionState=active`, `onboardingCompleted=true`를 확인했다. 로그인 후 CSRF는 익명 세션 및 로그아웃 전 활성 세션의 값과 모두 달랐다. 토큰·쿠키·인가 URL 원문은 기록하지 않는다.
- 전용 PostgreSQL DB `me_nu_oauth_local`의 관리 표식을 확인하고 읽기 전용 트랜잭션으로 재로그인 전후를 대조했다. 동일 네이버 식별자의 계정은 1개였고, 회원 ID·테스트 프로필·온보딩 완료 시각이 그대로 유지됐다. 공급자 식별자·이메일·프로필 원문은 검증 출력에 포함하지 않았다.
- 활성 `anime` tap 1개, Q1 `ost`·Q2 `events`의 답변 1행, 세부 관심사 0행을 확인했다. 동의 이력은 `v1` 4행으로 필수 2종 `true`, 선택 2종 `false`였고 알림 설정의 `marketing=false`도 확인했다. 재로그인 전후 해당 행의 ID·값·시각이 모두 일치해 온보딩 데이터가 보존되고 중복 생성되지 않았음을 확인했다. 로그인 시각·OAuth 시도·세션처럼 인증 처리에 필요한 데이터는 불변 비교에서 제외했다.
- 온보딩을 다시 제출하거나 계정을 초기화하지 않았다. 이 로컬 검증 단계에서 애플리케이션 코드·스키마·프론트·원격 환경은 변경하지 않았고 완료된 로컬 테스트 계정 데이터를 보존했다. 이후 진행한 원격 OAuth 설정 적용과 검증은 위 별도 기록을 따른다.

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
3. `POST /api/v1/onboarding/complete`에서 **Try it out**을 누르고 다음 본문을 입력한다. 닉네임·Q1·Q2·동의는 시연자가 선택한다. 생년월일이나 연령 확인값은 제출하지 않는다. 필수 2종은 동의해야 하고 선택 2종은 미동의로도 완료할 수 있다.

```json
{
  "nickname": "온보딩테스트",
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

이미 완료한 계정으로 온보딩을 다시 제출하면 계약상 `409 ONBOARDING_ALREADY_COMPLETED`다. 이를 재현하기 위해 계정이나 완료 상태를 초기화하지 않는다. `403`이면 응답의 오류 코드와 최신 CSRF·세션 상태를 확인한다. `422`이면 요청 형식과 Q1·Q2 허용값을 확인하고, 제거된 `birthDate`·`subtopicIds`·`profileImageId`를 보내지 않았는지 확인한다. 생년월일 기반 `AGE_RESTRICTED` 오류는 새 코드에서 사용하지 않는다.

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
- 프론트 서비스 URL은 사용자 확인으로 `https://me-nu.kr`로 확정했다. 프론트 작업은 보류하며, 로컬 검증은 프론트 없이 백엔드에서 인증 결과를 확인한다.
- API 주소는 `https://api.test.me-nu.kr`이다. 2026-10-20 DNS A 조회와 TLS 검증을 포함한 `/health` 호출이 성공했고 `200 {"status":"ok"}`를 받았다. 이후 2026-10-23 기존 배포 코드에 네이버 설정을 적용해 원격 실제 로그인과 세션 유지를 확인했다.
- 카카오 개발자 앱은 사용자가 생성했다. PC의 `backend/.env`에 키 두 개가 설정되어 있고 localhost callback이 등록됐다. 2026-10-21 실제 로컬 로그인 성공으로 현재 설정의 동작을 확인했다.
- 2026-10-23 사용자가 네이버 앱 등록과 PC의 `backend/.env`에 `NAVER_CLIENT_ID`·`NAVER_CLIENT_SECRET` 입력을 완료했다고 보고했다. 이후 실제 네이버 로컬 로그인·온보딩·로그아웃 후 재로그인과 세션 유지를 확인했고, 사용자 승인으로 같은 키를 서버에 반영해 원격 로그인도 확인했다. 개발자 콘솔을 직접 점검한 기록은 아니다.
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

신규 계정은 소셜 식별만 끝난 상태다. `POST /api/v1/onboarding/complete`가 닉네임·동의·Topic 및 필수 `answers: {q1,q2}`를 검증하고 저장한다. Q1·Q2는 각각 허용된 문자열 1개이며 `birthDate`, `subtopicIds`, `profileImageId`는 입력하지 않는다. 연령 확인 절차는 없으며 완료 전에는 보호된 서비스 기능에 접근할 수 없다.

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
2. PC 웹의 **서비스 URL**은 `https://me-nu.kr`, **Callback URL**은 위 네이버 백엔드 주소로 등록한다. 서로 다른 항목이다. 로컬 테스트에는 `http://localhost:8000/api/v1/auth/login/naver/callback`도 추가한다.
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

활성 공급자의 키·시크릿 누락, 잘못된 origin, 원격 API의 Secure 쿠키 누락은 설정 오류로 거절한다. 두 공급자를 동시에 활성화할 필요는 없다. 2026-10-23 원격 서버에는 기존 카카오 설정을 유지한 채 네이버 키·활성화와 CORS 수정을 적용했다. 상세 검증 범위는 위 원격 기록을 따른다.

로컬 API를 사용하려면 `OAUTH_PUBLIC_API_ORIGIN=http://localhost:8000`, `SESSION_COOKIE_SECURE=false`로 설정하고 로컬 callback도 등록한다. 카카오는 공식 튜토리얼에서 localhost HTTP를 안내한다. 네이버 로컬 주소는 콘솔 등록 조건을 별도로 확인한다. 팀 테스트의 기준 주소는 위 HTTPS 도메인이다.

## PC에서 카카오·네이버 로그인 테스트

Windows PowerShell에서 아래 전용 실행 스크립트를 사용한다. 기존 `backend/.env`를 재작성하지 않으며, 스크립트가 프로세스 안에서 테스트 DB·세션 설정과 선택한 공급자의 활성 상태를 적용한다. `--provider`를 생략하면 기존과 같이 카카오만 활성화한다.

2026-10-23 실행기 테스트 66개가 통과했다. 공급자 선택·키 누락·격리·원본 설정 보존·CLI 분기를 확인했으며, DB·Redis·서버는 모의 객체로 대체했다. 실제 네이버 인증 성공을 확인한 결과는 아니다.

```powershell
cd C:\Users\USER\me_nu\backend
.\.venv\Scripts\python.exe scripts/run_local_oauth.py
```

네이버만 사용하려면 준비 상태를 확인한 뒤 실행한다. `--check`는 서버를 시작하지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts/run_local_oauth.py --provider naver --check
.\.venv\Scripts\python.exe scripts/run_local_oauth.py --provider naver
```

| 옵션 | 활성 공급자·필수 키 검사 대상 |
| --- | --- |
| 생략 또는 `--provider kakao` | 카카오만 |
| `--provider naver` | 네이버만. 카카오 키는 검사하지 않음 |
| `--provider all` | 카카오·네이버 모두 |

- 테스트 DB: 기존 로컬 PostgreSQL 안의 `me_nu_oauth_local`, revision `c0e5f3b8a124`.
- 기존 DB `me_nu`는 revision `e7a4c2d9b812` 그대로 유지했다.
- Redis 접두사 `me_nu_oauth_local`, 쿠키 `menu_oauth_local`을 사용한다.
- `localhost:8000`으로 실행하고 선택한 공급자만 활성화한다. `.env`의 해당 `*_OAUTH_ENABLED`가 `false`여도 실행 프로세스에서는 선택 옵션을 따른다. 개발용 인증 우회와 개발용 API는 비활성화한다.
- 프론트 복귀 주소는 비워 두고, callback의 code/state가 콘솔에 남지 않도록 접근 로그를 끈다.
- 원격 DB·Redis 및 연결 대상 변경에 쓰일 수 있는 URL query 옵션을 거부한다. 테스트 DB 관리 표식이 없으면 마이그레이션·실행을 거부한다.
- 이 실행 모드에서는 데이터와 로그인 세션이 기존 로컬 API와 분리된다. 온보딩 테스트용 약관 4개와 활성 `anime` Topic을 준비했다. 콘텐츠나 데모 회원은 이 준비 작업에 포함하지 않았다. Topic ID·정책 버전은 API 조회 결과를 사용한다.

키가 없을 때는 `backend/.env`에 선택한 공급자의 키를 넣는다. 카카오는 `KAKAO_CLIENT_ID`에 REST API 키, `KAKAO_CLIENT_SECRET`에 Client Secret을 넣고, 네이버는 `NAVER_CLIENT_ID`·`NAVER_CLIENT_SECRET`을 사용한다. 키 값은 출력하거나 Git에 넣지 않는다. 나머지 OAuth 실행 설정은 이 스크립트가 적용한다.

각 공급자 콘솔에는 사용할 공급자의 아래 주소를 추가한다. 기존 테스트 서버 callback도 유지한다.

```text
http://localhost:8000/api/v1/auth/login/kakao/callback
http://localhost:8000/api/v1/auth/login/naver/callback
```

상태 확인과 새 PC에서의 테스트 DB 준비 명령은 다음과 같다. 아래 `--check`는 기본 공급자인 카카오를 확인하며, 네이버나 둘 다 확인하려면 각각 `--provider naver` 또는 `--provider all`을 함께 사용한다. `--prepare`는 기존 앱 DB를 변경하지 않고 전용 DB만 생성·마이그레이션한다. 준비 모드에서는 `--provider` 선택과 관계없이 모든 공급자가 비활성화되므로 OAuth 키가 필요 없다.

```powershell
.\.venv\Scripts\python.exe scripts/run_local_oauth.py --check
.\.venv\Scripts\python.exe scripts/run_local_oauth.py --prepare
```

현재 PC에서는 DB 준비가 완료됐으므로 선택한 공급자의 `--check` 후 실행하면 된다. 먼저 8000번 포트의 기존 API 실행 여부와 공급자 설정을 확인하고 중복으로 시작하지 않는다. 공급자 선택을 바꿔 다시 실행해야 한다면 기존 API를 실행한 터미널의 `Ctrl+C`로 종료한 뒤 새 옵션으로 실행한다.

브라우저에서 `http://localhost:8000/docs`를 열고 아래 수동 검증을 진행한다. 중간에 `127.0.0.1`이나 다른 브라우저 프로필로 바꾸지 않는다.

## DB 변경

새 revision `b9d4e2a7f013`은 `oauth_attempt` 테이블을 추가한다. 원본 state·공급자 토큰·시크릿은 저장하지 않는다. 만료행은 요청 제한 집계 기간이 지난 뒤 새 로그인 시작 시 정리한다.

세션이 Redis 모드여도 OAuth 시도는 PostgreSQL에서 처리하므로 마이그레이션이 필요하다. 회원 테이블의 기존 소셜 식별 컬럼·UNIQUE는 재사용한다.

이 절의 최초 T02 revision 직전은 T01의 `a8c3d1f6e902`다. 당시 `upgrade head` 설명에는 기존 `signup_channel` 제거도 포함됐다. 현재 서비스 후보의 적용 대상은 `150a601074af`로 명시하며 서버의 현재 revision과 적용 범위를 먼저 확인한다. 최초 검증에서는 별도 로컬 테스트 DB에만 적용했으며, 기존 로컬 DB와 원격 서버 DB에는 적용하지 않았다.

2026-10-21 후속 revision `c0e5f3b8a124`는 `tap`별 Q1·Q2를 저장하는 `onboarding_answers`를 추가한다. 전용 로컬 테스트 DB에는 적용됐고, 원본 `me_nu`는 `e7a4c2d9b812`로 유지했다. 2026-10-23 원격 점검에서 서버 DB도 이미 `c0e5f3b8a124`임을 확인했으며 이번 설정 적용 과정에서는 마이그레이션을 실행하지 않았다. `--prepare`는 전용 DB 마이그레이션만 수행하며 약관·Topic을 자동 생성하지 않는다.

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
- 기존 자동 테스트는 공급자 통신을 대체하고 DB 동작은 SQLite 메모리로 확인한다. 실제 카카오 OAuth의 로컬 브라우저 검증은 위 2026-10-21, 네이버의 로컬 및 카카오·네이버의 원격 전체 로그인 흐름은 2026-10-23 결과를 따른다. 두 공급자 모두 원격 HTTPS 쿠키·프록시를 통한 로그인·온보딩·로그아웃 후 재로그인과 세션 유지를 확인했으며, 실제 PostgreSQL 동시 요청 경합은 추가 검증 대상이다.
- 기존 검증 기록: OAuth·인증 자동 테스트 268개와 로컬 실행기 테스트 18개가 통과했다. 2026-10-23에는 공급자 선택 검증을 추가해 로컬 실행기 테스트 66개가 통과했다.
- 초기 로컬 PostgreSQL·Redis 점검에서는 API/DB/Redis 상태, Swagger, 익명 세션, CSRF 거절, 카카오 인가 URL 생성, 로그인 취소 시 state 소비, state 재사용 거절, 로그아웃을 확인했다. 이후 실제 회원 로그인 확인은 위 2026-10-21 검증 기록에 추가했다.
- 재인증·탈퇴 복구, 30일 로그인 유지, 프론트 연결 및 원격 Q1·Q2의 DB 직접 대조는 후속 작업이다. 로컬에서는 실제 카카오와 네이버 로그인 세션으로 온보딩 완료와 Q1·Q2 PostgreSQL 저장, 완료 회원의 로그아웃 후 재로그인·새로고침에 따른 세션 유지 및 저장값 보존까지 확인했다. 원격에서도 카카오와 네이버 각각 온보딩 완료·로그아웃 후 동일 회원 재로그인과 세션 유지를 확인했고, API 응답으로 프로필·동의·관심 분야 보존을 대조했다. 각 환경·공급자의 검증 범위는 위 날짜별 기록을 따른다.

계약 근거: [API-001](https://app.notion.com/p/3f704c45a9618129aac0fb2b55c85e71), [API-002](https://app.notion.com/p/3f704c45a96181208b87d1d534f49876), [카카오 REST](https://developers.kakao.com/docs/ko/kakaologin/rest-api), [네이버 인증 API](https://developers.naver.com/docs/login/api/api.md). 원본의 프론트 경로는 최종 확인 대상이며, 백엔드 단독 복귀 방식은 이번 작업에서 추가한 계약이다.
