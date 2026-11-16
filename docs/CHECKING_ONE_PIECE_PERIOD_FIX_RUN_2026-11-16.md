# Checking 원피스 공식 굿즈 출시 기간 수정 후 단건 생성 — 2026-11-16

현재 상태: Checking 반영 후 격리 Celery Worker에서 기사 한 건을 1회 실행해 **카드 4장과 상세글을 실제 생성했다.** 다만 예약 시작일을 기사 게시일로 오인한 코드 검수에 걸려 원본 결과는 `rejected`다. **게시 가능한 검수 통과 결과는 아니다.** 사용량 기록 7건의 추정비용은 **$0.00859036**으로 $1 목표 미만이다. 날짜 검수 오탐을 좁게 수정한 뒤 관련 회귀 **237건**과 실제 산출물의 코드 재검사를 통과했다. 원본 결과·작업 상태는 바꾸지 않았고 추가 유료 재생성도 하지 않았다. 테스트 컨테이너는 모두 정지했고 기존 서비스 12개는 유지됐다.

## 승인 범위

- 원피스 공식 굿즈 기사 [83393](https://one-piece.com/news/83393/index.html) 한 건을 격리 환경에서 실제 생성한다.
- 사용자가 재사용을 승인한 기존 API 키를 사용한다. 키 값은 이 문서나 실행 로그에 기록하지 않는다.
- 비용은 **$1 목표 예산**이다. 제공자 측 강제 차단 한도를 설정한 것은 아니며, 최종 사용량에 따른 추정비용과 제공자 청구액은 구분한다.
- 자동 수집·Beat·상시 Dispatcher와 서비스 DB 적재는 실행하지 않는다. 기존 추천 코드·설정·데이터를 유지한다.
- 작은 실행·점검 오류는 수정하고 이 문서에 남긴다. 시스템이나 Agent 구조 변경이 필요한 결함은 별도로 보고한다.

## Checking 반영과 사전 검증

| 항목 | 확인 결과 |
| --- | --- |
| 기준 Checking | `a7f85c9e3210cba52831529d52d92bc6e3703824` |
| 수정 커밋 | `2ca31f3030de5cc702179a9e6534e7daea207de2` — `fix(agent): preserve approximate goods release periods` |
| 원격 반영 | `origin/Checking`에 fast-forward push 후 원격 SHA 일치 확인 |
| 관련 회귀 | 격리 환경에서 **213 passed in 23.60s** |
| 실행 후 검수 보완 회귀 | **237 passed in 22.32s**, API 키·네트워크 없는 격리 환경 |
| 새 이미지 무과금 점검 | CLI·작품 catalog·폰트·PNG·오프라인 생성 그래프 통과 |
| 실행 준비 점검 | 키 파일 형식·읽기 및 산출물 디렉터리 쓰기 preflight 통과; 키 값 미출력 |
| 변경 범위 | Agent 날짜 처리·원문 보존·생성 프롬프트, 관련 테스트·문서 |
| 유지한 범위 | Celery 중복 방지·작업 DB 스키마·서비스 적재·추천 코드·자동 수집 기본값 |

원문의 `2027年2月中旬頃発売予定`를 임의의 정확한 하루로 바꾸지 않고, 월·상순·중순·하순 정밀도와 예정 표현을 보존하도록 수정했다. 기간 전체가 미래일 때만 출시 예정 근거로 인정하고 품절·종료·연기·상충 근거에 대한 보류 및 기존 검수 차단은 유지한다.

자세한 수정·기존 테스트 보정 근거는 [출시 기간 수정 기록](AGENT_GOODS_RELEASE_PERIOD_FIX_2026-11-16.md)에 남겼다. 이번 Checking 반영은 Git 코드 반영이며 별도 서버 배포를 실행한 기록은 아니다.

## 격리 실행 구성

| 항목 | 값 |
| --- | --- |
| 실행 프로젝트 | `menu-paid-onepiece-period-20261116` |
| 로컬 산출물 디렉터리 | `out/celery-paid-onepiece-period-2026-11-16` |
| 출처·작품·유형 | `one_piece_official` · 작품 ID `8` · `goods` |
| 생성 이미지 태그 | `me-nu-anime-generation:checking-2ca31f3` |
| 최종 이미지 digest | `sha256:7c4fcd81d954ed8db249ed8854e91f34cef8219f68c1e4d20dd5459818e8b0fa` |
| 작업 저장소 | 신규 PostgreSQL 작업 DB·Redis·산출물 volume |
| 서비스 연결 | 서비스 DB 연결 정보를 전달하지 않음 |
| 네트워크 | 작업 DB·Redis는 내부 네트워크, 생성 Worker만 외부 요청 가능 |
| 키 전달 | 생성 Worker에만 전용 키 파일 읽기 전용 mount |
| 작업 수 | 한 건 등록, Dispatcher 1건 발행·실패 0건 |

이전 격리 실행의 보류 작업 `1b6e0832-1630-454d-9372-0316b9728725`와 생성 시도 이력은 보존한다. 이전 작업의 시작 표시를 지우거나 중복 방지 규칙을 우회하지 않는다. 이번 실행은 사용자가 새로 승인한 수정 후 재검증이며 별도 DB와 volume을 사용한다.

## 실제 생성 결과

**카드·상세글 생성 완료, 코드 검수에서 반려.**

| 항목 | 현재 확인값 |
| --- | --- |
| 작업 ID | `039505e4-4e31-456f-b0cf-399acb9c5364` |
| 생성 시도 ID | `1909c466-246c-422b-bf50-a53eb1d0c163` |
| 작업·시도 수 | 작업 1건 · 시도 1회 |
| 작업 실행 상태 | `execution_status=succeeded` |
| 작업 결과 | `outcome=rejected` |
| Agent 최종 상태 | `producer_status=rejected_archived` |
| 검수·판정 | `review_mode=code` · `editorial_decision=rejected` |
| 실제 생성 시작 | `2026-11-16T03:44:48Z` / 한국 시각 12:44:48 |
| 실제 생성 종료 | `2026-11-16T03:47:48Z` / 한국 시각 12:47:48 |
| 생성 산출물 | 카드 4장 · 상세글 |
| 검수 blocker | `SOURCE_PUBLICATION_DATE_SHOWN`, 카드 page 2 |
| 독립 LLM 검수 | 코드 차단으로 도달하지 않음, review 단계 호출 0회 |
| 최종 result SHA-256 | `0e2b88fe6ee5f42585287997f02f536f2b9f1ac4ee851fc73c4207ec1f487c6b` |
| Redis 잔여 작업·미확인 메시지 | 0건 · 0건 |
| 격리 환경 종료 | 실행 중 테스트 컨테이너 0개, 산출물·volume 보존 |
| 기존 서비스 | 컨테이너 12개의 ID·시작 시각·재시작 횟수 동일 |
| 서비스 DB·자동 수집 | 서비스 DB 접근·적재 없음, 자동 수집 시작 안 함 |

코드 검수는 카드 2의 `2026-11-13`을 기사 게시일 노출로 처리했다. 하지만 생성 결과에는 원문의 예약 시작일에 연결된 검증 완료 `product_sales` 사실과 `preorder_start=2026-11-13` 근거가 있다. 게시일과 예약 시작일이 같은 날이고 해당 fact 유형이 기존 날짜 근거 허용 목록에 없어 오탐이 발생했다.

이 근거 연결에 한정해 `quality.py`를 보완했다. 검증된 `product_sales` 사실, 동일 출처·카드 인용, 명시적 `preorder_start`, 같은 원문 문장의 날짜·예약 시작 근거, 카드의 예약 시작 의미가 모두 있어야 예외를 인정한다. 단순 게시일·다른 출처·미검증·날짜 불일치·마감/종료/취소/연기/부정 표현은 계속 차단한다. 관련 신규 회귀 24건을 포함한 최종 **237건이 통과**했고, 실제 원본 패키지를 읽기 전용으로 재검사한 결과 `rechecked_issue_codes=[]`, `rechecked_hard_blockers=[]`였다. 이 검수 보완은 본 문서와 함께 후속 커밋으로 남긴다.

**봉인된 원본 result와 작업의 `rejected` 상태는 그대로 유지한다.** SHA-256도 동일하다. 오프라인 재판정은 아직 실행하지 않은 독립 LLM 검수의 승인을 대신하지 않으며, `ready_for_staging`으로 승격하거나 export·서비스 적재를 하지 않았다.

`execution_status=succeeded`는 작업 처리가 끝났다는 뜻이다. 이번에는 실제 생성된 카드와 본문이 있지만 검수는 반려됐다. **공개 준비 완료**는 `outcome=ready_for_staging` 및 실제 독립 검수 통과까지 별도로 확인해야 한다.

## 사용량·비용

| 항목 | 확인값 |
| --- | --- |
| 모델 | `gpt-6-luna` |
| 사용량 기록 | 7건 |
| 입력 토큰 | 69,256 |
| 출력 토큰 | 6,101 |
| 총 토큰 | 75,357 |
| 추정비용 | **$0.00859036** |
| 목표 예산 | $1, 목표 미초과 |
| 집계 일치 | `usage_records` 합계와 결과의 `usage_summary` 일치 |
| 제공자 청구 내역 | 조회하지 않음 |

최종 결과의 `usage_records`를 한 번만 합산했다. 같은 기록의 집계인 `usage_summary`나 여러 시점의 SQLite 누적 비용을 다시 더하지 않았다. 이 금액은 기록된 토큰에 따른 추정비용이며 제공자 청구서의 확정 금액이 아니다. 독립 LLM 검수에는 도달하지 않았고 해당 review 단계의 사용량 기록은 0건이다.

## 작은 오류와 수정 기록

| 문제 | 원인 | 수정·검증 | 생성 영향 |
| --- | --- | --- | --- |
| 이미지 revision 표기 불일치 | 첫 빌드가 `runtime-base`의 이전 `a7f85c9` revision label을 상속했다. 실제 Agent 소스는 `2ca31f3`였다. | 실제 작업 발행 전에 현재 커밋의 `--label`로 이미지 메타데이터만 보정하고, 작업이 없는 Worker를 교체했다. 최종 digest를 고정했다. | 코드·유료 호출에는 영향 없음. 생성 작업 발행 전 수정 완료. |
| 상태 조회 명령 오류 | 작업 CLI에 없는 `jobs get` 명령을 사용했다. | 지원되는 `jobs show`로 바로잡았다. | 읽기 명령에서 발생했으며 추가 생성·유료 재시도 없음. |
| 예약 시작일을 게시일로 오인 | 검증된 `product_sales` / `preorder_start` 날짜가 게시일과 같지만 기존 날짜 근거 허용 목록에서 누락됐다. | 원문·출처·날짜·카드 의미가 일치할 때만 인정하도록 보완. 마감 제목에 별도 시작 문장을 붙이는 혼동 사례도 차단. 237건 회귀 및 실제 산출물 코드 재검사 통과. | 실제 생성 결과는 반려로 보존. 원본 봉인·작업 상태 변경 및 추가 유료 재생성 없음. |

이전 날짜 처리 검증에서 보정한 테스트 fixture·기대값은 [수정 기록](AGENT_GOODS_RELEASE_PERIOD_FIX_2026-11-16.md)에 별도로 보존했다.

## 근거 파일

- [실행 계획](../../celery-paid-onepiece-period-2026-11-16/run-plan.json)
- [격리 Compose](../../celery-paid-onepiece-period-2026-11-16/compose.yaml)
- [이미지 빌드 로그](../../celery-paid-onepiece-period-2026-11-16/build-generation.log)
- [무과금 회귀 결과](../../celery-goods-period-fix/agent-regression.log)
- [최종 237건 회귀](../../celery-paid-onepiece-period-2026-11-16/agent-regression-post-generation.log)
- [실제 산출물 코드 재검사](../../celery-paid-onepiece-period-2026-11-16/recorded-package-recheck.json)
- [사용량 검증](../../celery-paid-onepiece-period-2026-11-16/usage-verification.json)
- [격리·기존 서비스 유지 확인](../../celery-paid-onepiece-period-2026-11-16/isolation-verification.json)
- [생성 콘텐츠 미리보기](../../celery-paid-onepiece-period-2026-11-16/content-preview.md)
- [보존된 원본 Agent 결과](../../celery-paid-onepiece-period-2026-11-16/artifacts/jobs/039505e4-4e31-456f-b0cf-399acb9c5364/attempts/1909c466-246c-422b-bf50-a53eb1d0c163/result.json)

자동 수집·서비스 DB 적재·피드 공개는 이번 실행 범위에 포함하지 않는다.
