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
| 서비스 마이그레이션 | `47c1a8d6b302` |

DB/Redis 비밀번호는 서버의 `init`에서 서로 다른 난수로 생성한다. 다른 환경의 회원·동의·세션·북마크·DB 덤프는 복사하지 않는다. API와 migration의 개발 인증 우회, 개발 API, TMDB/OpenAI 키는 비활성화돼 있다. LLM 키는 챗봇 답에만 쓰며, 서버 전용 `.env`의 `CHECKING_LLM_KEY`로 API 컨테이너에만 넣는다(migration에는 넣지 않는다). 기본값은 빈칸이고 빈칸이면 챗봇 답만 실패한다. 기존 환경 파일에도 `CHECKING_LLM_KEY=` 줄을 추가해야 배포 guard를 통과한다. `check`는 키 값 대신 키가 들어 있는지(`set`/`empty`)만 출력한다. 기존 에이전트 코드나 생년월일 정책은 바꾸지 않는다. DB의 기존 nullable `birth_date`와 값은 유지하며 물리 삭제 `d2`는 포함하지 않는다.

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

`deploy/checking/.env`는 서버에만 두고 권한 0600을 유지한다. 상속된 `CHECKING_*` 값이 파일과 다르면 guard가 중단한다. Compose 렌더링 원문은 비밀번호를 포함하므로 출력하거나 작업 로그에 저장하지 않는다. helper는 모든 동작 전에 전용 프로젝트·DB·Redis·출처·쿠키·포트·볼륨·네트워크·자원 상한과 명시적 마이그레이션 대상을 검사한다. 다른 Compose 파일과 합치거나 임의 플래그를 전달하지 않는다.

`build`는 추가로 `check-build`를 실행해 `CHECKING_IMAGE_TAG`가 현재 체크아웃의 실제 Git HEAD와 일치하는지, `backend/`에 staged/unstaged/untracked 변경이 없는지 확인한다. 불일치하면 이미지 빌드 전에 중단한다. Git 조회는 상속된 `GIT_*` 환경을 사용하지 않고 현재 체크아웃만 읽으며, sudo 소유권 차이를 위해 해당 경로에만 일회성 `safe.directory`를 지정한다. 전역 Git 설정은 변경하지 않는다. 일반 `check`·`up`·`status`·`stop`은 소스 빌드를 하지 않으므로 이 추가 검사 대상이 아니다. 빌드 도중에는 체크아웃을 변경하지 않는다.

`up`은 저장소 healthcheck와 명시적 `alembic upgrade 47c1a8d6b302` 성공 후 API를 시작한다. `head/heads`로 바꾸지 않는다. `d2`가 적용된 과거 DB는 이 경로로 이전하지 않는다. DB/Redis 호스트 포트와 컨테이너 관리자 권한은 공개하지 않는다.

## 정책·카탈로그·콘텐츠

이 구성은 자동 seed를 실행하지 않는다. 최초 가입에는 검토한 정책과 활성 anime Topic, 온보딩 V2 작품·태그·연결 데이터가 필요하다. 팀이 피드를 보려면 검토된 서비스 콘텐츠도 별도로 적재해야 한다. 기존 카탈로그/캐러셀 importer의 dry-run·충돌 검사·별도 승인 계약을 사용하고 대상 DB가 `me_nu_checking`인지 확인한다. Labs 전용 seed는 대상 guard가 다르므로 실행하지 않는다. 테스트 콘텐츠는 모든 화면에서 테스트 자료임을 명확히 표시하고, 합성 테스트 자료를 실제 에이전트 생성·LLM 검수 결과로 표현하지 않는다.

실제 에이전트 산출물을 **검토 전 팀 미리보기**로 공유할 때는 서버의 전용 환경 파일에 `CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED=true`를 명시한다(기본값 `false`). 기존 환경 파일에도 이 키를 추가해야 배포 guard를 통과한다. API와 importer는 Checking 배포 식별자·고정 도메인·전용 DB/계정·컨테이너 endpoint·Secure 쿠키·개발 인증 비활성 조건을 함께 검사한다. 다른 환경에서는 이 플래그를 활성화할 수 없다.

검증한 bundle/PNG만 Checking 실행 컨테이너에 전달한 뒤 `python -m app.seeds.carousel_contents --file <bundle.json> --expected-database me_nu_checking --checking-test-preview`로 dry-run하고, 동일 명령에 `--apply`를 추가해 적재한다. 로컬 사용자·세션·DB 파일이나 API 키는 전달하지 않는다. `--review-record-file`과 함께 사용할 수 없으며 `active / needs_review`, `checking_test_preview` 범위를 기록한다. 기존 QC 보류 결과와 원본 이미지·digest를 유지하고 승인 기록을 만들지 않는다. 카드뉴스 상세에는 AI 생성 안내를 표시한다. 미리보기 및 검토 필요 여부는 API의 `DEV_PREVIEW`와 `REVIEW_REQUIRED`로 확인할 수 있다. 재실행은 동일 콘텐츠를 추가하지 않으며 플래그를 끄면 기존 미리보기 행도 공개 조회에서 제외된다.

## 챗봇 정보사전

챗봇 사실 답(감독·성우·화수·주제가 등)의 근거인 정보사전은 이 스택의 PostgreSQL 안에 별도 DB `menu_dict`로 두고, API만 읽기 전용 계정 `menu_dict_reader`로 읽는다. 서버 전용 `.env`의 `CHECKING_DICT_DATABASE_URL`은 빈칸이거나 `postgresql+psycopg://menu_dict_reader:<64자리 hex>@postgres:5432/menu_dict` 모양이어야 하고, 비밀번호는 DB/Redis 비밀번호와 달라야 한다. 서비스 DB 계정·다른 호스트 주소는 guard가 거부하며 migration에는 넣지 않는다. 기본값은 빈칸이고 빈칸이면 챗봇은 서비스 글만 근거로 답한다. 기존 환경 파일에도 `CHECKING_DICT_DATABASE_URL=` 줄을 추가해야 guard를 통과한다. `check`는 값 대신 `set`/`empty`만 출력한다. 출처는 비상업 기준으로 사용하며, 상업 전환 시 다시 확인한다.

넣는 순서 (비밀번호·덤프 내용은 출력하거나 작업 로그에 남기지 않는다)

1. 서버 디스크 여유를 확인한다. 복원 후 약 0.6 GB가 늘어난다.
2. 원본 정보사전에서 읽기만 하는 덤프를 만든다: `pg_dump -Fc --no-owner --no-privileges -d menu_dict -f menu_dict.dump`. 검증한 덤프만 서버로 옮긴다.
3. 읽기 전용 계정 비밀번호를 `openssl rand -hex 32`로 따로 만들고, 계정과 DB를 만든 뒤 복원한다.

```bash
compose=(docker --context default compose --env-file deploy/checking/.env -p me-nu-checking -f compose.checking.yaml)
"${compose[@]}" exec -T postgres psql -U me_nu_checking -d me_nu_checking -v ON_ERROR_STOP=1 -v pw="$DICT_READER_PASSWORD" <<'SQL'
CREATE ROLE menu_dict_reader LOGIN PASSWORD :'pw';
ALTER ROLE menu_dict_reader SET default_transaction_read_only = on;
CREATE DATABASE menu_dict;
REVOKE CONNECT ON DATABASE me_nu_checking FROM PUBLIC;
SQL
"${compose[@]}" exec -T postgres pg_restore -U me_nu_checking -d menu_dict --no-owner --no-privileges --exit-on-error < menu_dict.dump
"${compose[@]}" exec -T postgres psql -U me_nu_checking -d menu_dict -v ON_ERROR_STOP=1 <<'SQL'
REVOKE ALL ON DATABASE menu_dict FROM PUBLIC;
GRANT CONNECT ON DATABASE menu_dict TO menu_dict_reader;
GRANT USAGE ON SCHEMA dict TO menu_dict_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA dict TO menu_dict_reader;
SQL
"${compose[@]}" exec -T postgres psql -U me_nu_checking -d menu_dict -v ON_ERROR_STOP=1 < backend/scripts/dictionary_indexes.sql
```

4. `backend/scripts/dictionary_indexes.sql`은 덤프에 색인이 이미 있어도 그대로 돌린다(있으면 건너뛴다). 색인이 없으면 조회가 5초 제한을 넘는다.
5. 서버 `.env`에 `CHECKING_DICT_DATABASE_URL`을 넣고 `check` → `up`으로 API를 다시 만든다. 감독·성우·몇 화 질문으로 답 아래 "근거"가 붙는지 확인한다.

끄려면 `CHECKING_DICT_DATABASE_URL=`로 비우고 `check` → `up`한다. `menu_dict`와 계정은 남아도 API가 읽지 않는다.

모든 사용자를 합친 하루 챗봇 질문 상한은 `CHECKING_CHAT_DAILY_TOTAL_LIMIT`(1~999999 정수, 0 = 무제한은 받지 않음)로 정한다. 같은 DB에서 세므로 메인 API와 합쳐 세고, 메인의 `MAIN_CHAT_DAILY_TOTAL_LIMIT`와 늘 같은 값으로 둔다. 바꿀 때는 숫자만 고치고 `check` → `up`한다(이미지는 다시 만들지 않는다). `check` 출력에 지금 숫자가 나온다.

## 관리자 추천 성능 평가 파일

관리자 추천 성능은 서버에서 따로 생성·검증한 오프라인 평가 JSON을 읽는다. API 요청에서
모델 평가나 학습을 실행하지 않는다. 새 Migration과 추천 배치·적재기 변경도 필요하지 않다.

- API에만 `deploy/checking/admin-evaluation/`을 `/app/admin-evaluation`로 읽기 전용 마운트한다.
  다른 호스트 경로·쓰기 마운트·마이그레이션 마운트는 배포 guard가 거절한다. 디렉터리가 없으면
  첫 기동 시 빈 디렉터리를 만든다. 평가 자료가 없는 상태에서도 API는 기동할 수 있다.
- 서버 `.env`의 `CHECKING_ADMIN_RECOMMENDATION_EVALUATION_PATH`는 빈 값(비활성)이거나
  `/app/admin-evaluation/evaluation.json`만 허용한다. 기존 `.env`에 키가 없으면 빈 값으로
  처리한다. 상속된 환경 변수로 값을 바꾸는 것은 거절한다. Main 환경은 변경하지 않는다.
- 검토한 exporter로 만든 파일을 서버의 위 디렉터리에 배치한다. 예시·합성 점수나 과거 문서의
  반올림된 점수를 실평가로 게시하지 않는다. 파일 크기(최대 16 MiB), 스키마, 평가 ID·완료 시각,
  데이터·코드 지문, 같은 케이스별 모델 순위를 확인하고 파일 SHA-256을 기록한다.
- 컨테이너 UID/GID 10001이 읽을 수 있도록 디렉터리 0755, JSON 0644로 둔다. 이 디렉터리에는
  배포할 JSON만 두고 원본 데이터·환경 파일·자격 증명·세션을 넣지 않는다. Git에서는 무시한다.
- 파일 준비 후 위 환경 키를 정확한 컨테이너 경로로 설정하고 `check` → `up`으로 API를
  재생성한다. 실제 관리자 세션으로 `recommendation-performance`의 평가 ID·모델·두 집단별
  NDCG/Hit Rate/표본을 검증한다. API의 `available`은 오프라인 자료의 유효성을 의미하며
  현재 서비스에 배포된 추천 모델의 온라인 성능을 보증하지 않는다.

파일 갱신은 같은 디렉터리에서 임시 파일을 완전히 쓰고 검증·권한 설정한 뒤 `evaluation.json`으로
원자 교체한다. 디렉터리 마운트이므로 이후 요청에 새 파일이 보인다. 개별 파일 마운트로 바꾸면
원자 교체 전 inode가 계속 보일 수 있다. 이전 검증 파일과 SHA는 API 마운트 밖의 백업에 보존한다.
자료 롤백은 이전 검증 파일을 같은 절차로 원자 교체하고 평가 ID를 재확인한다. 연결만 끄려면
환경 키를 빈 값으로 바꿔 `check` → `up`한다. DB downgrade·권한 변경·볼륨 삭제는 하지 않는다.

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
