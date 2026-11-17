# 공통 파이프라인 계약

`jobs.py`는 기간 중요/evergreen lane과 crawl/generate/review stage를 공통 식별자로 정의한다. `AgentRepository`는 멱등 enqueue, 원자적 claim/lease, 재시도·완료 처리를 제공한다. SQLite claim은 `BEGIN IMMEDIATE`, PostgreSQL claim은 `FOR UPDATE SKIP LOCKED`를 사용한다. 큐를 기존 CLI/그래프에 연결하는 작업은 후속 단계이며, 현재 모듈 import만으로 DB 초기화나 worker 시작을 하지 않는다. 큐 동작을 바꿀 때 SQLite와 PostgreSQL 양쪽 계약을 함께 유지한다.
