# 공식 굿즈 출시 기간 처리 보완 — 2026-11-16

## 범위와 원인

- 기준: Checking `a7f85c9e3210cba52831529d52d92bc6e3703824`.
- 수정 브랜치: `codex/goods-release-period`. 기존 `backend-labs` 작업 폴더와 분리한 worktree에서 작업했다.
- 대상: [ONE PIECE 공식 withCAT 기사](https://one-piece.com/news/83393/index.html), 출처 `one_piece_official`, 작품 ID `8`, 유형 `goods`.
- 실제 단건 작업은 `expiry_unresolved_held`로 종료됐다. 원문의 `2027年2月中旬頃発売予定`를 기존 정확한 일자 파서가 인식하지 못했고, 구조화된 판매 상태도 없었다.
- 날짜를 임의의 하루로 바꾸거나 검수 조건을 우회하지 않고, 출시 기간의 정밀도를 보존하도록 보완했다.

## 변경한 동작

1. 명시적인 연도가 있는 일본어 월·상순·중순·하순과 인접한 출시 문구를 처리한다. 연도가 없는 표현, `이후`·`말` 등 아직 지원하지 않는 표현은 추정하지 않는다.
2. 원문과 `release_precision`을 보존한다. 기간 전체가 아직 미래일 때만 `upcoming_release`로 인정한다. 기간 진입 후·경과 후에는 이 표현만으로 판매 여부를 추정하지 않는다.
3. 내부 비교용 `release_window_start/end`는 정확한 출시일이 아니다. `release_date`와 판매 종료일은 만들지 않으며, 생성용 Fact에는 계산한 날짜 범위를 넣지 않는다.
4. CLI와 Agent 내부 pipeline이 원문 근거를 저장하고, SQLite 재조회 후에도 원문을 다시 해석한다. 저장된 숫자 범위를 그대로 신뢰하지 않는다.
5. 품절·판매 종료·상충하는 날짜·명시적인 출시 연기/취소/변경은 계속 차단한다. 일반적인 “출시일은 변경될 수 있음” 안내는 확정된 연기로 취급하지 않는다.
6. 사실 추출·카피 프롬프트에도 월/순 단위와 예정·경 등의 표현을 보존하도록 명시했다. 이는 모든 모델 출력의 무오류를 보장하는 것이 아니며 기존 독립 검수는 계속 필요하다.

DB 스키마, Celery 작업 상태·중복 방지, 추천 코드와 서비스 적재 규칙은 변경하지 않았다. 기존의 종료일 없는 판매 상태에 대한 짧은 재검토 기간도 유지했다.

## 검증

- 네트워크가 없는 Docker 컨테이너, API 키 없음, 테스트별 임시 SQLite를 사용했다.
- 실제로 수집했던 기사 원문을 그대로 재입력한 결과: 기존 `unresolved` → `active / upcoming_release / mid_month`. 정확한 출시일과 종료일은 계속 `null`이다.
- 신규 날짜·기간·품절·연기·윤년·경계값 테스트, CLI 입력 및 저장 경계, SQLite 저장·재조회, 기존 사실 추출·검수·카드·사용량 회귀를 실행했다.
- 최종 결과: **213 passed in 23.60s**. Agent 전체 테스트나 서비스 추천 통합 테스트를 전부 재실행한 것은 아니다.

Agent 디렉터리에서 실행할 관련 회귀 목록은 다음과 같다. 이번 실행에는 테스트 전용 Docker 이미지 `me-nu-anime-period-test:a7f85c9`, `--network none`, 빈 API 키, 읽기 전용 소스와 임시 `/tmp`를 사용했다.

```sh
python -m pytest -o addopts= -q -p no:cacheprovider \
  tests/test_goods_release_period.py tests/test_expected_work_cli.py \
  tests/test_pipeline_recovery_fixes.py tests/test_quality_guardrails.py \
  tests/test_extraction.py tests/test_generation_card_count.py \
  tests/test_review_publication_guards.py tests/test_usage_accounting.py
```

근거: [실제 기사 기간 판정](../../celery-goods-period-fix/actual-article-period-check.json), [관련 회귀 로그](../../celery-goods-period-fix/agent-regression.log).

## 진행 중 수정한 작은 문제

- 새 CLI 테스트 fixture에 필수 `content_kind`가 빠져 있었다. `information`을 명시하여 수정했다. 실제 Agent 오류가 아니며 LLM 호출은 없었다.
- 기존 Checking 소스로 비교 실행한 결과, 기존 회귀 142건 중 12건이 이미 실패했다. [기준 실행 로그](../../celery-goods-period-fix/baseline-existing-regression.log)를 보존했다.
  - 9건: 검수 테스트의 모의 저장 함수가 현재 API의 `usage_records` 인자를 받지 못했다. 모의 함수 시그니처만 보정했다.
  - 1건: 기사 게시일만 오래되면 차단한다는 과거 기대값이었다. 현재의 출처 근거 기반 유효기간 정책에 맞췄다.
  - 1건: 이미지 재사용을 허용 가능한 수정 사항으로 보던 과거 기대값이었다. 현재의 더 엄격한 게시 차단을 검증하도록 고쳤다.
  - 1건: 이미지 부족 테스트가 카드 수와 같은 수의 이미지를 제공했다. 실제 최소치보다 적은 이미지 한 개를 제공하도록 고쳤다.
- 위 보정으로 production 검수·점수·이미지·추천 코드를 완화하지 않았다.
- 저장 mock을 고친 후 가려져 있던 기대값도 확인했다. 이미지 차단은 `hard_blockers`에 들어가며 `actionable_issues`는 수정 가능 수준의 항목만 담는 현재 규칙에 맞췄다.
- `verified=False` 단독 차단은 기존 Checking 커밋 `0524a89`에서 이미 제거됐다. 해당 테스트는 독립 검수 대기를 검증하도록 바꾸고, 별도로 출처 ID가 끊긴 사실은 `FACT_SOURCE_MISSING`으로 LLM 호출 전에 차단되는 테스트를 추가했다. 해당 중간 결과(210 통과·2 실패)는 [로그](../../celery-goods-period-fix/agent-regression-stale-review-expectations.log)에 보존했다.

## 실행·배포 상태

이번 수정 검증의 실제 LLM 호출과 서비스 DB 접근은 0회다. 이전 보류 작업 이력과 산출물은 그대로 보존했다. 유료 재생성, 자동 수집, 원격 push·merge·배포는 이번 수정 단계에서 실행하지 않았다.

수정은 로컬 `codex/goods-release-period` worktree에 있으며 Checking과 운영 서비스에는 아직 반영하지 않았다. 실제 생성 완료·결과물 품질·서비스 적재까지 통과했다는 의미는 아니다.

## 담당자 전달용 초안

> 원피스 공식 굿즈가 “2027년 2월 중순경 출시 예정”을 인식하지 못해 생성 전 보류돼, Checking 기준으로 월·상순·중순·하순을 원문 정밀도 그대로 처리하도록 보완했습니다. 정확한 날짜를 임의로 만들지 않고 품절·종료·연기 및 기존 검수 차단은 유지합니다. 격리 환경의 관련 회귀 213건 통과, 실제 LLM 호출·서비스 DB 쓰기는 0회입니다. Celery 중복 방지·DB 스키마·추천 코드는 변경하지 않았고, 현재 로컬 수정 단계이며 아직 배포하지 않았습니다.

외부 메시지는 발송하지 않았다.
