# Checking 서비스 환경

`Checking` 브랜치의 팀 검증용 환경이다. FE는 `https://checking.me-nu.kr`, API는 `https://api.checking.me-nu.kr`을 사용한다. 이 파일은 배포 절차이며 실제 배포 완료 기록은 아니다. 기존 Main/Test/Labs 설정과 데이터는 변경하지 않는다.

| 항목 | Checking 전용 값 |
| --- | --- |
| Compose 프로젝트 | `me-nu-checking` |
| API 포트 | `127.0.0.1:8002` → 컨테이너 `8000` |
| DB / DB 사용자 | `me_nu_checking` / `me_nu_checking` |
| 볼륨 | `me-nu-checking_postgres-data`, `me-nu-checking_redis-data` |
| 네트워크 | `me-nu-checking_data`(internal), `me-nu-checking_egress` |
| Redis 키 접두사 / Secure 쿠키 | `me_nu_checking` / `menu_checking_session` |
| 백엔드 이미지 | `me-nu-backend-checking:<40자리 커밋 SHA>` |
| 서비스 마이그레이션 | `f4b8c1d9a620` |

DB/Redis 비밀번호는 서버의 `init`에서 서로 다른 난수로 생성한다. 다른 환경의 회원·동의·세션·북마크·DB 덤프는 복사하지 않는다. API와 migration의 개발 인증 우회, 개발 API, LLM/TMDB/OpenAI 키는 비활성화돼 있다. 기존 에이전트 코드나 생년월일 정책은 바꾸지 않는다. DB의 기존 nullable `birth_date`와 값은 유지하며 물리 삭제 `d2`는 포함하지 않는다.

상시 제한은 API 512 MiB/0.50 CPU, PostgreSQL 512 MiB/0.35 CPU, Redis 128 MiB/0.15 CPU로 합계 1,152 MiB/1.0 CPU다. PostgreSQL은 shared buffers 128 MB/최대 연결 30개, Redis는 데이터 48 MB와 `noeviction`을 사용한다. 일회성 migration은 256 MiB/0.25 CPU다. 8 GB/2 CPU 공유 호스트의 소규모 팀 검증을 위한 초기 상한이며 이미지 빌드·OS·기존 서비스 자원은 별도다. 실제 포트 충돌과 여유 자원을 먼저 확인한다.

## 서버 준비

기존 체크아웃을 전환하지 말고 Checking 전용 디렉터리에 검토한 커밋을 준비한다. Docker Engine/Compose v2, Python 3, Bash가 필요하다. 아래 초기화는 비밀값을 출력하지 않고 기존 `.env`를 덮어쓰지 않는다.

```bash
git rev-parse HEAD
python3 deploy/checking/validate.py init --image-tag <확인한-40자리-SHA>
sudo bash deploy/checking/checking.sh check
sudo bash deploy/checking/checking.sh build
sudo bash deploy/checking/checking.sh up
sudo bash deploy/checking/checking.sh status
```

`deploy/checking/.env`는 서버에만 두고 권한 0600을 유지한다. 상속된 `CHECKING_*` 값이 파일과 다르면 guard가 중단한다. Compose 렌더링 원문은 비밀번호를 포함하므로 출력하거나 작업 로그에 저장하지 않는다. helper는 모든 동작 전에 전용 프로젝트·DB·Redis·출처·쿠키·포트·볼륨·네트워크·자원 상한과 `f4` target을 검사한다. 다른 Compose 파일과 합치거나 임의 플래그를 전달하지 않는다.

`build`는 추가로 `check-build`를 실행해 `CHECKING_IMAGE_TAG`가 현재 체크아웃의 실제 Git HEAD와 일치하는지, `backend/`에 staged/unstaged/untracked 변경이 없는지 확인한다. 불일치하면 이미지 빌드 전에 중단한다. Git 조회는 상속된 `GIT_*` 환경을 사용하지 않고 현재 체크아웃만 읽으며, sudo 소유권 차이를 위해 해당 경로에만 일회성 `safe.directory`를 지정한다. 전역 Git 설정은 변경하지 않는다. 일반 `check`·`up`·`status`·`stop`은 소스 빌드를 하지 않으므로 이 추가 검사 대상이 아니다. 빌드 도중에는 체크아웃을 변경하지 않는다.

`up`은 저장소 healthcheck와 명시적 `alembic upgrade f4b8c1d9a620` 성공 후 API를 시작한다. `head/heads`로 바꾸지 않는다. `d2`가 적용된 과거 DB는 이 경로로 이전하지 않는다. DB/Redis 호스트 포트와 컨테이너 관리자 권한은 공개하지 않는다.

## 정책·카탈로그·콘텐츠

이 구성은 자동 seed를 실행하지 않는다. 최초 가입에는 검토한 정책과 활성 anime Topic, 온보딩 V2 작품·태그·연결 데이터가 필요하다. 팀이 피드를 보려면 검토된 서비스 콘텐츠도 별도로 적재해야 한다. 기존 카탈로그/캐러셀 importer의 dry-run·충돌 검사·별도 승인 계약을 사용하고 대상 DB가 `me_nu_checking`인지 확인한다. Labs 전용 seed는 대상 guard가 다르므로 실행하지 않는다. 테스트 콘텐츠는 모든 화면에서 테스트 자료임을 명확히 표시하고 실제 에이전트 생성·LLM 검수로 표현하지 않는다.

## 도메인·TLS·FE·OAuth

FE 호스팅에서 `Checking` 브랜치에만 `checking.me-nu.kr`을 연결하고 `NEXT_PUBLIC_API_BASE_URL=https://api.checking.me-nu.kr`로 새 빌드를 만든다. 기존 Main/Test/Labs 도메인과 환경변수는 유지한다. DNS에서 API 도메인은 백엔드 호스트로 연결하고 기존 Caddy 사이트를 유지한 채 다음 블록을 추가한다.

```caddyfile
api.checking.me-nu.kr {
    reverse_proxy 127.0.0.1:8002
}
```

전체 Caddy 설정을 검증한 뒤 reload하고 HTTPS를 확인한다. OAuth callback의 code/state 쿼리가 access log에 남지 않도록 한다. host proxy 요청에서 확인한 정확한 Docker gateway IP만 `CHECKING_FORWARDED_ALLOW_IPS`에 추가한다. wildcard/CIDR는 허용하지 않는다.

공급자 콘솔에는 기존 callback을 유지하고 다음 Checking 주소를 추가한다.

```text
https://api.checking.me-nu.kr/api/v1/auth/login/kakao/callback
https://api.checking.me-nu.kr/api/v1/auth/login/naver/callback
```

각 공급자의 앱/허용 사용자/도메인 설정과 실제 서버 자격 증명을 확인한 후에만 해당 `CHECKING_*_OAUTH_ENABLED=true`를 설정한다. 기본값은 모두 `false`다. 자격 증명은 서버 전용 `.env`에만 넣고 다시 `check`한 뒤 Checking API를 재생성한다. 실제 로그인 → 동의 → 닉네임 → 작품/태그/소비 유형 → 가입 완료 → 피드/상세/보호 이미지 → 저장/새로고침 → 로그아웃 흐름을 별도로 확인한다. guard 통과는 외부 OAuth 성공을 의미하지 않는다.

## 갱신·중지·검증

새 커밋은 `CHECKING_IMAGE_TAG`와 실제 체크아웃 SHA를 맞춰 `check`/`build`한다. 스키마 변경이 있으면 Checking API만 `stop`하고 `migrate` 후 `up`한다. 기존 완료 migration 컨테이너가 재사용될 수 있으므로 갱신 시 명시적 migration을 생략하지 않는다. `stop`은 API만 멈추며 DB/Redis와 데이터는 유지한다. 자동 downgrade, 볼륨 삭제, 기존 환경 재태깅은 제공하지 않는다.

```bash
python -m pytest deploy/checking/test_checking_validate.py -q -p no:cacheprovider
```

테스트는 합성 값으로 Compose를 렌더링하고 격리 guard와 임시 파일 초기화를 확인한다. 실제 컨테이너 기동·DB migration·DNS/TLS·OAuth·브라우저 검증은 수행하지 않는다.
