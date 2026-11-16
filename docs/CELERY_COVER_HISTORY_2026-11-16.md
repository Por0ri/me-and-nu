# Celery 작업 간 이전 표지 이력 전달

2026-11-16. 기준 Checking `80dc426` (PR #77 포함). 추가 LLM 호출과 서비스 DB 쓰기 없이 변경·검증한다. 기존 공개 콘텐츠와 추천 서비스는 유지한다.

## 변경

- 작업마다 분리한 Agent SQLite는 유지한다. 완료된 작업/시도 증거를 조회해 기존 result, manifest, PNG를 검증한 다음 첫 표지 metadata를 새 작업에 전달한다.
- 반려·held 및 과거 시도·중복 형제 이력을 보존한다. 원본 URL/입력 해시와 기존 산출물은 수정하지 않는다.
- immutable snapshot의 SHA/job/attempt를 검증하고 실제 Agent loader로 생성 전 확인한다. CLI 수집 이전과 생성·수정 시 재검증한다. 새 manifest v3에 이력을 봉인하며 기존 v2 읽기는 유지한다. 작업 DB 마이그레이션 없음(v7 유지).
- 전용 session advisory lock으로 동시 생성과 이력 조회 경합을 막는다. busy는 정확한 배달을 재발행 가능하게 되돌리고, admission 연결 장애는 ACK 대신 broker requeue한다. 실행 중 연결 상실은 subprocess를 중단한다.
- 기존 공개 콘텐츠 4044의 원래 반려 결과는 독립 검수 이력을 그대로 두고, 봉인된 원본에서 표지 metadata만 bootstrap으로 검증한다. 나머지 공개 3건은 전용 작업 DB 이력에 있다.

## 보장 범위

검증·봉인이 완료된 과거 표지의 동일 visual key 재사용을 막는다. 본문 카드 이미지 재사용은 허용한다. 생성 여부가 불명확한 시도는 다음 큐를 멈추지 않는 기존 정책을 유지한다. 이런 시도를 늦게 복구해 공개할 때는 이후 생성물과 별도 중복 검수가 필요하다. 모든 수동 콘텐츠·미확정 결과·의미가 유사한 이미지까지 중복 차단하는 기능은 아니다. 구버전 생성 Worker를 함께 실행하면 안 된다.

## 무과금 검증

검증 환경은 비밀키 없는 컨테이너, 외부 네트워크 없는 Agent 실행, 전용 임시 PostgreSQL/Redis다.

- Worker 단위 123건, PostgreSQL 72건 통과. 잠금 경합/연결 상실, 유효 lease 대기, 기존 스키마 이력 보존 및 원본 봉인 검증 포함.
- 실제 Redis/Celery: 동일 기사 5회 접수→1건, 잘못된 큐 점유 거부, 중복 배달 무시. busy 배달은 attempt 0으로 재대기→두 번째 배달에서 attempt 1로 성공. 반려 결과의 표지가 다음 작업 snapshot에 전달됨(3작업).
- 실제 Redis/Celery 재시도 6시나리오·12작업 통과. 확정된 시작 전 실패 최대 3회, 모호한 생성 1회 후 needs_attention, 이후 작업 정상 진행.
- Agent 신규 표지/CLI/작품/실제 Worker→Agent bridge: 61건 통과. 새 snapshot 검증 40건과 bridge 4건을 포함한다.
- 추천 피드·콘텐츠 적재·승인·추천 배치: 222건 및 subtest 4건 통과.
- 기존 image_selection 회귀의 설명 문자열 2건 실패는 변경 전부터 존재한 문제다. 이번 변경에서 해당 파일은 수정하지 않았다. PR #77 전체 기존 실패 기준선은 `CHECKING_PR77_OFFLINE_REVIEW_2026-11-16.md`에 남겨두었다.

## 진행 중 발견·수정한 사항

- Windows lstat/fstat의 ctime 차이로 정상 snapshot을 거절했다. inode/device/size/mtime와 SHA 검증을 유지하고 ctime 비교만 제거했다. Linux 경로·symlink 테스트도 실행했다.
- 대량 이력 테스트에서 artifact 전체 문자열 조인이 오래 걸렸다. 엄격한 경로에서 추출한 attempt UUID의 PK 조인을 추가했다. 잘못된 경로는 계속 차단한다.
- guard admission 장애 시 일반 task 실패로 처리하면 메시지가 ACK될 수 있어 명시적 broker requeue로 보완했다.
- 검증 명령의 단일 phase 인자, PYTHONPATH와 bind mount 범위를 바로잡았다. 이미지에 내장된 출처·작품 파일을 가리는 전체 `/app` 마운트를 제거했다. 서버·유료 호출과 무관한 로컬 검증 설정 오류다.
- 추가 PG 테스트가 probe 제외를 확인하려고 기존 작업 종류를 직접 바꾸다가 불변 제약에 걸렸다. 종류 변경은 허용하지 않고 별도 probe를 정상 접수하는 테스트로 수정했다. 기존 DB 제약과 업무 코드는 유지했다.

## 배포

검증 완료 후 Checking에 커밋·푸시하고 서버에 비활성 배포한다. API와 추천 컨테이너는 재시작하지 않는다. 생성 Worker는 새 이미지·표지 초기 이력으로 생성만 하고 시작하지 않으며 키 마운트를 제거한다. 전후 작업 이력, 공개 4건·PNG 12장, 추천 행 수와 서비스 컨테이너를 비교한다.
