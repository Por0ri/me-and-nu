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
| 서비스 마이그레이션 | `94d2da693c10` |

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

`up`은 저장소 healthcheck와 명시적 `alembic upgrade 94d2da693c10` 성공 후 API를 시작한다. `head/heads`로 바꾸지 않는다. `d2`가 적용된 과거 DB는 이 경로로 이전하지 않는다. DB/Redis 호스트 포트와 컨테이너 관리자 권한은 공개하지 않는다.

공지 예약 실행은 [관리자 공지 API 계약](../../docs/ADMIN_NOTICES_API_2026-11-23.md)의 별도 적용 순서를 따른다. API 배포만으로 systemd timer가 설치되지는 않는다. Migration과 BE 배포를 확인한 뒤 `publish_scheduled_notices.py` 미리보기와 합성 검증을 거쳐 `systemd/me-nu-checking-notices.service`, `systemd/me-nu-checking-notices.timer`를 설치한다.

## 토론방 (기본 꺼짐)

토론방 API·실시간 채팅·토론 개설 알림은 서버 `.env`의 `CHECKING_DEBATE_ENABLED=true`일 때만 켜진다. 기본값은 `false`이며, 기존 `.env`에 이 키가 없어도 꺼진 채로 guard를 통과한다. 토론 표는 스위치와 관계없이 `up`의 migration(`94d2da693c10`)이 만든다.

- 켜기: `deploy/checking/.env`에 `CHECKING_DEBATE_ENABLED=true`를 넣고 `check` → `up`을 실행한다. 이미지는 그대로이고 API 컨테이너만 새 설정으로 다시 만들어진다.
- 끄기: `false`로 바꾸고 같은 순서로 실행한다. 토론 API와 토론 알림만 사라지고 나머지 서비스와 저장된 토론 데이터는 그대로다.
- 문제가 생기면 이전 커밋으로 되돌리지 말고 스위치를 끈다. 이전 코드는 `94d2da693c10`을 몰라 migration이 실패하고, 토론 데이터가 있으면 downgrade도 거부된다.
- 실시간 허브가 API 메모리에 있으므로 API는 1개 프로세스(Dockerfile `--workers 1`)로 둔다. 같은 DB를 쓰는 main과 Checking 중 한 곳에서만 켠다(베타는 main에서 켜고 Checking은 끈 채로 둔다).
- 개설 기준은 `CHECKING_DEBATE_VOTE_THRESHOLD`(1~100, 기본 10)다. 팀원이 적어 10표를 모으기 어려우면 시험하는 동안만 낮춘다.
- 정리 작업(`python -m app.jobs.finalize_debates`, 10분마다)은 Checking에 예약하지 않는다. 같은 DB를 쓰는 main 서버에서 `deploy/main/main_debates.py` 타이머가 돌린다([main 운영 안내](../main/OPERATIONS.md)). 돌지 않아도 화면은 시각으로 상태를 계산하지만, 끝난 방의 채팅이 DB에 남는다.

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

관리자 추천 성능은 별도로 생성·검증한 평가 JSON을 읽는다. 오프라인 작품 평가와 서비스 콘텐츠
평가는 데이터 단위·정답·평가 기간이 다르므로 파일과 API의 `dataset=offline|service`를 분리한다.
API 요청에서 모델 평가나 학습을 실행하지 않으며 새 Migration은 필요하지 않다.

- API에만 `deploy/checking/admin-evaluation/`을 `/app/admin-evaluation`로 읽기 전용 마운트한다.
  다른 호스트 경로·쓰기 마운트·마이그레이션 마운트는 배포 guard가 거절한다. 디렉터리가 없으면
  첫 기동 시 빈 디렉터리를 만든다. 평가 자료가 없는 상태에서도 API는 기동할 수 있다.
- 서버 `.env`의 `CHECKING_ADMIN_RECOMMENDATION_EVALUATION_PATH`는 빈 값(비활성)이거나
  `/app/admin-evaluation/evaluation.json`만 허용한다. 서비스 평가용
  `CHECKING_ADMIN_SERVICE_EVALUATION_PATH`는 빈 값이거나
  `/app/admin-evaluation/service-evaluation.json`만 허용한다. 기존 `.env`에 키가 없으면
  각각 빈 값으로 처리한다. 상속된 환경 변수로 값을 바꾸는 것은 거절한다. Main 환경은 변경하지 않는다.
- 검토한 exporter로 만든 완료된 평가 파일을 아래 게시 명령으로 배치한다. 예시·합성 점수나 과거 문서의
  반올림된 점수를 실평가로 게시하지 않는다. 파일 크기(최대 16 MiB), 스키마, 평가 ID·완료 시각,
  데이터·코드 지문, 같은 케이스별 모델 순위를 확인하고 파일 SHA-256을 기록한다.
- 컨테이너 UID/GID 10001이 읽을 수 있도록 디렉터리 0755, JSON 0644로 둔다. 이 디렉터리에는
  배포할 JSON만 두고 원본 데이터·환경 파일·자격 증명·세션을 넣지 않는다. Git에서는 무시한다.
- 파일이 아직 없어도 위 환경 키를 정확한 컨테이너 경로로 설정하고 `check` → `up`으로 API를
  재생성할 수 있다. 이때 API는 `unavailable` / `artifact_missing`을 반환하고 화면에는 평가 대기가
  표시된다. 유효한 파일 게시 후 다음 `recommendation-performance` GET부터 별도 재시작 없이
  NDCG@10·Hit Rate@10을 집계한다. 열린 화면은 새로고침하거나 다시 조회한다.
  실제 관리자 세션으로 응답의 평가 ID·모델·두 집단별
  NDCG/Hit Rate/표본을 검증한다. API의 `available`은 선택한 평가 자료가 유효하다는 뜻이다.
  오프라인 작품 점수와 서비스 행동 기반 콘텐츠 점수를 같은 실험 결과로 비교하지 않는다.

평가 작업은 `model/eval/admin_performance_export.py`의 artifact 계약에 맞는 JSON을 생성한다.
모델별 같은 검증 케이스의 정답 순위(`targetRanks`), 표본, 코호트 정의, 평가 시각과 데이터·코드 지문이
필요하다. 정답이 없는 일반 추천 목록이나 클릭 수만 전달하면 NDCG·Hit Rate를 계산할 수 없다.
아래 게시 도구는 검증·게시만 담당한다. 서비스 행동 수집과 평가 실행은 다음 절의 정기 작업이 담당한다.

완료된 파일을 서버의 별도 수신 경로에 복사한 뒤 Checking 호스트에서 실행한다.
호스트 Python에 Pydantic이나 모델 패키지를 설치할 필요가 없다.

```bash
sudo python3 /home/ubuntu/apps/me-nu-checking/deploy/checking/publish_evaluation.py \
  --dataset offline --input /absolute/path/to/completed-evaluation.json
```

명령은 고정 Checking checkout과 실제 실행 이미지 SHA, 건강 상태, 읽기 전용 마운트 및 활성 경로를
확인한다. Docker의 `default` context만 사용하며 Checking API rollout helper와 같은
`/run/lock/me-nu-checking-api-rollout.lock`을 사용한다. `checking.sh`의 build/up/migrate/stop도
이 잠금으로 게시와의 경합을 막는다. 실행 중인 API와 같은 이미지 ID로 네트워크·자격 증명이 없는
일회성 컨테이너에서 입력을 검증한다. 입력은 최대 16 MiB의 일반 파일이어야 하며 심볼릭 링크는 거절한다.
검증 실패 시 기존 게시 파일을 유지하고, 입력 내용이나 상세 검증 오류는 로그에 출력하지 않는다.

검증한 파일은 API가 읽을 수 있는 0644 권한으로 같은 파일시스템의 비공개 staging에 완성한 뒤
선택한 dataset의 고정 파일로 원자 교체한다. `offline`은 v1 `held_out`/`series`, `service`는
v2 `prospective`/`content` 계약만 받으며 서로의 경로를 덮어쓸 수 없다. 기존 파일과 SHA-256은
API 마운트 밖의 `/home/ubuntu/apps/.checking-evaluation-artifacts/backups/<dataset>/`에
0600으로 보존한다. 검증한 내용이 현재 파일과 같으면 교체·추가 백업을 생략한다. 성공 출력에는
dataset·새 파일 SHA·이전 SHA와 백업 이름이 포함된다. DB·권한·Main·추천 적재기와 API 컨테이너는 변경하지 않는다.

디렉터리 마운트이므로 이후 요청에 새 파일이 보인다. 개별 파일 마운트로 바꾸면
원자 교체 전 inode가 계속 보일 수 있다. 이전 검증 파일과 SHA는 API 마운트 밖의 백업에 보존한다.
자료 롤백은 위 명령의 `--dataset`을 맞추고 `--input`에 해당 백업 JSON 절대 경로를 넣어 같은 검증·게시 절차를 거친 뒤
평가 ID를 재확인한다. 백업을 자동으로 정리하지 않으므로 운영자가 보관 공간을 관리한다. 연결만 끄려면
환경 키를 빈 값으로 바꿔 `check` → `up`한다. DB downgrade·권한 변경·볼륨 삭제는 하지 않는다.

## 서비스 추천 평가 자동 수집·게시

`run_evaluation_pipeline.py`는 Checking에서 매시간 저장된 추천 후보·순위를 먼저 고정하고,
관측 기간이 지난 케이스를 평가한 뒤 검증된 서비스 파일만 게시한다. 기본 관측 기간은 7일,
집단별 최소 표본은 20명이다. 따라서 자동화를 켠 직후에는 점수 대신 수집·관측 대기가 정상이다.
기존/신규 사용자는 예측 수집 시점 가입 기간으로 구분하고 사용자당 하나의 최신 성숙 예측을 쓴다.
최소 표본을 채운 집단만 결과에 포함하며 다른 집단은 미집계 상태로 남긴다.

정답은 후보를 고정한 **이후** 관측 기간 안에 발생한 첫 서버 검증 긍정 반응
(`like`, `bookmark`, `preference_positive`) 콘텐츠다. 이후 반응 취소와 무관하게 행동 발생을 평가하며,
관측된 긍정 반응이 없는 사용자를 임의로 실패나 0점으로 만들지 않는다. 같은 시점의 같은 후보 집합으로
저장된 추천 순서·최신순·긍정 반응 인기순을 비교한다. 최종 피드 노출을 재현한 실험이나 시청 완료 기반
작품 평가, 무작위 온라인 A/B 테스트는 아니다. 화면의 평가 조건과 표본을 함께 해석한다.

작업은 실제 API의 `EVENT_DATA_SOURCE`를 읽어 그대로 수집·표시한다. 현재 Checking 기본 출처는
`local_demo`이며 production 자료로 바꾸어 표시하거나 서로 섞지 않는다. 비공개 수집 상태도 DB·출처·
관측 정책에 묶이므로 설정을 바꿔 기존 표본을 재사용할 수 없다. 입력 상한(활성 애니메이션 사용자
2,000명, 이벤트 100,000개, 스냅샷 20,000개)을 넘으면 일부만 평가하지 않고 실패 상태로 중단한다.

Checking 호스트에서 다음 순서로 설치한다. 먼저 새 스키마를 포함한 API를 배포하고
`CHECKING_ADMIN_SERVICE_EVALUATION_PATH=/app/admin-evaluation/service-evaluation.json`을
설정해 `check` → `up`한다. 평가 파일이 아직 없어도 된다.

```bash
sudo python3 /home/ubuntu/apps/me-nu-checking/deploy/checking/evaluation.py dry-run
sudo python3 /home/ubuntu/apps/me-nu-checking/deploy/checking/evaluation.py enable
sudo python3 /home/ubuntu/apps/me-nu-checking/deploy/checking/evaluation.py run
sudo python3 /home/ubuntu/apps/me-nu-checking/deploy/checking/evaluation.py status
```

`dry-run`은 설치할 고정 systemd unit 두 개를 보여준다. `enable`은 실제 API·읽기 전용 마운트·
서비스 경로를 확인한 후 `me-nu-checking-evaluation.service`와 `.timer`만 설치한다. 타이머는
매시간 및 부팅 후 실행되며 누락한 일정은 재시작 후 한 번 처리한다. 수동 실행과 타이머 실행은
별도 작업 잠금으로 중복을 막고, **게시 시점에만** 배포와 같은 잠금을 잡는다. 수집 중 배포 이미지가
바뀌면 후보를 보관하고 게시를 다음 실행에서 다시 확인한다.

수집 컨테이너는 현재 API의 불변 이미지 ID를 사용하며 `me-nu-checking_data` 내부 네트워크에서만
DB를 읽는다. DB 연결 값은 메모리·자식 환경으로 전달하고 명령 인수·journal·공개 상태 파일에 쓰지 않는다.
수집기는 PostgreSQL repeatable-read/read-only 트랜잭션을 강제한다. 컨테이너는 UID 10001,
읽기 전용 루트, 모든 capability 제거, 384 MiB/0.25 CPU/64 PID 제한으로 실행한다. 컨테이너 작업은
10분, systemd 작업은 15분 제한이며 종료 시 그 실행에서 만든 컨테이너만 정리한다.

`/home/ubuntu/apps/.checking-evaluation-pipeline/` 아래의 수집 상태와 게시 대기 파일은 API에
마운트하지 않는다. 상태 디렉터리는 0700, HMAC 키·SQLite·대기 영수증은 0600이며 수집 상태에는
HMAC으로 가명화한 사용자/요청 키를 쓴다. 스냅샷은 최대 90일 보관하며, 사용자·모델 버전별 최신 관찰 완료
예측과 관찰 중인 예측을 남기고 오래된 완료 예측을 정리한다. 탈퇴 사용자·삭제된 탭 자료는 다음 수집 시 제거한다.
완성된 후보를 먼저 원자 저장한 뒤 평가 입력 지문을 기록한다. 게시 실패·응답 유실·프로세스 재시작 때는
대기 후보를 우선 재검증·게시하므로 이미 계산한 결과를 잃지 않는다. 실패 시 기존 게시 파일은 유지하고
다음 시간 실행에서 재시도하며, 동일 입력은 재평가와 중복 게시를 생략한다.

공개 마운트의 `pipeline-status.json`에는 상태(`running`, `waiting`, `unchanged`, `published`,
`failed`), 안전한 사유 코드, 출처·관측 기간·최소 표본·집단별 수와 마지막 실행/성공/게시 시각만 쓴다.
관리자 API가 이 집계 상태를 읽으며 사용자 키·DB 주소·원본 반응·상세 오류는 노출하지 않는다.
`journalctl -u me-nu-checking-evaluation.service`에는 비밀값 없는 결과 요약을 남긴다.

중지할 때는 다음 명령을 쓴다. 타이머만 중지·비활성화하며 진행 중인 작업은 완료될 수 있고
수집 상태·게시 파일·백업은 보존한다. 이 작업은 모델 재학습이나 기존 추천 생성 배치의 일정을 변경하지 않는다.

```bash
sudo python3 /home/ubuntu/apps/me-nu-checking/deploy/checking/evaluation.py disable
python -m unittest discover -s deploy/checking -p 'test_*evaluation*.py' -v
```

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
