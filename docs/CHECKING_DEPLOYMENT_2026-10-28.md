# Checking 배포 기록 — 2026-10-28

**진행 상태:** Checking 프론트·API·전용 DB/Redis 배포, DNS·TLS, OAuth 활성화, 테스트 콘텐츠 적재를 완료했다. 공개 Checking 주소에서 실제 Kakao 로그인부터 V2 온보딩·홈·상세·저장·로그아웃·재접근 차단까지 확인했다. Naver는 시작 API와 callback 등록을 확인했으며 이번 Checking에서 실제 사용자 콜백은 검증하지 않았다. 실제 에이전트 생성·LLM 검수는 보류 상태다.

## 확인한 공개 환경

| 항목 | 값·상태 |
| --- | --- |
| 프론트 | `https://checking.me-nu.kr` |
| API | `https://api.checking.me-nu.kr` |
| 백엔드 이미지/서버 커밋 | `26d3410da0018ef432d89b9950919dde5488dff1` |
| 프론트 동작 변경 커밋 | `0a667023da34dc14dcc8fc5d5d088a99eaf6c6cb` — Checking 루트만 로그인으로 이동 |
| API A 레코드 | `52.78.2.187` — 서버에서도 DNS 해석 확인 |
| API 프록시 대상 | `127.0.0.1:8002` |
| Caddy 활성 설정 | `/etc/caddy/Caddyfile` |
| 변경 전 원본 백업 | `/etc/caddy/Caddyfile.checking-before-20261028T002244Z` |

Checking만 원격 배포 대상이다. 배포 담당자가 기존 `main`·`backend-labs` 참조 보존을 확인했으며, 에이전트 코드와 생년월일 정책을 변경하지 않았다. 이전 로컬 통합 검증은 [서비스 흐름 보고서](SERVICE_FLOW_E2E_2026-10-28.md), 브랜치 역할은 [Checking 통합 기록](CHECKING_INTEGRATION_2026-10-28.md)과 구분한다.

## Caddy 적용과 기존 서비스 보존

프록시 담당자가 실제 SSH로 다음 순서를 확인·실행했다.

1. 기존 Caddy 1,029바이트 원문과 `root:root`·`0644` 권한, OAuth 쿼리의 `code`·`state` 제거 필터를 확인했다. Checking 블록은 없었다.
2. 원본 백업과 Checking 블록만 덧붙인 후보 파일을 생성하고, 후보 전체에 `caddy validate`를 실행해 exit 0을 확인했다.
3. 새 API의 loopback `/health`, `/health/db`, `/health/redis`가 모두 200인 것을 확인한 뒤 후보를 원자적으로 교체했다.
4. `systemctl reload caddy`가 exit 0으로 완료됐다. Caddy PID는 전후 `180051`로 같았고 restart는 실행하지 않았다.
5. 원본 바이트 전체가 새 설정의 앞부분에 그대로 남아 있으며 기존 권한·OAuth 쿼리 필터도 유지됐다. 기존 테스트·Labs API 컨테이너의 ID와 시작 시각이 전후 동일했다.

추가한 사이트는 아래 하나다. 기존 `api.test`→8000과 `api.labs`→8001 블록은 수정하지 않았다.

```caddyfile
api.checking.me-nu.kr {
    tls {
        issuer acme {
            disable_tlsalpn_challenge
        }
    }
    reverse_proxy 127.0.0.1:8002
}
```

변경 후 외부 PC에서 인증서 검증을 끄지 않고 다음 HTTPS 요청을 실행했다. 배포 담당자도 세 호스트의 공개 `/health`를 교차 확인했다.

| 호스트 | `/health` | `/health/db` | `/health/redis` |
| --- | --- | --- | --- |
| `api.checking.me-nu.kr` | 200 | 200 | 200 |
| `api.test.me-nu.kr` | 200 | 200 | 200 |
| `api.labs.me-nu.kr` | 200 | 200 | 200 |

이 프록시 작업에서는 방화벽, 기존 서비스 환경파일·컨테이너, DB 데이터, 개인키를 변경하지 않았다. 비밀값은 기록하지 않는다.

## 프론트·OAuth의 별도 확인

배포 담당자는 Vercel의 Checking 배포가 Ready임을 확인했다. 사용자 승인에 따라 `checking.me-nu.kr` 하나만 Unprotected Domains 예외로 저장했고, 프로젝트의 기존 Require Log In 설정은 유지했다. 공개 Checking 호스트의 테스트 HTML 3개가 HTTP 200이며 `[테스트]`·LLM 미사용 표시를 포함함을 확인했다. 이것을 OAuth나 DB 콘텐츠 적재 완료로 해석하지 않는다.

Kakao·Naver 콘솔에는 기존 callback 3개를 보존하고 아래 Checking callback을 각각 네 번째로 추가한 뒤 저장·재조회했다. 공급자 등록과 서버 OAuth 활성화, 실제 사용자 로그인 성공은 서로 다른 단계다.

- `https://api.checking.me-nu.kr/api/v1/auth/login/kakao/callback`
- `https://api.checking.me-nu.kr/api/v1/auth/login/naver/callback`

Checking 서버의 OAuth 두 공급자를 활성화했다. 기존 Labs의 OAuth client ID/secret 4개만 서버 내에서 복사했으며 기존 파일의 전후 바이트 해시 일치, Checking DB/Redis 암호 불변을 확인했다. `FORWARDED_ALLOW_IPS=127.0.0.1,172.23.0.1`은 실제 Checking egress gateway를 확인한 값이다. seed 완료 후 Checking API만 한 번 재생성했고 기존 6개 컨테이너 ID는 유지됐다. LLM/TMDB/OpenAI 키와 개발 API·개발 인증 우회는 비활성 상태다.

## 데이터 초기화와 검증

별도 `me_nu_checking` DB에서 `f4b8c1d9a620`을 확인하고 bootstrap → 카탈로그 → 캐러셀 순서로 각각 dry-run, apply, 반복 실행을 검증했다.

| 자료 | 결과 |
| --- | --- |
| 활성 Topic / 테스트 정책 | 1 / 3 |
| 작품 / 태그 / 연결 | 30 / 38 / 140 |
| 테스트 캐러셀 / PNG / 출처 | 3 / 18 / 3 |
| 반복 적재 | 변경 0, 캐러셀 `unchanged=3` |
| 초기 적재 직후 개인 상태 | 사용자·DB 세션·OAuth 시도·동의·tap·저장·Redis 세션 모두 0; 이후 로그인 QA에서 새 데이터 생성 |

자료는 `[테스트]` 별빛 우체국(3장), 구름 열차(5장), 달빛 극장(10장)이다. 합성 문구와 도형이며 실제 에이전트 실행이나 LLM 검수 결과가 아니다. 기존 만료 시각 `2026-11-26T18:30:51Z`를 유지했다. 기존 개인 DB나 세션은 복사하지 않았다. 허용 목록의 bundle/review/manifest/PNG 21개만 배포했다. 기록은 서버 `/home/ubuntu/me-nu-checking-seed-20261028/`에 있다.

## 기능·접근 검사

- 프론트 테스트 299개, 타입 검사 성공. Checking 배포 guard 67개, bootstrap 검사 14개 통과.
- Checking 정확한 host의 `/`만 `/pivot/login`으로 HTTP 307 이동한다. 설치된 Next 라우팅 유틸 11개 경우와 실제 공개 응답으로 확인했다.
- 세션 쿠키는 `menu_checking_session`, Secure/HttpOnly이며 응답은 `private, no-store`다. 테스트용 익명 세션의 logout은 204였다.
- 두 OAuth 시작 API 모두 200, 공급자 도메인과 Checking callback·state 존재를 확인했다. 키·토큰은 기록하지 않았다.
- CORS는 Checking 출처만 허용하고 쿠키를 허용한다. Labs/알 수 없는 출처의 preflight는 400, 비로그인 상세·PNG는 401, OpenAPI에 개발 경로는 없다.
- Naver 앱은 원래대로 **개발 중**이며 등록된 멤버만 로그인할 수 있다. 제공 정보/생년월일 설정은 변경하지 않았다.

## 실제 공개 주소의 사용자 흐름

Edge에서 `checking.me-nu.kr`을 사용해 아래 순서로 확인했다. 기존 로그인한 Kakao 계정의 OAuth 인증이 새 Checking DB에 테스트 사용자로 연결됐으며, 다른 환경의 회원 정보는 복사하지 않았다.

1. 카카오 로그인 버튼 → 공급자 인증 → Checking 동의 화면 복귀 성공.
2. 실제 정책 팝업이 `checking-20261028-v1`의 팀 내부 테스트 안내임을 확인했다. 필수 안내 두 항목만 선택했고 선택 마케팅은 꺼 두었다.
3. 닉네임과 애니메이션 Topic 입력 후 Q1에서 `너의 이름은.` 1개, Q2에서 판타지·몸 바뀜 2개, Q3에서 OST 1개를 선택해 가입을 완료했다. 생년월일 입력 단계는 없다.
4. 홈에서 테스트 캐러셀 3개와 1308×1632 PNG 로딩을 확인했다. 상세에서는 표지·본문·Checking 출처 링크가 표시됐다.
5. 상세 저장 → SAVE → 새로고침 후 항목 유지 → 상세 재진입 시 저장 상태 유지 → 저장 해제 → 빈 저장함을 확인했다.
6. MY 로그아웃 → 로그인 화면 → `/pivot/home` 직접 재접근 시 로그인으로 이동하는 것을 확인했다. 최종 브라우저 콘솔 오류는 0개였다.

Checking DB의 테스트 계정과 취향은 이 QA에서 새로 생성됐다. 테스트 북마크는 해제하고 서비스에서는 로그아웃했다. Naver 앱 멤버 제한, 홈 검색·공유/좋아요·일부 MY 기능의 기존 준비 중 상태는 이 배포에서 확장하지 않았다. 이번 결과를 미구현 기능이나 모든 공급자의 실사용 검증 완료로 해석하지 않는다.

## 도메인 작업 완료 재확인

최신 프론트 커밋의 선별 통합에 앞서 공개 환경을 다시 확인했다. DNS·TLS·Vercel 도메인 연결·접근 보호 예외 설정에 남은 적용 작업은 없다.

- `checking.me-nu.kr` CNAME은 기존 지정 Vercel 대상과 일치한다. 루트 주소를 브라우저로 열면 `/pivot/login`으로 이동하며 Vercel 계정 로그인 없이 카카오·네이버 로그인 버튼이 표시된다.
- Vercel의 Checking 배포 `5f90f8e`가 `Ready`·`Latest`이며 `checking.me-nu.kr` 도메인에 연결되어 있음을 확인했다. 이후 코드 변경을 배포하면 이 커밋은 도메인 완료 시점의 기준 기록이 된다.
- Checking/Test/Labs의 `/health`, `/health/db`, `/health/redis` 9개 응답이 모두 200이었다. Checking 출처의 CORS 요청은 허용되고 Labs·알 수 없는 출처는 거부됐다.
- 익명 상세·슬라이드 이미지 요청은 401이었다. 두 OAuth 공급자는 활성화되어 있고 Checking 콜백 주소가 일치한다. 재확인에서는 OAuth 시도·회원·세션 데이터를 추가하지 않았으며, 실제 Kakao 사용자 흐름 검증은 앞 절의 결과다.
- Naver의 개발 멤버 제한과 실제 사용자 콜백 미검증은 계속 남은 검증 범위다. 도메인/DNS 미완료 사항은 아니다.

이 재확인으로 설정·DB·기존 서비스·main 참조를 변경하지 않았다. 후속 프론트 변경은 별도 통합 기록과 검증 결과로 관리한다.