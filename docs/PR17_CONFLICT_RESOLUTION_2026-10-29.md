# PR #17 충돌 해결 및 로컬 검증 — 2026-10-29

## 로컬 검증 완료 시점의 상태

- 대상 PR: https://github.com/Por0ri/me-and-nu/pull/17
- Checking 기준: `08f9294efb6f8a237e3d14d1183b84e77ebb3a1b` (`merged curation logic`)
- 에이전트 PR 기준: `5335811fb5c50c064584e4de9306ec1bdd4e32b1`
- 해결 후보 브랜치: `codex/agent-pr17-resolution`
- 작업 폴더: `C:/Users/USER/.codex/worktrees/agent-pr17-resolution/me_nu`
- 변경은 작업 폴더에만 보관했다. 실제 Git 병합, 커밋, 푸시, 배포는 하지 않았다. Checking과 원본 PR 브랜치는 그대로다.
- 따라서 GitHub PR의 충돌 표시는 아직 남는다. 이 문서는 로컬에서 검증한 해결 후보를 기록하며 원격 병합 완료를 뜻하지 않는다.

## 후속 반영 승인

로컬 검증 이후 사용자가 해결 내용을 커밋해 PR #17 원본 브랜치에 푸시하고 Checking으로 병합하도록 승인했다. main 변경과 백엔드·DB 배포는 승인 범위에서 제외된다. 아래 테스트 결과는 반영 대상 코드의 검증 기록이며, 실제 푸시·병합 결과는 PR #17 이력에서 확인한다.

## 충돌 처리 기준

원래 충돌은 59개(내용 32개, 수정/삭제 27개)이며 모두 에이전트 폴더 안이다. Checking의 PR #11 리버트와, 해당 확장 코드를 이어서 수정한 PR #17이 충돌했다. 추천 통합 직전과 직후의 충돌 파일은 동일했다.

Checking을 기준으로 별도 작업 폴더를 만들고 PR의 에이전트 파일 155개를 누락 없이 복원했다. 에이전트 폴더 밖의 Checking 추적 파일은 변경하지 않았다. 새 서비스 계약 테스트와 이 작업 기록만 추가했다. 기존 Checking 폴더의 도메인 문서 미커밋 변경도 그대로 보존했다.

유형별 수집, 작품 이름 매칭, 이미지·상세 본문 생성, 레이아웃과 검수 개선을 유지한다. PR의 편집 점수 정책(전체 68, 카드별 55, 언어 항목 2)도 유지하며 이전 점수로 되돌리지 않는다. 미검증 사실·권리·안전 차단, 실제 LLM 검수 필요, 오프라인 fixture의 저장·게시 금지, 시각·상세 검수 누락 차단을 별도로 검증했다.

Checking의 온보딩, 추천 모델, DB 모델·마이그레이션, API, 홈·상세·저장 연결과 생년월일 보류 상태는 그대로다.

## PR 원본에 추가로 수정한 오류

1. `review_rules.py`: `CARD_COUNT` 규칙 문장 뒤의 쉼표 때문에 문자열이 튜플로 만들어지던 오류를 수정했다. 규칙 생성과 프롬프트 사용을 회귀 테스트로 확인했다.
2. `agents/reviewer/agent.py`: 오프라인 fixture의 카드 번호가 초안과 다를 때 모드가 `invalid`로 바뀌어 실제 저장 경로로 진입하던 오류를 수정했다. 오프라인 모드를 유지해 종료하고 저장·게시하지 않는다. 실제 LLM 응답의 카드 번호 불일치는 기존처럼 차단한다.

그 외 에이전트 생산 코드는 PR 원본을 유지했다. 오래된 테스트의 프로필 함수·점수·레이아웃·이미지 fallback·설정·사용량 기대값을 현재 PR 정책에 맞췄다. 출처·작품·권리 불일치 거절과 보조 이미지 설명, 작은 원본 해상도 검사, 검수/비용 경계 테스트도 유지·보강했다.

## 검증 결과

- 에이전트 전체: **479 통과**, 실패·오류·스킵 0.
- Checking 콘텐츠 적재·피드·상세·저장·활동 API: **173 통과**, Windows에서 심볼릭 링크 생성이 불가능한 검사 1개 스킵.
- 신규 에이전트→서비스 계약: **12 통과**.
- Checking 프론트: **332 통과**.
- 합계 **996 통과**, 환경 제약 스킵 1개. 수정한 주요 테스트의 Ruff와 Git 공백 검사 통과.

계약 테스트는 PR의 실제 `AgentRepository`가 임시 SQLite에 저장한 패키지·PNG·HTML overlay를 읽어 기존 Checking importer에 전달한다. 현재 콘텐츠 유형 10개, 작품 ID와 매칭 증거, 상세 본문, PNG 해시·텍스트 영역, 반복 적재를 검증했다. 에이전트의 `ready_for_feed`만으로 공개하지 않으며 별도 합성 운영자 승인 기록 이후 공개 projection·PNG 조회가 가능함을 확인했다. 서비스의 3~10장 계약도 유지한다.

중간 bundle 조립은 테스트 fixture이다. 실제 LLM 생성·독립 실검수, 외부 크롤링, 실제 PostgreSQL·서비스 DB, OAuth와 배포는 실행하지 않았다. 실제 생성부터 자동 운영 적재까지의 실행 완료를 뜻하지 않는다. 최종 에이전트 테스트에서 외부 네트워크·실제 .env·PostgreSQL·subprocess·소스 쓰기 시도는 모두 0이었다.

## 검증 기록과 후속 반영

기록 폴더: `C:/Users/USER/me_nu/out/pr17-resolution-2026-10-29/`

- `resolution-audit.json`: 기준 SHA, 59개 충돌 파일, PR 파일 보존, 원본 Checking 불변 확인.
- `agent-final-summary.json`, `agent-final.log`, `agent-final.xml`: 최종 479개 테스트 결과.
- `backend-carousel-summary.json`: 서비스 회귀 결과와 격리 상태.
- `service-contract-tests.log`: 신규 교차 계약 12개 결과.
- `frontend-tests-final.log`: 프론트 332개 결과.
- `run_agent_suite.py`, `run_backend.py`, `run_service_contract.py`: 이번 검증에 사용한 격리 실행 도구.

반영 승인을 받으면 원격 Checking/PR의 최신 SHA를 다시 확인하고, PR 브랜치에 Checking의 이력과 이 해결 후보를 함께 반영해야 한다. 파일을 덮어쓰는 것만으로 공통 조상과 충돌 이력이 정리되는 것은 아니다. 실제 PR 병합과 원격 푸시는 별도 승인 이후 진행한다.
