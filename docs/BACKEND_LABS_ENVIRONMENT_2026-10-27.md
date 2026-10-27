# backend-labs 전용 통합 환경 배포 기록

기준일: 2026-10-27. 이 기록은 같은 작업에서 확인한 실제 서버·DNS·Vercel 결과와 로컬 검증 결과를 구분한다. 비밀번호, OAuth secret, 세션 토큰 및 callback의 code/state는 기록하지 않는다.

**후속 코드 계약 안내 (2026-10-27):** 아래 생년월일·만 14세 검사와 화면 검증은 당시 배포 계약의 기록이다. 현재 로컬 백엔드는 `birthDate` 수집·저장·연령 검사를 제거했으며 해당 필드를 보내면 422다. [구현 기준 ERD·API 정합화](ERD_API_ALIGNMENT_2026-10-27.md)에 코드와 환경별 확인 수준을 기록했다. 이번 정합화에서 Labs를 재배포하거나 원격 DB revision을 재확인하지 않았으므로 아래 기록을 새 계약의 배포 완료로 해석하지 않는다.

**최신 사용자 결정:** 생년월일 수집 방식은 통합 완료 후 결정한다. 로컬 코드의 생년월일 제거 상태는 최종 제품 정책이나 Labs 적용 지시가 아니다. 후속 로컬 DB 재조회는 `me_nu=e7a4c2d9b812`, `me_nu_oauth_local=d2f6a9c4e731`을 확인한 것이며 원격 Labs의 재검증 결과와 구분한다.

## 배포 대상

| 항목 | 확인한 값 |
| --- | --- |
| 배포 브랜치 | `backend-labs` |
| 배포 커밋 | `ab409a0105c6cfe8049be07eb20b1662d940abd4` |
| FE 로그인 | [https://labs.me-nu.kr/pivot/login](https://labs.me-nu.kr/pivot/login) |
| API | [https://api.labs.me-nu.kr](https://api.labs.me-nu.kr) |
| Swagger | [https://api.labs.me-nu.kr/docs](https://api.labs.me-nu.kr/docs) |
| 서버 체크아웃 | `/home/ubuntu/apps/me-nu-labs` |
| Compose 프로젝트 | `me-nu-labs` |
| API 호스트 바인딩 | `127.0.0.1:8001` |
| DB / DB 사용자 | `me_nu_labs` / `me_nu_labs` |
| 이미지 | `me-nu-backend-labs:ab409a0105c6cfe8049be07eb20b1662d940abd4` |

Vercel의 `labs.me-nu.kr` 도메인은 **Preview 환경의 `backend-labs` 브랜치 전용**으로 연결했다. 해당 브랜치의 `NEXT_PUBLIC_API_BASE_URL`은 `https://api.labs.me-nu.kr`다. [해당 Vercel 배포](https://vercel.com/ew-2bf9/me-and-nu/Awpnysu71PSkUgwYrfYerQh5gaNz)의 성공 상태를 확인했다. 기존 `test.me-nu.kr`과 `feature/pivot-user-fe`의 연결은 유지했다. `main`에는 반영하지 않았다.

실행 절차와 명령의 기준은 [deploy/labs/README.md](../deploy/labs/README.md), 구성 파일은 [compose.labs.yaml](../compose.labs.yaml)이다. 아래 결과는 초기 배포 커밋에 대한 기록이며 이후 문서/테스트 보완 커밋을 같은 런타임 이미지로 간주하지 않는다.

## 완료한 실제 환경 검증

| 검증 | 결과 |
| --- | --- |
| DNS / HTTPS | 새 Labs FE/API 도메인 연결 및 HTTPS 응답 확인 |
| 서버 설정 guard | `me-nu-labs`, 전용 볼륨·네트워크·DB·Redis·origin·쿠키·포트·리소스 상한 검사 통과 |
| 컨테이너 | Labs API, PostgreSQL, Redis 모두 Healthy |
| 스키마 | 일회성 migration 컨테이너 `Exited (0)` |
| 공개 health 응답 | API health와 DB/Redis 의존성 health 확인 모두 HTTP 200 |
| CORS | `https://labs.me-nu.kr` 허용, 기존 FE origin `https://test.me-nu.kr`은 Labs API에서 거부 |
| 쿠키 | `menu_labs_session` 이름 및 `Secure`, `HttpOnly`, `SameSite=Lax` 확인 |
| 저장소 | 전용 DB/Redis와 `me_nu_labs` Redis 키 접두사 사용 |
| 개발 인증 | 개발 API와 인증 우회 비활성화 유지 |

새 Caddy 사이트 블록은 `api.labs.me-nu.kr`을 `127.0.0.1:8001`로 전달한다. 전체 구성을 검증한 뒤 Caddy reload를 수행했다. 기존 API의 `127.0.0.1:8000` 전달 경로는 유지했다.

## 실제 PostgreSQL seed 결과

읽기 전용 dry-run 계획을 확인한 뒤 Labs 전용 seed를 적용했다. 새 정책 4개, 가상 콘텐츠 2개가 생성됐다. 반복 dry-run은 신규 정책/콘텐츠 모두 0이며 같은 ID를 반환했다.

| 항목 | 실제 Labs 값 |
| --- | --- |
| `anime` Topic ID | `1` |
| `anime-body` 콘텐츠 ID / 상세 모드 | `1` / `full_body` |
| `anime-link` 콘텐츠 ID / 상세 모드 | `2` / `link_excerpt` |
| 최초 적용 | `new_policies=4`, `new_contents=2` |
| 반복 dry-run | `new_policies=0`, `new_contents=0` |
| 적용 직후 SQL 확인 | 사용자 0명, 저장 항목 0개, 콘텐츠 2개 |

ID는 이 DB에서 관찰한 값이다. 프런트엔드나 테스트 코드에 고정하지 않는다. 두 콘텐츠는 실제 기사/작품 정보가 아닌 명시적 Labs 가상 자료이며 이미지가 없다. 정책은 비공개 통합 검증용 임시 문안이다. 기존 회원 DB, 동의 이력, 북마크 또는 Redis 세션을 복사하지 않았다. seed는 회원 계정과 동의를 생성하지 않는다.

## 기존 환경 유지 확인

배포 전후 기존 API 컨테이너의 ID, 이미지, `StartedAt`이 동일했다. 기존 `deploy/.env`의 SHA-256 비교도 일치했으며 해시나 파일 내용은 이 문서에 기록하지 않는다. Caddy의 기존 원문 부분을 그대로 보존한 것을 비교했다. 기존 IP 접근 제한, 공유 VM 설정, Vercel 보호 설정을 유지했다.

원래 작업 폴더에 있던 사용자 미커밋 파일 5개는 이번 배포 변경에 포함하거나 수정하지 않았다. Labs는 같은 VM을 사용하므로 호스트 자원과 Caddy 프로세스는 공유한다. DB/Redis/쿠키/네트워크 분리가 별도 VM의 장애 격리를 의미하지는 않는다.

## OAuth 상태

사용자가 아래 두 callback 추가를 승인했다. 기존 callback을 보존하면서 추가 등록한다.

```text
https://api.labs.me-nu.kr/api/v1/auth/login/kakao/callback
https://api.labs.me-nu.kr/api/v1/auth/login/naver/callback
```

Kakao와 Naver 모두 저장 후 재조회하여 기존 callback 2개와 새 Labs callback 1개가 유지된 것을 확인했다. 두 공급자의 client ID/secret 총 4개 값은 서버 내부에서만 Labs 변수로 옮겼으며 외부 출력이나 Git 기록에 포함하지 않았다. 두 `LABS_*_OAUTH_ENABLED`를 `true`로 설정한 뒤 `.env` 권한 0600, 설정 guard 통과, API 재기동 및 전체 Healthy 상태를 확인했다.

실제 Labs FE에서 카카오 로그인 버튼을 클릭한 뒤 공급자 인증을 거쳐 `/pivot/consent` 화면에 도착했다. 이는 실제 callback의 authorization code 교환과 `onboarding_pending` 세션 발급까지 확인한 결과다. 약관 체크나 온보딩 제출은 수행하지 않았다. 배포 직후 seed 검증 시점에는 사용자 0명이었고, 이 로그인 검증으로 첫 실제 pending 계정이 이후 생성됐다. seed가 회원을 생성한 것은 아니다.

Kakao/Naver의 HTTP 로그인 시작은 모두 200이며 공급자 호스트와 Labs callback이 정확했다. 취소 callback은 303 redirect와 `AUTH_CANCELLED`, state 누락 또는 재사용은 `AUTH_FAILED`로 처리되는 것을 확인했다. 실제 Naver 사용자 로그인 완료는 별도 검증 항목이다. OAuth 설정 후에도 기존 API 컨테이너 상태, 기존 환경 파일 해시, Caddy 기존 내용 보존에 대한 최종 비교 3개가 모두 일치했다.

## 최종 HTTP 회귀 검증

독립 검증의 **31개 항목이 모두 통과**했다.

| 범위 | 확인 결과 |
| --- | --- |
| Labs 및 기존 테스트 API | 앱·DB·Redis health 모두 HTTP 200 |
| Kakao/Naver 로그인 시작 | HTTP 200, 공급자 host 및 Labs callback 일치 |
| OAuth 취소 및 state 보호 | 취소 시 303/`AUTH_CANCELLED`, state 누락·재사용 시 `AUTH_FAILED` |
| CSRF 보호 | CSRF 누락 변경 요청 403 |
| 인증 경계 | 보호 API 무인증 요청 401 |
| 개발 인증 비활성화 | 개발 로그인·가입 경로 404 |
| CORS | Labs FE origin만 허용 |
| 세션 쿠키 | host-only, `Secure`, `HttpOnly`, `SameSite=Lax` |

검증에 사용한 익명 세션은 로그아웃으로 정리했다. 이 자동 HTTP 검증은 실제 사용자의 동의·온보딩 제출이나 Naver 계정 인증을 수행하지 않았다.

## 첫 신규 회원과 남은 사용자 검증

Labs DB는 신규 DB이므로 기존 테스트 환경 회원도 이곳에서는 신규 회원이다. 초기 배포에서는 Swagger를 통한 가입 완료가 필요했으나, 후속 온보딩 연결로 실제 화면에서 가입을 완료할 수 있다. 이 절은 후속 구현의 사용 절차이며 위의 초기 서버 배포 커밋/검증 기록과 구분한다.

1. [Labs 로그인 화면](https://labs.me-nu.kr/pivot/login)에서 본인이 로그인한다. 이미 pending 세션이 있다면 같은 브라우저에서 다음 단계로 진행한다.
2. 약관 내용을 확인하고 본인이 동의할 항목을 선택한다. 선택 약관은 거절할 수 있다.
3. 닉네임과 본인의 생년월일을 입력한다. 만 14세 이상 가입할 수 있다.
4. 애니메이션과 Q1/Q2 답변을 고른 뒤 `가입 완료하고 콘텐츠 보기`를 누른다.
5. 가입 및 세션 확인 후 자동으로 홈에 도착한다. 홈 → 두 상세 모드 → 북마크 → 저장 목록 → 새로고침 후 유지 → 해제를 확인한다.

카카오의 실제 callback은 검증했지만 실제 사용자 본인의 동의에 기반한 첫 가입 제출은 대신 수행하지 않았다. `onboarding_pending`을 회원가입 완료 또는 `active` 세션으로 해석하지 않는다. Naver의 실제 사용자 로그인 완료도 별도 검증 항목이다. 후속 화면 연결의 로컬 검증은 아래에 기록한다.

## 로컬 검증 범위

Compose CLI 렌더링과 격리 guard 회귀 테스트 21개를 통과했다. seed 검증 19개는 SQLite 메모리 DB와 직접 coroutine runner로 통과했다. 검증 항목은 읽기 전용 계획의 무기록, 재실행 ID 유지, 계정·동의·세션 미생성, 전체 트랜잭션 rollback, 기존 편집 보존, 잘못된 대상 사전 거부 및 실제 상세 서비스의 두 표시 모드다.

Windows의 pytest seed 수집 실행은 지연되어 해당 19개 검증을 직접 runner로 수행했다. 테스트용 `AsyncAdapter.execute`와 `ContentReaction` 스키마 보완은 이 배포 기록과 함께 반영하며 배포 로직 변경은 없다. 위의 실제 PostgreSQL seed/migration 결과는 별도로 서버에서 확인했다.

## 후속 Pivot 온보딩 연결 검증

닉네임·생년월일 입력 화면, 정확한 `/api/v1/onboarding/complete` 요청, 동적 `anime` Topic 조회, 교체된 세션/CSRF 확인을 연결했다. 기존 `/consumer`의 별도 클라이언트 계약은 변경하지 않았다. 기본 정보와 동의·답변 초안은 메모리에만 보관하고 가입 완료/세션 만료 시 비운다. 디자인 미리보기와 시연은 실제 가입 API를 호출하지 않는다.

- 전체 FE 단위/통합 테스트 263개 통과, TypeScript 검사 및 Labs 환경변수를 사용한 로컬 production build(Webpack) 통과. Windows 작업트리의 외부 node_modules 링크 때문에 로컬 빌드만 Webpack 모드를 사용했다.
- 온보딩 서비스 단위 검증 및 실제 브라우저 API 클라이언트를 사용한 통합 검증에서 정확한 DTO, 선택 약관 false, 실제 Topic ID, 세션/CSRF 교체 후 북마크, 동시 제출 1회, 정책 변경, 서버 오류 재시도 및 응답 유실 복구를 확인했다.
- 별도 localhost 테스트 API와 실제 화면에서 필수 약관만 동의, 빈 닉네임/만 14세 미만 입력 차단, 기본 정보 수정 시 입력 유지, Q1/Q2 선택, 503 실패 후 재시도를 확인했다.
- 가입 응답을 7초 지연한 상태에서 버튼 잠금과 브라우저 뒤로 가기를 검증했다. 화면을 떠난 뒤에도 공용 세션이 active로 갱신되어 홈으로 이동했다.
- 이어서 홈 → 상세 → 저장 → Saved → 새로고침 후 세션/저장 항목 유지까지 확인했다.

위 화면 검증은 가상의 가입 정보와 구속력 없는 테스트 약관을 가진 loopback fixture에서 수행했다. 실제 Labs 사용자의 약관 선택, 개인정보 또는 계정 상태는 변경하지 않았다. 서버 코드·DB 스키마 변경은 필요하지 않으며 Labs 프리뷰의 FE 변경으로 배포한다.
