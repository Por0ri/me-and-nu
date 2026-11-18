# 메인 서비스 연결 준비

2026-11-17 결정: 하루 **검수 통과 콘텐츠 20건을 목표**로 한다. 이는 유료 생성
시도를 하루 20회로 제한한다는 뜻이 아니다. 반려·생략·실패에도 비용이 발생할 수
있으므로 일일 시도 상한, 일일 예산, 종료 시각은 별도로 결정한다. 아직 결정되지
않은 예산을 이전의 누적 $5 실험 예산으로 대체하지 않는다.

이 디렉터리는 **비활성 연결 준비 자료**다. `compose.main.yaml`은 기존 Checking
데이터에 연결하는 API 한 개만 정의한다. 새 DB/Redis/마이그레이션/적재/Worker/Beat를
포함하지 않으며 자동 실행 스크립트도 없다. API는 `127.0.0.1:8003`에만 바인딩한다.
`MAIN_PLAN_*` 값은 준비 의도 기록이며 런타임 스위치가 아니다. 기존 Checking
배포·데이터·추천은 변경하지 않는다.

`.env.example`은 비어 있는 자격증명과 미완료 확인 항목을 포함한 후보 설정이다.
서버 반영을 검토할 때만 무시되는 `deploy/main/.env`로 복사하고 권한을 600으로
제한한다. `MAIN_POSTGRES_PASSWORD`/`MAIN_REDIS_PASSWORD`에는 **기존 Checking의 값**을
안전하게 복사해야 한다. 새 비밀번호를 생성하면 기존 데이터에 연결되지 않는다.
OAuth ID/secret은 공급자 설정과 일치하는 서버 비밀값을 쓴다. 값을 화면·로그·Git에
출력하지 않는다. 실제 `docker compose config` 전체 출력도 비밀값을 포함하므로
저장/게시하지 않는다. 테스트는 가짜 값만 사용한다.

## 로컬 검증

저장소 루트에서 실행한다. DB·Redis·네트워크 연결이나 API 키가 필요 없다.
Compose 렌더링 테스트에는 Docker Compose CLI가 필요하지만 Docker daemon은 사용하지 않는다.

```sh
python deploy/main/validate.py prepare
python deploy/main/validate.py preflight
python -m unittest discover -s deploy/main -p 'test_main_validate.py' -v
```

`prepare` 성공은 안전한 비활성 계획이 작성됐다는 뜻이다. 템플릿의 `preflight`는
미확정 항목 때문에 **종료 코드 2가 정상**이다. 출력에는 setting 이름만 나타나고
입력한 값은 표시하지 않는다. 이 검사는 실제 서버 상태를 확인하거나 배포를 승인하지
않는다. 모든 선언이 채워져도 라이브 검증·배포가 완료됐다는 의미는 아니다.

이미지와 기존 DB/Redis 자격증명을 서버의 무시되는 `.env`에 채운 뒤에는
`python deploy/main/validate.py prepare --env deploy/main/.env --check-compose`로
컨테이너를 시작하지 않고 Compose 렌더링을 검사할 수 있다. 상속된 `MAIN_*`와
`COMPOSE_*` 및 manifest 동명 환경변수는 제거하고 지정 파일만 사용한다. 비밀값을
포함하는 Compose 출력은 메모리에서만 검사한다. `preflight`는 모든 선언이 완료된
경우 이 렌더링 검사까지 필수로 수행한다. 실제 `up` 실행 절차는 아직 제공하지 않는다.

테스트의 실제 backend `Settings` 검사는 백엔드 의존성이 설치된 Python에서 실행한다.
원본 설정 모듈 두 개를 임시 폴더에 복사하고 가짜 자격증명만 사용하므로 로컬
`backend/.env`를 읽지 않는다. `production`과 메인 CORS/OAuth 콜백 수용 여부 및
Checking 미리보기 거부를 확인한다.

## 확정된 데이터 연결

현재 Vercel의 기존 `me-nu.kr → www.me-nu.kr` 308 리다이렉트를 유지한다.
메인 FE 주소는 `https://www.me-nu.kr`, API는 `https://api.me-nu.kr`로 준비한다. 이 계획이
라이브 DNS/TLS·OAuth 설정 완료를 의미하지 않는다. 기존 API Settings는 이 주소와
`MENU_DEPLOYMENT_ENV=production`을 지원한다. Checking 전용 가드를 느슨하게 바꿀
필요가 없다.

사용자는 현재 Checking 데이터를 메인에서도 계속 사용하도록 결정했다.
`MAIN_DATA_STRATEGY=reuse_checking`, `MAIN_TARGET_DATABASE=me_nu_checking`으로 고정한다.
API는 기존 `me-nu-checking_data` 외부 네트워크에서 `postgres`/`redis`에 연결한다.
기존 회원·공개 콘텐츠·추천·활동 이력을 복사하거나 초기화하지 않으며 DB 이름 변경,
마이그레이션, seed, 전체 재적재를 하지 않는다. 기존 추천 배치도 유지한다.

`compose.deploy.yaml` 기본값은 별도 `me_nu` DB와 staging 세션이다. 이 파일을 그냥
실행하면 Checking의 공개 콘텐츠·회원·추천이 자동 승계되지 않는다. 기존
`compose.checking.yaml`과 `deploy/checking/validate.py`도 주소만 바꿔 재사용하지 않는다.

현재 Checking의 미리보기 허용 상태는 메인으로 이어받지 않는다. 메인은
`CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED=false`로 준비한다. 사용자가 정한 현재 공개
3,530건은 별도 고정 manifest로 대상을 봉인한 뒤, 미리보기 없이 공개 가능한 정상
승인 상태로 전환하는 준비를 한다. 이 Compose/검증기는 콘텐츠 상태를 바꾸지 않는다.
별도 전환 절차에서 과거 검수 이력·회원·추천을 보존하고, 비공개 글 및 manifest에
없는 새 검수 대기 글이 공개되지 않는지 격리 DB에서 확인해야 한다. 전환 전 원본 DB에
메인 API를 붙이는 것만으로 3,530건이 그대로 노출된다고 간주하면 안 된다.

고정 집합 전환 도구와 실제 읽기 검증·합성 PostgreSQL 결과는
[`MAIN_CONNECTION_PREPARATION_2026-11-17.md`](../../docs/MAIN_CONNECTION_PREPARATION_2026-11-17.md)에
기록했다. 새 LLM 품질 검수를 수행한 것으로 표시하지 않고, 운영자의 기존 공개 범위 유지
결정과 전환 전 상태를 기존 JSON 이력에 남긴다. 운영 DB apply 명령은 아직 제공하지 않는다.
메인 프록시 후보는 [`Caddyfile.example`](./Caddyfile.example)이며 기존 사이트 블록을 대체하지 않는다.

`MAIN_EVENT_DATA_SOURCE=local_demo`는 기존 Checking의 활동 provenance를 보존한다.
이는 `MENU_DEPLOYMENT_ENV=production`과 다른 필드다. 메인 도메인을 연결하면서
활동 출처 라벨·과거 이력·추천 코드·배치·스케줄을 함께 변경하지 않는다.

기존 PostgreSQL의 연결 한도는 30이다. 기존 API 엔진 기본 풀은 기본 5 + overflow 10으로,
같은 기본값의 API를 하나 더 띄우면 추천/운영 접속 여유가 사라질 수 있다. 따라서 메인만
`DATABASE_POOL_SIZE=2`, `DATABASE_MAX_OVERFLOW=0`으로 제한한다. 기존 API 기본값은
유지한다. 실제 연결 수·메모리 여유 확인 전에는 `MAIN_DB_CAPACITY_VERIFIED=false`를
유지한다. 이 설정만으로 라이브 수용량 확인이 끝난 것은 아니다.

## 챗봇 (LLM 키 · 정보사전)

메인 API는 챗봇에 쓸 값 두 개만 서버의 무시되는 `deploy/main/.env`에서 받는다.
`MAIN_LLM_KEY` → API의 `LLM_KEY`, `MAIN_DICT_DATABASE_URL` → API의 `DICT_DATABASE_URL`이다.
비우면 챗봇은 답하지 않는다(사전만 비우면 우리 글로만 답한다). 유료 생성 키
`OPENAI_API_KEY`와 `TMDB_KEY`는 계속 빈 값이다. 두 줄은 비어 있어도 파일에 있어야 검사를
통과하고, 검사 결과에는 값 대신 `chatbot_llm_key`/`chatbot_dictionary`의 `set`/`empty`만 나온다.

- 정보사전은 Checking PostgreSQL 안의 읽기 전용 DB `menu_dict`를 같이 쓴다(2026-11-18
  Checking에 올림, `deploy/checking/README.md` "챗봇 정보사전"). 메인을 위해 다시 올리지 않는다.
  주소는 Checking 서버 파일의 `CHECKING_DICT_DATABASE_URL`과 같은 값을 서버 안에서 복사하고
  출력하지 않는다. 검사는 `menu_dict_reader` 계정, `postgres:5432/menu_dict`, 서비스 DB/Redis와
  다른 64자리 hex 비밀번호만 허용한다. LLM 키도 서버 안에서만 넣고 출력하지 않는다.
- 모든 사용자를 합친 하루 질문 상한은 `MAIN_CHAT_DAILY_TOTAL_LIMIT`(1~999999 정수, 0 = 무제한은 받지
  않음)이다. 같은 DB에서 세므로 메인과 Checking을 합쳐 세고, Checking의
  `CHECKING_CHAT_DAILY_TOTAL_LIMIT`와 늘 같은 값으로 둔다. 바꿀 때는 숫자만 고치고 API 컨테이너를
  다시 만든다(이미지는 다시 만들지 않는다). 한 사람 한도(30번)는 코드 기본값이다.
- 메인 서비스 DB 풀은 2개라서 챗봇은 LLM 답을 기다리기 전에 DB 연결을 돌려준다. 사전 조회는
  서비스 풀과 따로 API마다 최대 4개(기본 2 + overflow 2) 연결을 짧게 쓴다.
- 끄려면 `MAIN_LLM_KEY=`로 비우고 API 컨테이너를 다시 만든다.

## 연결 확정 후 실행 순서

1. 확정된 기존 DB 연결을 검증한다. 전체 Git SHA로 API 이미지를 고정하고,
   검증된 백업·복원 경로와 공개 콘텐츠/추천 개수 기준선을 기록한다.
2. API-only 후보 설정의 이미지·기존 DB/Redis·가용 용량을 확인하고 새 세션 쿠키
   `menu_main_session`과 Redis prefix `me_nu_main`을 쓴다. Checking 쿠키를 복사하지
   않으므로 사용자는 메인에서 다시 로그인한다. dev API/bypass와 미리보기는 끈다.
   자동 migrate/seed나 추천 재계산을 시작 명령에 넣지 않는다.
3. 메인 FE를 `NEXT_PUBLIC_API_BASE_URL=https://api.me-nu.kr`로 빌드한다. 메인 FE를
   Checking API에 직접 붙이지 않는다. CORS 허용 주소는 manifest의 메인 두 주소다.
4. 메인 DNS·HTTPS·프록시와 공급자별 OAuth 콜백을 확인한다. 공급자 설정에 등록할
   주소는 `https://api.me-nu.kr/api/v1/auth/login/kakao/callback` 및
   `https://api.me-nu.kr/api/v1/auth/login/naver/callback`이다. 기존 회원을 이어받으려면
   **Checking과 동일한 Kakao/Naver 애플리케이션(Client ID)을 유지**하고 메인 콜백을
   추가한다. 계정은 provider + provider_user_id로 연결되므로 새 OAuth 앱으로 바꾸면
   동일인의 기존 계정과 이어지지 않을 수 있다. 키/비밀값은 무시되는 서버
   `deploy/main/.env`로만 관리하고, Git의 `.env.example`에는 절대 넣지 않는다.
5. 메인 API/FE 후보에서 로그인→온보딩→피드→상세·이미지→저장→로그아웃을 검증한다.
   공개 콘텐츠 중복·검수 전 노출·추천 피드 회귀와 기존 Checking의 정상 동작을 확인한다.
   완료한 사실만 `MAIN_*_VERIFIED=true`로 기록하고 전환 승인 후에만 배포한다.
6. 문제가 생기면 메인 FE/API 경로를 이전 대상으로 되돌린다. 공유 DB는 삭제/복구하지
   않는다. 메인 API를 내릴 때 기존 Checking 컨테이너·네트워크·볼륨은 건드리지 않는다.

일일 생성은 연결 배포와 별도 단계다. `DAILY_APPROVED_CONTENT_TARGET=20`은 목표일 뿐
자동 보장되지 않는다. 기존 한정 생성 controller는 누적 예산·건수·종료 시각으로
제한한 실험 도구이며, 매일 초기화되는 20건 운영 scheduler가 아니다. KST 날짜별
승인/시도 집계, 중복 방지, 잔여 예산 예약, 종료 시각, 실패·생략 비용 보존을 갖춘
운영 정책을 별도로 검증해야 한다. 그 전까지 자동 수집·유료 생성은 꺼둔다.
