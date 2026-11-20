# Main 공유 DB 관리자 이력 탈퇴 보호

2026-11-20. Main `ea36ce921bd598942fc332ac5f81ddf56e8f6dc8` 기준의 미커밋 배포 후보다. 실제 서버·서비스 DB·권한은 변경하지 않았다.

## 변경 이유와 동작

Main과 Checking이 같은 회원 DB를 사용하지만 Main의 탈퇴 서비스에는 관리자 이력 확인이 없었다. Checking에서 부여하거나 회수한 관리자 계정을 Main에서 탈퇴 상태로 바꾸면 권한 및 감사 이력과 회원 상태가 충돌한다.

- 탈퇴 요청은 기존 `lock_user`로 회원 행을 `FOR UPDATE` 잠그고 `populate_existing`으로 상태를 다시 읽는다. 대기 중 상태가 바뀌면 `409 WITHDRAWAL_STATE_CONFLICT`로 중단한다.
- 크리에이터 채널 확인에 더해 `admin_account`의 `admin_account_id/user_id`만 SQLAlchemy Core로 조회한다. `viewer/operator/admin` 역할과 회수 여부에 관계없이 이력이 있으면 `409 WITHDRAWAL_BLOCKED`다.
- Checking의 grant/bootstrap은 같은 회원 행을 잠그고 활성 상태를 검사한 뒤 이력을 삽입한다. 양쪽이 같은 회원 잠금을 사용해 먼저 완료된 처리를 이후 작업이 확인한다.
- 테이블 미존재·조회 권한 오류·DB 오류는 `503 WITHDRAWAL_UNAVAILABLE`로 중단하고 트랜잭션을 롤백한다. 이때 회원 상태·탈퇴 시각·로그인 세션·쿠키를 바꾸지 않는다. DB 내부 오류는 응답에 포함하지 않는다.
- 일반 회원의 30일 유예, 복구, 세션 종료, 크리에이터 채널 차단과 CSRF 계약은 유지한다. 화면은 `WITHDRAWAL_BLOCKED`의 안전한 서버 메시지를 표시해 관리자 이력과 채널 사유를 구분한다.

## 배포 전제와 새 환경의 호환성

이 변경은 관리자 ORM·권한 API·역할 부여 기능을 Main에 추가하지 않는다. Main Alembic 목표도 기존 `d7b3e9a1c452`로 유지한다.

배포 전에 연결 대상 공유 DB에 Checking의 `47c1a8d6b302` 변경이 적용되어 `admin_account`가 존재하고, Main API DB 계정이 필요한 열을 조회할 수 있음을 읽기 전용으로 확인해야 한다. Main 배포 구성은 API 전용이며 이 후보로 마이그레이션이나 seed를 자동 실행하면 안 된다. Main의 구형 Alembic으로 공유 DB의 최신 migration 이력을 변경하지 않는다.

Main 마이그레이션만 적용한 신규/구형 DB에는 관리자 표가 없다. 그 상태에서는 **일반 회원도 탈퇴 요청이 503으로 안전하게 중단**된다. 표 부재를 '관리자 없음'으로 해석하거나 설정으로 우회하지 않는다. 새 환경도 별도 검토한 스키마 준비가 선행되어야 한다. 이는 이번 최소 패치의 의도적인 배포 전제다.

권한 부여를 이 잠금 계약 없이 SQL로 직접 실행하는 운영 절차는 경합 보호 범위가 아니다. Checking의 검증된 grant/bootstrap 경로를 사용해야 한다.

## 격리 검증

`backend/scripts/run_main_withdrawal_checks.py`는 기존 로컬 `pgvector/pgvector:pg17` 이미지로 고유한 임시 컨테이너를 만든다. 127.0.0.1 임의 포트, tmpfs, 합성 데이터만 사용하며 `.env`가 있으면 실행을 거절한다. 운영 DB에 연결하지 않고 종료 시 생성한 컨테이너만 제거한다.

실행 명령(저장소 루트):

```powershell
& C:/Users/USER/me_nu/backend/.venv/Scripts/python.exe backend/scripts/run_main_withdrawal_checks.py
$env:NODE_PATH = 'C:/Users/USER/me_nu/frontend/node_modules'
node --test frontend/tests/pivot-my-page.test.cjs frontend/tests/pivot-service-flow.test.cjs
& C:/Users/USER/me_nu/backend/.venv/Scripts/python.exe -m unittest discover -s deploy/main -p test_main_validate.py -q
```

검증 범위:

- SQLite 실제 API·인증·CSRF·세션·복구·신고 회귀, 관리자 3개 역할의 활성/회수 이력, 다른 회원의 이력, 표 부재·조회 오류, 오래된 회원 상태, 실패 시 로그인 보존.
- PostgreSQL 실제 AsyncSession과 행 잠금으로 grant가 먼저 확정된 경우 탈퇴 차단, 탈퇴가 먼저 확정된 경우 grant 활성 상태 검사의 거절을 확인한다. `pg_blocking_pids`로 실제 대기를 관찰한다. 권한 부여 쪽은 Checking과 같은 잠금/상태 검사 SQL 계약의 시험 구현이며 Main에 권한 부여 코드를 추가하지 않는다.
- PostgreSQL 신규 Main 스키마에서 관리자 표가 없을 때 503 및 중단된 트랜잭션 롤백, 관리자 이력 차단 시 계정·세션 보존, 알림 설정의 탈퇴/복구 회귀.
- 프런트 마이페이지/서비스 흐름 및 Main 배포 입력 검증. 실제 브라우저 로그인·운영 탈퇴는 수행하지 않는다.

결과: 백엔드 전체 실행 89건 중 87건이 통과했고, 추가 Redis 2건은 로그인 준비의 기존 commit을 탈퇴 commit으로 세는 시험 오류였다. 제품 코드 변경 없이 탈퇴 직전 횟수 대비 증가가 없는지로 시험을 수정해 해당 2건이 통과했다. PostgreSQL 5건(양방향 경합 포함)과 알림 설정 9건은 전체 실행에서 통과했다. 프런트 마이페이지 26건·서비스 흐름 11건, Main 배포 입력 검증 14건도 통과했다. 임시 컨테이너 삭제를 확인했다.

검증 로그·소스 해시·기계 판독 요약은 `out/main-admin-withdrawal-evidence-20261120/`에 보관한다. 기본 sandbox에서 출력 없이 정체된 Python 검사는 종료하고 같은 격리 검사를 승인된 실행 환경에서 재시도했다. 커밋·푸시·머지·배포·권한 부여는 수행하지 않았다.
