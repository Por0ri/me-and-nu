# backend-labs 독립 통합 환경

`backend-labs`의 FE/BE 통합 검증 전용 환경이다. FE는 `https://labs.me-nu.kr`, BE는 `https://api.labs.me-nu.kr`를 사용한다. 기존 `test.me-nu.kr`, `api.test.me-nu.kr`, `me-nu-deploy` 프로젝트 및 운영 데이터와 연결하지 않는다. 이 문서는 배포 절차이며 배포 완료 증거는 아니다.

## 격리 경계

| 항목 | Labs 전용 값 |
| --- | --- |
| Compose 프로젝트 | `me-nu-labs` (`labs.sh`가 `-p`로 고정) |
| API 호스트 포트 | `127.0.0.1:8001` |
| DB / DB 사용자 | `me_nu_labs` / `me_nu_labs` |
| 볼륨 | `me-nu-labs_postgres-data`, `me-nu-labs_redis-data` |
| 네트워크 | `me-nu-labs_data`(internal), `me-nu-labs_egress` |
| 세션 저장소 / 키 접두사 | 전용 Redis / `me_nu_labs` |
| Secure 세션 쿠키 | `menu_labs_session` (API 호스트 한정) |
| 이미지 | `me-nu-backend-labs:<40자리 커밋 SHA>` |

DB/Redis 비밀번호는 초기화 시 각각 새 난수로 생성한다. 앱은 별도 고정 세션 서명 키 대신 세션별 난수 토큰을 발급하며 전용 Redis와 쿠키가 세션을 분리한다. 기존 사용자·동의·북마크·DB 덤프·Redis 데이터를 복제하지 않는다. 개발 로그인 및 인증 우회는 항상 꺼져 있다.

상시 컨테이너 메모리 상한은 API 768 MiB + PostgreSQL 768 MiB + Redis 192 MiB, CPU 상한 합계는 1.5 CPU다. 일회성 migrate/seed는 각각 256 MiB/0.25 CPU다. 8 GB/2 CPU 호스트에서 소규모 기능 검증을 위한 초기값이며, Docker/OS/기존 서비스/이미지 빌드는 별도 자원을 쓴다. 같은 서버의 CPU·디스크·장애 영향까지 물리적으로 격리되는 것은 아니다. 운영에 가까운 부하 테스트는 별도 호스트에서 한다.

## 준비 및 첫 실행

새 디렉터리에 검토한 `backend-labs` 커밋을 체크아웃한다. 실제 Labs 서버 경로는 `/home/ubuntu/apps/me-nu-labs`다. 기존 서버 체크아웃에서 브랜치를 변경하거나 기존 `.env`를 복사하지 않는다. Docker Engine과 Compose v2, Python 3, Bash가 필요하다. Docker 권한이 없는 Ubuntu 계정은 아래 helper를 `sudo bash ...`로 실행한다. 초기화는 일반 사용자로 실행하고 `.env`의 접근 권한은 0600을 유지한다.

```bash
git rev-parse HEAD
python3 deploy/labs/validate.py init --image-tag <위에서-확인한-40자리-SHA>
sudo bash deploy/labs/labs.sh check
sudo bash deploy/labs/labs.sh build
sudo bash deploy/labs/labs.sh up
sudo bash deploy/labs/labs.sh status
sudo bash deploy/labs/labs.sh seed
sudo bash deploy/labs/labs.sh seed-apply
```

`init`은 기존 `.env`를 덮어쓰지 않는다. 기본 공급자 플래그는 모두 `false`다. `check`는 로컬 Compose 렌더링만 수행하며 실제 비밀번호/해석된 설정을 출력하지 않는다. 매 helper 동작 전에 프로젝트, URL, DB, Redis, 쿠키, 네트워크, 볼륨, 포트, 리소스 상한을 검사한다. Compose 설정을 합치거나 임의 플래그를 전달하지 않는다. `docker compose config` 원문에는 비밀번호가 포함되므로 작업 기록에 출력하지 않는다.

`up`은 PostgreSQL/Redis의 healthcheck와 `alembic upgrade head` 성공 후 API를 기동한다. `seed`는 PostgreSQL 읽기 전용 트랜잭션으로 계획만 출력하고 `seed-apply`만 기록한다. seed는 별도 `tools` profile로 평상시 자동 실행되지 않는다. 실패한 migration/seed는 원인을 해결한 뒤 같은 Labs helper로 재실행한다. DB/Redis의 호스트 포트는 노출하지 않는다.

이미지 빌드에 붙인 SHA가 실제 체크아웃과 일치해야 한다. 런타임 CPU 제한이 이미지 빌드에 적용되지는 않으므로 기존 서비스 부하를 확인한 뒤 빌드한다. 기존 앱이 사용하는 저장소/태그를 재태깅하지 않는다. 볼륨 생성 후 `.env`의 저장소 비밀번호만 임의로 바꾸면 기존 볼륨의 인증값과 불일치한다.

## DNS, TLS, FE

`labs.me-nu.kr`은 Vercel에서 `backend-labs` 브랜치에만 연결하고, 그 브랜치에만 `NEXT_PUBLIC_API_BASE_URL=https://api.labs.me-nu.kr`를 설정한 뒤 새 빌드를 만든다. 기존 FE 브랜치의 변수와 도메인은 유지한다. 브라우저에서 내려받은 빌드가 Labs API로 호출하는지도 확인한다.

`api.labs.me-nu.kr`은 BE 서버로 연결한다. 기존 Caddy 구성에 아래 사이트 블록을 추가하되 기존 사이트 블록을 유지하고, 전체 설정 검증 성공 후 reload한다.

```caddyfile
api.labs.me-nu.kr {
    reverse_proxy 127.0.0.1:8001
}
```

새 사이트에서 OAuth callback 쿼리(code/state)를 access log로 기록하지 않는다. 기존 공통 access log 정책도 점검한다. 새 인증서 발급과 HTTPS 응답을 확인한다. 첫 기동 후 host proxy에서 들어오는 실제 Docker gateway IP를 확인하여 `LABS_FORWARDED_ALLOW_IPS`에 그 정확한 IP만 추가하고 API를 재생성한다. `*`/CIDR 전체 허용은 validator가 거절한다. 서비스가 추가될 때 생기는 네트워크 gateway 값을 추측해 하드코딩하지 않는다.

## OAuth 등록

등록할 callback은 아래 두 개다.

```text
https://api.labs.me-nu.kr/api/v1/auth/login/kakao/callback
https://api.labs.me-nu.kr/api/v1/auth/login/naver/callback
```

가능하면 Labs 전용 공급자 앱을 사용한다. 기존 공급자 앱을 공유한다면 기존 callback을 삭제하거나 기존 secret을 회전시키지 않고 Labs callback/service URL을 추가한다. Kakao 앱의 플랫폼/Redirect URI와 Naver 앱의 서비스 URL·callback 등록, 테스트 사용자 접근 범위를 해당 콘솔에서 확인한다. 공급자 콘솔 권한이나 등록이 준비되지 않았다면 관련 플래그를 `false`로 둔다. 서버·DNS만 준비된 것으로 OAuth 로그인을 검증했다고 기록하지 않는다.

등록 완료 후에만 서버의 `deploy/labs/.env`에 해당 `LABS_*_CLIENT_ID`, `LABS_*_CLIENT_SECRET`을 직접 안전하게 채우고 공급자 플래그를 `true`로 바꾼다. 비밀값을 저장소, 채팅, 배포 기록에 붙이지 않는다. 기존 공급자 자격증명을 재사용하더라도 DB/Redis 비밀번호·세션 쿠키·키 접두사는 Labs 전용 값을 유지한다. `check`, `up`을 다시 실행하고 로그인 시작 응답의 redirect URI가 정확히 Labs callback인지 확인한다.

## 새 DB의 콘텐츠와 첫 회원

별도 seed는 `anime` Topic, Labs Subtopic, 가상 콘텐츠 2개와 임시 정책 4개만 생성한다. 콘텐츠 하나는 `full_body`, 하나는 `link_excerpt`를 검증하며 모두 이미지가 없다. 출처는 예약 예시 도메인 `example.com`이고 실제 기사/작품 정보를 주장하지 않는다. 본문형은 API 계약에 필요한 synthetic AgentRun/Draft를 함께 만들지만 `agent_invoked=false`, `node_count=0`을 기록하며 실제 LLM을 실행하지 않는다. 재실행은 같은 ID를 유지하고 사람이 수정한 기존 fixture나 정책을 덮어쓰지 않는다. 전체 기록은 한 트랜잭션이다.

정책 문안은 내부 동의 저장 기능의 검증용으로만 제공한다. 공개 서비스의 정식 약관을 대신하지 않는다. seed는 회원, 동의 이력, 관심 Topic 구독, 인증 세션을 만들지 않는다. 기존 테스트 환경 회원도 Labs에서는 신규 회원이다.

현재 FE의 온보딩 제출 서비스가 연결되지 않았다면 사용자가 직접 다음 기존 API 절차를 수행한다.

1. Labs FE에서 실제 OAuth로 로그인한다. 새 계정은 `onboarding_pending`이다.
2. 같은 브라우저에서 `https://api.labs.me-nu.kr/docs`를 연다. `GET /api/v1/auth/session`의 `csrfToken`을 Swagger `Authorize`에 입력한다.
3. `GET /api/v1/policies`와 `GET /api/v1/topics`를 읽고 현재 `policyVersion`과 `anime` Topic의 실제 ID를 확인한다.
4. `POST /api/v1/onboarding/complete`에 본인이 정한 `nickname`, 실제 가입 정보 `birthDate`, `accountType: "user"`, 본인의 정책별 `consents` 선택, 실제 `topicId`, 본인의 Q1/Q2 단일 `answers`를 제출한다. ID·생년월일·동의 답변을 임의로 만들어 대신 제출하지 않는다.
5. 세션이 교체되므로 다시 `GET /api/v1/auth/session`으로 `active`와 새 CSRF 토큰을 확인하고 Labs FE 홈으로 돌아온다.

검증 범위는 로그인 → active 세션 → 홈 피드 → 두 상세 모드 → 북마크 → 저장 목록 → 새로고침 유지 → 북마크 해제다. 이미지 없는 카드도 확인한다. 실제 테스트 회원으로 수행하고 원래 테스트 환경의 세션/데이터가 유지되는지도 확인한다.

## 갱신과 중지

커밋을 변경할 때 `LABS_IMAGE_TAG`를 검토한 새 SHA로 바꾸고 `check`/`build`를 수행한다. 스키마 변경이 있으면 Labs API를 `stop`한 뒤 `migrate`, `up` 순으로 실행한다. `up`이 이미 완료된 migration 컨테이너를 재사용할 수 있으므로 갱신 시 migration 실행을 생략하지 않는다. 이전 이미지 태그로의 복구는 스키마 하위 호환을 확인한 뒤 수행한다. 자동 downgrade나 볼륨 삭제는 제공하지 않는다. `stop`은 Labs API만 정지하며 DB/Redis와 데이터는 남긴다.

검증 명령(백엔드 테스트 의존성이 설치된 Python):

```bash
python -m pytest deploy/labs/test_validate.py deploy/labs/test_seed.py -q -p no:cacheprovider
```

이 검증은 Compose 렌더링 및 메모리 DB에서 격리 guard, 무기록 dry-run, 재실행, 충돌 거부, 트랜잭션 rollback, 두 상세 계약을 확인한다. 컨테이너 기동·실제 PostgreSQL migration·외부 OAuth 검증을 대체하지 않는다.
