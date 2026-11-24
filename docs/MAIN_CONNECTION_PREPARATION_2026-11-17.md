# 메인 연결·기존 공개 콘텐츠 전환 준비 (2026-11-17)

사용자는 기존 Checking 회원·콘텐츠·검수 이력·추천 데이터를 그대로 메인에 연결하고,
현재 공개된 3,530건을 미리보기 없이 유지하도록 결정했다. 이번 범위는 준비와
격리 검증이다. 서비스 DB 상태 변경, 메인 DNS/배포 승격, 자동 수집, 유료 생성은 하지 않는다.

## 연결 결정과 현재 상태

- 기준 코드: Checking `a908d2d3da22bb1e91bc40cb705f292c1c471c27`에서 분리한
  `codex/main-beta-connection`.
- Vercel `ew / me-and-nu`의 기존 canonical은 `www.me-nu.kr`이다.
  `me-nu.kr`은 308로 www에 연결하도록 이미 등록돼 있다. 이 방향을 유지한다.
- 조회 당시 Production은 main `dfebc48d3a65acd7a10a1b941f8c9e61b72144da`의 옛 배포였다.
  DNS만 연결하면 최신 Checking 화면이 제공되는 것이 아니다.
- Vercel에서 apex/www 모두 Invalid Configuration, 서버 DNS 조회에서도 미해결이었다.
  현재 표시된 요구값은 apex A `216.150.1.1`, www CNAME
  `1b4d5e1d5141e60c.vercel-dns-017.com.`이다. 실제 적용 시 프로젝트 화면에서 다시 확인한다.
- `api.me-nu.kr`은 DNS/프록시 미설정이다. 기존 Checking API는 정상이며
  `api.checking.me-nu.kr → 127.0.0.1:8002`이다. 메인은 별도 API의 loopback 8003으로 준비한다.
- 메인 API는 기존 `me_nu_checking` DB와 Redis를 사용한다. 새 데이터베이스,
  마이그레이션, seed 또는 추천 배치를 시작하지 않는다.
- 메인 전용 세션 `menu_main_session` / `me_nu_main`을 사용하므로 메인에서 다시
  로그인한다. 기존과 동일 OAuth 앱에 콜백을 추가해야 기존 회원 ID가 이어진다.

읽기 전용 집계 당시 회원 8, 콘텐츠 4,047, 추천 라운드 70, 추천 항목 7,000이었다.
현재 PG 연결은 전체 7/30, Checking 연결 2였고 메모리 여유는 약 5GB였다.
메인 API만 풀 2 / overflow 0을 적용하고 기존 설정의 기본 동작은 유지한다.
공유 DB이므로 개통 후 Checking에서 수정한 콘텐츠·회원도 메인에 영향을 준다.

## 현재 공개 집합만 전환

현재 공개 3,530건 = Checking 미리보기 3,526건 + 정식 공개 4건(4044~4047).
전체 DB의 active preview 수는 4,029건이므로 `status=active`나 `preview=true`만으로
대상을 고르면 만료 등으로 이미 숨겨진 글까지 포함한다. 대상은 실제 공개 predicate로
얻은 고정 ID 집합과 당시 Content/Draft/출처/슬라이드 메타데이터의 fingerprint로 묶는다.

전환은 기존 3,526건의 `active / needs_review / checking`을
`active / confirmed / published`로 맞추고, Content와 Draft의 최상위 preview 키들을 제거한다.
이미 승인된 4건은 변경하지 않는다. 제목·본문·이미지·출처·작품 연결·게시 시각·만료 시각은
바꾸지 않는다. 비공개 517건과 manifest 생성 후 추가된 검수 대기 글도 대상에 넣지 않는다.

이는 **운영자의 기존 공개 범위 유지 결정**이며 새 LLM 검수나 개별 품질 검수의 수행을
뜻하지 않는다. 기존 검수 evidence/operator review를 보존하고 전환 전 Content/Draft 상태,
manifest hash, 행 fingerprint, 운영 결정, `llm_quality_review_performed=false`를 기존 JSON의
`main_publication_transitions` 이력에 추가한다. 현재 DB에는 admin 감사 테이블이 없어
임의의 관리자 계정을 만들거나 새 서비스 DB 스키마를 요구하지 않는다.

기존 공개 predicate와 추천 계산을 완화하지 않는다. 새 콘텐츠는 기존 승인 경로를 거쳐야
노출된다. 미래 게시·만료·숨김·삭제 조건도 계속 적용되므로 3,530이라는 수는 전환 시점의
동일성을 검증하는 기준이며, 만료 이후까지 콘텐츠를 강제로 남기는 정책은 아니다.

전환 모듈은 고정 계획을 검증하고 격리 DB에만 적용할 수 있다. 실제 서비스에 쓰는
apply 명령은 이번 준비에 포함하지 않는다. 실행 시점에 원본이 바뀌었거나 만료되면
대상을 임의 확대하거나 덮어쓰지 않고 전체 전환을 중단하도록 검증한다.

## 일일 생성 정책

하루 20건은 **검수 통과 콘텐츠 목표**다. 실패·생략을 포함하는 시도 상한과 비용 예산은
아직 정해지지 않았다. 이전 총 $5 한정 실험을 새 일일 예산으로 재사용하지 않는다.
이번 전환 3,526건도 새 LLM 생성/검수 통과 실적으로 계산하지 않는다. 기존 비용·작업
이력은 보존하며 Worker/Beat/유료 생성 활성화는 별도 결정과 검증이 필요하다.

## 검증 방법과 제한

서비스 DB는 읽기 전용 트랜잭션으로만 조회한다. 실제 콘텐츠/초안 metadata를 파일로
추출하려던 작업은 자동 승인 검토에서 데이터 반출 범위 미승인으로 거부되어 실행하지 않았다.
대신 서버 안에서 메모리로 계획을 계산하고 개수·hash만 반환하며, 격리 DB 검증에는
같은 규모와 상태를 가진 합성 데이터를 사용한다. 실제 회원 개인정보와 PNG 원본은 옮기지 않는다.

로컬 검증 중 기존 root 의존성 lockfile이 달라 재사용하지 않고 분리 worktree에서 `npm ci`했다.
기본 sandbox의 Next 빌드가 진행되지 않아 해당 테스트 프로세스를 종료하고 허용된 별도 빌드를
실행했다. 읽기 집계 스크립트의 PostgreSQL JSON 줄바꿈 처리는 JSONB 출력으로 바로잡았다.
모두 준비/검증 도구 문제였으며 서비스 파일·DB·추천·Agent 실행 설정은 변경되지 않았다.

SQLite 테스트 fixture의 JSON을 SQL 문자열에 직접 넣었을 때 생긴 바인딩 오류는
명시적 파라미터 바인딩으로 수정했다. 독립 리뷰에서 확인한 테스트 DB URL query override
우회도 차단하고 회귀 테스트를 추가했다.

## 검증 결과

- 서버 내부 최종 읽기 전용 dry-run: KST 2026-11-17 09:44:31, 대상 3,530건,
  전환 가능 preview 3,526 / 기존 승인 4. 원문 파일 저장·반출 없음, 서비스 DB 쓰기 0.
- 공개 ID 집합 SHA-256:
  `59d795b06e5cbf43b718c820bd70cfbfcfb69e50161c96eeb482af486396dded`.
- 최종 in-memory manifest SHA-256:
  `1447100eba9050097cc75ba0e02c414070b07207150486d7fd83a6f817c2da38`.
  실제 운영용 manifest 파일은 만들지 않았다. 실행 준비 시 같은 대상과 원본 버전을
  다시 확인해야 한다. 오늘 dry-run 이후 변경·만료된 글은 자동으로 대상에 추가하지 않는다.
- 두 읽기 검증 사이 실제 회원 8 / 추천 라운드 70 / 추천 항목 7,000 / 작품 연결 8,057의
  row fingerprint가 같았다. 콘텐츠 상태 전환은 서비스 DB에서 실행하지 않았다.
- 합성 PostgreSQL 리허설: 4,047건에 새 pending 1건 추가. 3,526건만 정상 공개 전환,
  기존 승인 4건 무변경. 메인 공개 결과 4 → 3,530이며 Checking의 기존 공개 집합과
  추천 조회 집합이 정확히 같았다. 제외 517건과 신규 pending 1건은 계속 비공개였다.
- 합성 DB의 회원·세션·추천 6,300항목·Agent 실행·검수 로그·출처·슬라이드 및 기존
  검수 근거 hash 보존. 원본 편집 충돌 차단, 중간 CAS 실패 후 전체 rollback,
  두 번째 실행에서 추가 쓰기 0을 확인했다. 실제 PNG 원문을 반출/재검증한 테스트는 아니다.
- 전환 전용 회귀 34건, 기존 추천/공개/세션/로그인·풀 설정 205건 + 11 subtests 통과.
  전환 후 API의 `DEV_PREVIEW`/`REVIEW_REQUIRED` 안내가 사라지고 `AI_GENERATED` 안내는
  유지되는 것도 확인했다. 과거 preview 값은 감사 이력 안에만 보존된다.
- 메인 설정 검증 10건 통과. FE 646건 중 645 통과, 기존 skip 1, 실패 0.
  메인 API 주소를 주입한 production build 및 typecheck 통과.
- 자동 수집·유료 생성·LLM 호출·서비스 DB 쓰기·DNS 변경·배포 승격은 수행하지 않았다.
- 작업 전후 기존 컨테이너의 이미지·상태·안전한 환경 설정 메타데이터가 같았다.
  Checking API healthy, 생성 Worker created(미실행)를 확인했다. 비교 시 JSON 키 순서에
  따른 문자열 차이는 객체 비교로 바로잡았고 실제 설정 차이는 없었다.
  이번 합성 검증용 PostgreSQL 컨테이너와 내부 네트워크는 정리했다.

재현 도구:

- `backend/app/services/main_publication_transition.py`: 고정 계획/격리 적용.
- `backend/scripts/prepare_main_publication.py`: 승인된 로컬 자료·합성 fixture에서 계획 생성만.
  실제 DB를 조회하거나 적용하는 CLI가 아니며, 운영 원문 반출 승인을 대신하지 않는다.
- `backend/tests/test_main_publication_transition.py`: 네트워크 없는 SQLite 회귀.
- `backend/tests/main_publication_postgres_rehearsal.py`: 합성 PostgreSQL 대량 리허설.
  별도의 내부 네트워크에 `main-publication-test-db` / `test_main_publication_20261117`을
  만들고 테스트 전용 `MAIN_PUBLICATION_TEST_DATABASE_URL`을 제공해야 한다. 운영 URL은
  거부한다. 임의 schema에서 합성 자료만 생성하고 검증 후 해당 schema를 제거한다.

다음 운영 전환은 검증된 백업과 당시 대상/상태 비교, 기존 운영자 승인 이력의 보존,
실제 쓰기 경로 검토를 마친 후 별도로 진행한다. 그다음 메인 API/OAuth·Vercel 최신
빌드·DNS 연결을 순서대로 확인한다. 생성 예산 결정은 이 데이터 전환과 별도다.
