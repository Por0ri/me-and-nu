# Checking 베타 배포·한정 자동 생성 완료 기록

작성 기준: 2026-11-16. `runtime-resume-status-5.json`, `finished-runtime.json`, `final-service-verification.log`, `runtime-audit-final.json`과 4건의 서비스 apply 완료 로그까지 반영했다. 실제 배포·생성 기준은 **d78de66**이며, 실행 도중 원격 Checking에 추가된 **PR #77(f45b858)은 이번 이미지에 반영·검증하지 않았다.**

## 확인된 완료 범위

Checking API 배포, 기존 원피스 생성물의 승인·공개, 오늘 한정 자동 수집·유료 생성 4슬롯 실행이 완료됐다. 새 작업은 **검수 통과 3건, 유형 불일치 생략 1건**으로 종료했다. 기존 생성 1건을 포함한 승인된 총 5슬롯을 소진했다.

최종 읽기 전용 검증으로 확인한 서비스 공개 콘텐츠는 **4044·4045·4046·4047, 총 4편·12장**이다. 각 요청의 공개 건수는 1건이며 상태는 `active / confirmed / published`다. 재적재 dry-run은 모두 `unchanged`이고 12장 PNG 바이트가 일치한다. 미리보기 우회로 공개하지 않았다.

| 항목 | 확인 결과 |
|---|---|
| Checking 배포 커밋 | `d78de66eb3367978d7f45a2398323e8674c44e36` |
| API 이미지 | `me-nu-backend-checking:d78de66eb3367978d7f45a2398323e8674c44e36` |
| 서비스 DB revision | 기존 `d7b3e9a1c452` 유지 |
| 작업 DB | 독립 `menu_content_jobs`, v7, 신규 작업·생성 시작 각 4건 |
| 자동 실행 종료 | 14:51:23 KST, Controller exit 0, `new_job_slots_consumed` |
| 유료 생성 Worker | 14:52:55 KST 정상 정지, exit 0 |
| 다음 날 예약 | **없음**. 오늘 한정 실행이며 무제한 Beat 자동 생성을 켜지 않았다. |
| API 상태 | `/health`, `/health/db`, `/health/redis` loopback HTTP 200·정상, 외부 HTTPS 정상 응답 확인 |
| 기존 서비스 | API 교체 외 컨테이너 9개 ID·시작 상태·이미지 유지 |
| 추천 | 기존 추천 컨테이너 유지, 서비스 적재 전후 63 rounds / 6,300 items 동일 |

Controller 종료 스냅샷에서는 Worker가 아직 실행 중이지만, 더 늦은 `finished-runtime.json`에서 Worker 정상 종료를 확인했다. 최종 `runtime-audit-final.json`도 `passed=true`, `failures=[]`이며 Controller·유료 Worker 정지와 DB·Redis 보존을 확인했다. 자동 실행 중 `runtime-audit-live.json`의 `running` 값과 최종 종료 상태를 구분한다.

최종 외부 HTTPS 점검에서도 API health 3개와 콘텐츠 4044~4047 페이지가 모두 HTTP 200으로 응답했다. 콘텐츠 페이지의 200은 페이지 shell 응답 확인이며 실제 사용자 로그인·가입부터 피드까지 전체 동선 검증을 뜻하지 않는다. 콘텐츠 공개 확인은 기존 공개 조건을 이용한 커밋 후 조회와 별도 읽기 전용 상세·이미지 검증이다. 근거: `public-health-final.json`.

## 소스 반영·운영 실행·서비스 데이터 변경

### 저장소 소스

- Checking에 원본 반려 이력을 보존하는 독립 검수 등록·내보내기 기능을 반영했다.
- 작업 DB v7은 `content_generation_review`와 선택된 `review_id`를 추가한다. 원본 job/attempt/result/manifest/Agent SQLite/PNG를 덮어쓰지 않는다.
- 같은 기사에서 선택되는 생성 결과는 1건이며, 이미 선택한 결과·검수 ID를 다른 것으로 바꾸지 않는다.
- 기존 적재기의 검수 대기·운영자 승인 규칙과 추천 코드·모델은 유지했다. 이번 자동 실행을 위해 Agent 구조를 바꾸지 않았다.
- 이전 무과금 검증 문서의 “서비스 DB 쓰기 없음·미배포”는 그 당시 범위다. 이후 사용자가 승인한 이번 실제 배포·공개와 구분한다.

### 원격 Checking의 동시 변경과 실제 배포 버전

배포 진행 중인 약 14:46 KST에 원격 Checking으로 **PR #77, f45b858**이 추가 머지됐다. 배포 담당의 최신 원격 비교 결과 d78de66은 그 조상이며, 추가 변경은 Agent 44개 파일, 5,164줄 추가·589줄 삭제다. Backend·추천·Worker 코드는 이 추가 변경에 포함되지 않았다.

이번 서비스 API 이미지와 생성 Worker 이미지는 **d78de66 기준을 유지**한다. API 이미지의 빌드 context도 `./backend`로 확인했으며 PR #77 Agent 변경이 해당 API 이미지로 유입된 경로는 없다. 이 문서의 실제 생성·검수·공개·무과금 검증 결과를 PR #77의 새 Agent에 대한 검증으로 해석하면 안 된다. 문서 반영용 로컬 작업 체크아웃은 최신 원격 f45b858로 fast-forward했다. 이번 후속 변경은 이 문서 한 파일이며, Git 동기화 자체가 서버 이미지 재배포를 뜻하지 않는다. PR #77 Agent 이미지 반영·검증은 별도 후속 작업이다.

### 이번 서버 운영 도구

`/home/ubuntu/me-nu-beta-20261116`의 독립 프로젝트 `menu-beta-generation-20261116`을 사용했다. 한시 Controller·운영자 적재·공개 검증 도구는 이번 실행용 harness이며, Checking API에 상시 자동 발행 기능을 추가한 것이 아니다.

- 전용 작업 DB·Redis·단일 생성 Worker. 호스트 포트 없음.
- 작업 네트워크는 서비스 DB·세션 Redis·추천 네트워크와 분리.
- API 키는 generation Worker에만 읽기 전용 연결. Controller와 importer에는 키를 제공하지 않았다. 키 값은 로그·이 문서에 기록하지 않는다.
- 최종 키 파일 메타데이터는 mode 0600, owner/group 10001이다. 값은 출력하지 않았다.
- Controller가 실제 기사 수집 → 입력 검증 → 정확한 job 1건 발행 → 완료·비용 확인 → 통과 번들 내보내기를 순차 수행했다.
- 서비스 적재·운영자 승인은 별도 절차로 수행했다. Controller 자체의 `service_db_writes=0`은 전체 배포에서 서비스 쓰기가 없었다는 뜻이 아니다.

### 서비스 데이터와 배포용 복사본

- 서비스에는 승인된 번들과 정확한 digest에 연결된 운영자 검수 기록을 적재했다.
- 승인 기록의 `reviewedBy`는 `Codex-assisted beta release`다. 사용자가 승인한 베타 공개 범위에서 Codex가 원문 근거·본문·이미지 검토와 공개 적용을 수행했다.
- 기존 원피스 83393은 검수 대기로 먼저 적재해 공개 0건을 확인한 후 승인했다.
- 신규 83418·83385·83382는 dry-run 후 명시적 운영자 승인 기록과 함께 적재했다.
- 83418은 공개 만료일만 단축한 배포용 복사본을 사용했다. 원본 봉인·본문·PNG는 그대로다.
- 83382는 근거 없는 크기·금색 암시를 두 텍스트 필드에서 제거한 배포용 복사본을 사용했다. 원본 봉인·PNG를 보존하고 보정 내역을 별도 운영자 기록으로 남겼다.
- 추천 계산을 다시 실행하거나 기존 추천 round/item을 수정하지 않았다. 기존 콘텐츠의 작품 링크도 유지했다.
- 신규 콘텐츠의 공개 자격과 개인별 추천 목록 갱신은 별개다. 개인화 추천 반영은 기존 추천 배치 일정과 선정 결과를 따른다.

## 기사별 생성·공개 결과

| 기사 | 생성/검수 결과 | 서비스 반영 | 카드 | 기록된 추정비용 |
|---|---|---|---:|---:|
| 83393, 기존 생성물 | 원본 반려 보존 + 별도 독립 검수 승인 | **4044 공개 완료** | 4 | baseline에 포함 |
| 83418 | `ready_for_staging`, 독립 검수 78점, blocker 없음 | **4045 공개 완료** | 3 | $0.00772830 |
| 83388 | goods 요청이 event로 판정돼 `content_type_mismatch_skipped` | 공개하지 않음 | 0 | 미계측, $1 예약 |
| 83385 | `ready_for_staging`, 독립 검수 72점, blocker 없음 | **4046 공개 완료** | 3 | $0.00933559 |
| 83382 | `ready_for_staging`, 독립 검수 75점, blocker 없음; 운영자 사실 범위 보정 | **4047 공개 완료** | 2 | $0.00381376 |

낮은 해상도 등 비차단 검수 주의 사항은 지우지 않았다. LLM 검수 통과만으로 운영자 공개 승인을 대신하지 않는다.

### 83393 → Content 4044

- 원문: `https://one-piece.com/news/83393/index.html`
- request: `crawl-53ef7bddae4103`
- 원본 job: `039505e4-4e31-456f-b0cf-399acb9c5364`
- bundle digest: `a6cbd2d8c34de6cb1f77d6d64258cfc975ab483759ba61e4d64090150d02862f`

`pending_review / needs_review / checking`, 공개 0건에서 시작했다. 운영자 승인 후 `active / confirmed / published`, 공개 1건을 확인했다. 별도 읽기 전용 검증에서 4장 PNG 바이트, 원본 검수 증거, `preview=false`를 확인했다. 이 적재·승인에는 추가 LLM 호출이 없다.

근거: `original-service-pending.log`, `original-service-approved-final.log`, `original-service-independent-verify.log`.

### 83418 → Content 4045

- 원문: `https://one-piece.com/news/83418/index.html`
- job: `74821959-f5bc-474a-85b7-f72e93dc28d9`
- request: `crawl-e5d186616a4626`
- 공개용 digest: `dd24be876dd939f3a9729d9fe5f58c8d4546af2d8ccdecd3d8fa35e50a3a22d4`

원본의 `feed_valid_until`은 2026-12-16이었으나, 검증된 fact 06과 본문은 예약 마감을 **2026-11-23 23:59 KST**로 명시했다. 배포용 복사본의 공개 만료를 해당 예약 마감으로 단축하고 `reviewEvidence.operator_correction`에 변경 전후·근거 fact·원본 bundle SHA·추가 LLM 호출 0·본문/이미지 무변경을 기록했다.

서비스 dry-run 후 승인 기록과 함께 apply했다. 커밋 후 공개 1건, 별도 읽기 전용 검증에서 Content 4045·3장 PNG 바이트·`preview=false`를 확인했다. 기존 원본 bundle과 PNG는 변경하지 않았다.

근거: `83418-release-validation.json`, `83418-service-dry-run-final.log`, `83418-service-approved.log`, `83418-service-independent-verify.log`, `harness/prepare_release_copy.py`.

### 83385 → Content 4046

- 원문: `https://one-piece.com/news/83385/index.html`
- job: `eca5a5db-a8f0-4a4a-bfa8-f20855c80c35`
- request: `crawl-a72307d2b48046`
- digest: `abe5cdb53476b77e94a0f21119558ad02447b4a1749262a3fa4f3c44ad954e45`

dry-run의 `created=1`은 저장 예정값이며 당시 공개 0건이었다. 후속 apply 로그에서 **Content 4046, `active / confirmed / published`, 이미지 3장, `committed=true`, 커밋 후 공개 1건**을 확인했다. 추천 63/6,300과 기존 콘텐츠 링크는 전후 동일하다. 최종 별도 읽기 전용 검증에서도 공개 1건·재적재 dry-run `unchanged`·3장 PNG 바이트 일치를 확인했다.

근거: `83385-service-dry-run.log`, `83385-service-approved.log`, `final-service-verification.log`.

### 83382 → Content 4047

- 원문: `https://one-piece.com/news/83382/index.html`
- job: `b0666dc9-5866-4a6e-a163-fc31a005c166`
- request: `crawl-1f26a29c639226`
- 생성된 카드 2장, LLM 검수 75점, blocker 없음.
- 원본 피드 만료: `2026-11-18T15:00:00+00:00`.
- 공개용 digest: `ab42c10a12afe81adc963bc346e93198be40d32494310a2d609ba239bc1ec959`.

독립 LLM 검수는 사실 지원 문제에 3점을 주고도 종합 75점·승인으로 통과시켰다. 따라서 LLM 승인만으로 공개하지 않고 운영자가 원문 근거와 표현을 다시 확인했다. 검증된 fact 01의 출처는 메탈릭 컬러만 확정하며 크기나 금색은 확정하지 않는다.

배포용 복사본의 `package.cards[0].body_sentences[0]`에서 `금빛(?)`을 `메탈릭`으로 바꾸고, `package.detail_body`의 `손바닥만 한 존재감에 금빛(?) 상상까지 얹히니,`를 `메탈릭의 반짝임을 상상하니,`로 바꿨다. `reviewEvidence.operator_correction`에 두 필드의 변경 전후·근거 fact·원본 bundle SHA·운영자 검토 시각을 기록했다. 보정 필드와 감사 기록을 되돌리면 원본 문서와 일치하는지 검사했고, 원본 bundle과 모든 PNG 바이트는 보존했다.

추가 LLM 호출·새 LLM 검수 없이 운영자 보정으로 처리했다. 이 보정본에 별도 독립 LLM 승인이 새로 생겼다는 뜻은 아니다. dry-run을 통과하고 명시적 운영자 승인으로 apply해 **Content 4047, `active / confirmed / published`, 공개 1건**을 확인했다. 최종 읽기 전용 검증에서 재적재 dry-run `unchanged`·2장 PNG 바이트 일치를 확인했다.

근거: `harness/prepare_83382_release_copy.py`, `83382-service-dry-run.log`, `83382-service-approved.log`, `final-service-verification.log`.

## 총량·비용·종료 상태

사용자 승인 범위는 누적 **$5 목표**, 기존 1건 포함 **최대 5건**이다. 새 생성 4슬롯을 전부 사용했고 추가 생성은 끝냈다. 생략·반려·실패도 생성 시도 슬롯에 포함하는 방식이며, 원하는 공개 콘텐츠가 5건이 될 때까지 계속 생성하는 설정이 아니다.

| 비용 구분 | 금액 |
|---|---:|
| 기존 생성·검수·사전 확인 baseline | $0.01178341 |
| 신규 83418 기록된 추정비용 | $0.00772830 |
| 신규 83385 기록된 추정비용 | $0.00933559 |
| 신규 83382 기록된 추정비용 | $0.00381376 |
| **사용량 기록으로 계산 가능한 추정비용 합계** | **$0.03266106** |
| **83388 미계측 비용 예약액** | **$1.00** |
| **예산에서 차감한 합계** | **$1.03266106** |

`$1.00`은 실제 청구액이나 측정된 비용이 아니다. 83388의 `measured_cost`와 `estimated_cost_usd`는 `null`로 남기고 `accounting_method=reserve`로 구분했다. 공급자 청구서의 확정 합계를 검증한 기록도 아니다. $5는 운영 목표 예산이며 공급자 결제 시스템의 hard cap이 아니다.

83388은 사실 추출 LLM 호출을 거친 뒤 `write_bound_skip`에서 사용량이 누락됐다. 비용 0으로 가정하지 않고 Controller가 `missing_cost_evidence`로 중단했다. 다음 식별자에만 묶인 별도 예약 기록을 검증한 뒤 재개했다.

- job: `83eaaf72-4402-4458-a35f-a28935d0fb03`
- attempt: `0e3f7207-cdda-4089-ac4c-3e9a7bcdb1ef`
- result SHA: `33cd7b72a2ee681b0588e95a86ace8dd6ef912827c0d748cf61aa609bb1c8450`
- producer status: `content_type_mismatch_skipped`

재개 시 원본 봉인을 다시 검증했고, DB/file lock 아래 예약·감사 이력·재개 상태를 함께 저장했다. 같은 job을 다시 발행하거나 생성하지 않았다. 남은 2슬롯만 실행했다. 다른 작업의 사용량 누락에는 이 예약을 재사용할 수 없다.

최종 Controller는 exit 0, 생성 Worker도 exit 0으로 정상 종료했다. 작업 DB·Redis·산출물·checkpoint는 보존했고 **내일 자동 생성 예약은 없다**.

처음 Controller 컨테이너의 exit 2는 위 비용 증거 누락으로 의도적으로 멈춘 이력이며, 예약 검증 후 재개한 `resume1` 컨테이너는 exit 0이다. 정지한 생성 Worker의 과거 health 필드가 `unhealthy`로 남아 있지만 `state=exited`, exit 0과 함께 확인해야 하며 실행 중 실패로 해석하지 않는다. 서비스 API는 `running / healthy`, 추천 컨테이너도 계속 실행 중이다. 근거: `final-key-metadata-and-status.log`.

작업 DB 완료 백업:

- `/home/ubuntu/me-nu-beta-20261116/backups/completed-task-database.dump`
- 27,234 bytes
- SHA-256: `c22800586f6b7fc5132d7ab499f61df9df09913c4f3859e89d73a0aba7b78ec3`

근거: `runtime-resume-status-5.json`, `finished-runtime.json`, Docker 종료 시각 `finished-timestamps.log`.

## 검증과 기존 서비스 보존

- API 이미지의 UID/GID 10001로 소스 179개 해시·읽기, Python 174개 compile, Pillow 12.3.0, 앱 import·OpenAPI·health·Alembic revision을 검증했다.
- 한시 Controller 초기 단위 테스트 18건, 비용 예약·재개 보강 후 **25건 통과**.
- Controller의 재시작·발행 중단·중복·슬롯 제한·기한·비용 누락·잘못된 receipt·원본 SHA/attempt/status 불일치·예약과 실측 구분을 검증했다.
- 실행 중·종료 후 읽기 전용 감사에서 작업 DB v7·4건, 호스트 포트 없음, generation Worker만 키 마운트, 기존 컨테이너 9개 불변, API 건강 상태를 확인했다. 최종 감사 `passed=true`, 유료 Worker 정지 상태다.
- 공개 apply 로그에서 추천 round/item과 기존 콘텐츠 링크를 대조했다. 최종 읽기 전용 검증에서 Content 4044~4047 각 공개 1건, 각 번들의 재적재 dry-run `unchanged`, PNG 12장 바이트 일치, 원본 producer evidence 보존·`preview=false`, 추천 63/6,300을 확인했다. 이 마지막 검증 자체는 서비스 DB 쓰기·LLM 호출 0회다.

API 배포 전 백업 `/home/ubuntu/me-nu-checking-update-20261116-d78de66`은 **schema와 기존 환경 파일 범위이며 전체 서비스 데이터 dump가 아니다**. 기존 서비스 데이터와 볼륨은 유지했다. 위 완료 작업 DB dump와 백업 범위를 혼동하지 않는다.

## 실행 중 작은 오류와 수정 기록

1. **비root 이미지 파일 권한:** 제한적인 umask 때문에 Worker 소스가 UID 10001에서 읽히지 않았다. 저장소 추적 소스만 일반 파일 0644·디렉터리 0755로 바로잡고 이미지를 재빌드했다. 비밀 파일은 범위에서 제외하고 제한 권한을 유지했다. 이후 무과금 preflight·건강 상태 통과.
2. **Controller 모듈 경로:** 한시 컨테이너의 Python 모듈 경로 누락으로 `ModuleNotFoundError`가 발생했다. 실행 경로를 보완하고 실제 생성 전에 plan/preflight를 재검증했다.
3. **읽기 전용 컨테이너의 임시 공간:** 비용 예약 테스트 실행에 `/tmp` tmpfs가 빠져 `No usable temporary directory`가 발생했다. 쓰기 가능한 제한된 임시 공간을 지정한 재실행에서 25건 전부 통과했다. Agent 실행 결과 오류가 아니다.
4. **번들 전달·권한:** 읽기 전용 API rootfs에 직접 복사하려던 방식과 root 소유 공개 산출물의 chmod 권한 문제를 바로잡았다. 명시적 bundle mount·공개 산출물 권한으로 전달했고 서비스 dry-run을 다시 통과했다. 비밀값을 공개 산출물과 함께 처리하지 않았다.
5. **커밋 직후 공개 검증 시각:** PostgreSQL `now()`는 트랜잭션 시작 시각으로 고정돼, 같은 트랜잭션에서 더 늦게 정한 `published_at`을 즉시 공개로 보지 못했다. 운영 검증 harness의 공개 확인을 **커밋 후 새 읽기 전용 트랜잭션**에서 수행하도록 수정했다. 기존 피드·추천 공개 조건은 바꾸지 않았다.
6. **유료 추출 후 사용량 누락:** 83388은 자동 중단 후 원본 해시에 묶인 별도 $1 예약으로 재개했다. 원본 result·manifest·Agent DB를 고치거나 비용 0으로 기록하지 않았다.
7. **83418 예약 마감과 피드 만료 불일치:** 검증된 원문 사실에 맞게 배포 복사본의 공개 만료만 단축했다. 원본 봉인·본문·PNG는 그대로 두고 운영자 보정 근거를 남겼다.
8. **83382 근거 없는 크기·금색 암시:** 원문이 확정하는 메탈릭 표현으로 두 텍스트 필드만 보정했다. 원본 bundle·PNG를 보존하고 운영자 변경 이력을 남겼다. 추가 LLM 호출 없이 dry-run·승인 적재·최종 중복/공개 검증을 통과했다.

근거: `runtime-preflight.log`, `runtime-preflight-path-fixed.log`, `runtime-preflight-final.log`, `runtime-permissions-build.log`, `controller-reservation-tests.log`, `controller-reservation-tests-fixed.log`, 각 service dry-run/apply 로그.

## Agent 담당 후속 요청

1. **모든 terminal 결과의 사용량 보존:** 83388처럼 fact extraction 후 유형 불일치로 종료할 때 `extraction_usage`와 이전 누적 usage를 result에 반드시 남겨야 한다. 실제 0회, 미계측, 양수 사용량을 구분한다. 이번 외부 예약은 누락 수정의 대체물이 아니다.
2. **예약 종료와 노출 종료 연결:** 명시적 preorder end가 기본 feed TTL보다 이르면 공개 만료를 앞당긴다. 보관·삭제 기한과 피드 노출 기한을 분리한다. 83418 사례로 무과금 회귀를 추가한다.
3. **이미지 선정과 주장 일치:** 공식 출처·해시·규격·낮은 해상도 확인 외에도 카드의 상품·변형·색상 주장과 선택한 이미지가 실제로 맞는지 검수한다. 83382는 근거 없는 크기·금색 암시를 운영자가 두 텍스트 필드에서 제거한 후 공개했다. 원문과 관계없는 이미지를 같은 작품이라는 이유만으로 승인하지 않도록 사례 기반 회귀가 필요하다.
4. **독립 검수 승인 기준:** 83382는 사실 지원 문제를 3점으로 지적하면서도 종합 75점·승인이 됐다. 사실 근거와 이미지 의미 검토에서 낮은 점수나 미해결 사항이 있으면 운영자 검토가 필요하다는 점을 명시하고, blocker·승인 임계값이 중요한 사실 문제를 놓치지 않는지 점검한다. 오늘의 4건 공개는 최종 운영자 판단을 거쳤다.
5. **이력 보존:** 반려·생략·검수·배포 보정 이력을 덮어쓰지 않는다. 재검수나 보정은 원본 SHA를 가리키는 별도 기록으로 남긴다.

이 후속 항목은 이번에 실제 실행한 d78de66 Agent에서 관찰한 내용이다. PR #77이 각 항목을 이미 고쳤는지는 아직 비교·검증하지 않았으므로 담당자와 최신 Agent에서 다시 확인한다.

무과금 확인 항목: 추출 stub 뒤 type mismatch에도 누적 사용량 유지, 생성 전 실제 0회와 미계측 구분, preorder end 우선 만료, 카드별 상품/색상과 이미지 일치, 원본 해시·본문·PNG 불변, 보정본 중복 적재 1건 유지.

## 최종 확인과 후속 범위

- [x] Checking API 배포·건강 상태 확인
- [x] 기존 83393 공개 4044, 신규 83418 공개 4045
- [x] 신규 83385 apply·공개 4046 확인
- [x] 신규 4슬롯 생성 종료, 비용/예약 분리, Controller·유료 Worker 정상 종료
- [x] 작업 DB 완료 백업과 이력 보존, 다음 날 생성 예약 없음
- [x] 83385 별도 읽기 전용 검증 완료
- [x] 83382 운영자 보정·승인·공개 4047 및 원본/PNG 보존 확인
- [x] 최종 공개 4건·12장, 중복 dry-run, 추천 보존 및 종료 후 런타임 감사 완료
- [ ] PR #77의 새 Agent 이미지 검증·반영은 별도 진행
- [ ] 상시·다음 날 자동 생성은 별도 범위와 한도 승인 후 설정. 현재 예약 없음.

로컬 실행 근거는 `out/beta-open-2026-11-16/`에, 이번 서버 운영 harness와 작업 이력은 `/home/ubuntu/me-nu-beta-20261116/`에 보존했다. 한시 harness와 비밀 파일은 이 문서 커밋에 포함하지 않는다.
