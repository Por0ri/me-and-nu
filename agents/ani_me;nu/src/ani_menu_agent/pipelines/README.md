# 독립 작업 파이프라인

기간형 `ani_me;nu` 실행은 수집, 콘텐츠 생성, 검수 세 프로세스로 분리한다. 각 프로세스는 같은 SQLite DB를 사용하지만 각자 맡은 단계만 처리한다.

```text
출처 탐색·원문/근거/이미지 적재
        ↓ generate 작업을 DB 큐에 추가
독립 생성 워커 → 콘텐츠 초안 저장 → review 작업을 DB 큐에 추가
독립 검수 워커 → 통과 / 생성 수정 큐 / 수집 보완 / 탈락 보관
```

- 수집 워커는 기본적으로 `news`, `event`, `goods`만 주기적으로 탐색한다. `people`, `music`, `humor`, `curation`, `review` 및 작품 상시 정보의 장기 저장 경로는 RAG 프로젝트 범위다.
- 생성 워커는 `generate` 큐만 소비하고 독립 검수 그래프를 호출하지 않는다.
- 검수 워커는 `review` 큐만 소비한다. 수정 반환은 새 생성 큐 작업으로 기록한다.
- 큐 작업은 lane/stage/idempotency key로 중복을 막고 lease로 원자적으로 claim한다. 긴 실행은 lease heartbeat로 점유를 연장한다.
- 각 워커는 `ani-menu pipeline-worker --stage crawl|generate|review`로 개별 실행할 수 있다. `scripts/run_continuous_generation.py`는 세 워커를 별도 프로세스로 함께 감독한다.
- 런처는 실행될 때만 워커를 시작한다. 기존 STOP 표식이 있으면 시작을 거부하며, 실행 중에는 STOP 파일 생성 후 현재 항목을 마칠 때까지 기다린다.
- 이 변경은 DB 레코드를 마이그레이션하거나 현재 워커를 시작하지 않는다. SQLite 초기화는 워커가 실제 실행될 때만 발생한다.

기존 `crawl-type` 및 `crawl-generate`의 직접 실행 경로는 호환을 위해 남겨 두었다. 독립 운영은 `crawl-type --enqueue-only` 또는 런처 경로를 사용한다.
