# 관리자 BE 1차 통합 계약 · 2026-11-18

기준: Checking `a0a1af2`와 PR #71 `e96e267`. 독립 브랜치 `codex/admin-be-integration-20261118`에서 통합했다. 이 문서는 이전 foundation/review 문서의 미등록 Router, 기본 거부 Provider, 상세 updatedAt 미제공 설명을 대체한다. 코드 통합은 공유 DB Migration·실제 권한 부여·배포 실행을 뜻하지 않는다.

## PR #71 검토 결과

- `admin_account`는 사용자당 활성 grant 1개를 부분 unique index로 보장한다. 회수 후 새 행으로 재부여하여 과거 권한과 조치 이력을 유지한다.
- `admin_action_log`는 grant FK를 참조한다. downgrade는 두 테이블을 잠근 뒤 이력이 있으면 중단한다. 운영 이력 삭제를 통한 downgrade는 허용하지 않는다.
- role 문자열은 DB enum이 아니다. Provider/부여 서비스가 `viewer`, `operator`, `admin`만 허용하며 알 수 없는 역할은 거부한다.
- 회수된 grant를 포함해 관리자 이력이 있는 사용자의 일반 탈퇴는 `409 WITHDRAWAL_BLOCKED`다. 이 정책을 유지한다. grant 부여와 탈퇴가 동시에 실행되는 경우에는 같은 UserAccount 잠금으로 직렬화한다.
- 명시적 서비스 revision은 `47c1a8d6b302`; 이전 revision은 `d7b3e9a1c452`다. 사용자 생년월일 삭제 정책과 관련된 별도 revision을 섞지 않는다.

## 권한과 운영 범위

| 역할 | 콘텐츠 목록·상세 | 콘텐츠 승인·반려 | 관리자 부여·회수·이력 조회 |
| --- | --- | --- | --- |
| viewer | 허용 | 거부 | 거부 |
| operator | 허용 | 허용 | 거부 |
| admin | 허용 | 허용 | 허용 |

콘텐츠 범위는 이 서비스에 저장된 전체 콘텐츠다. 소유자별 제한이나 임의 이메일 allowlist는 사용하지 않는다. 회원·신고/문의 GET 및 그 외 쓰기 API는 이 변경에 포함하지 않는다. 이 역할표를 해당 미구현 API의 접근 허가로 간주하지 않는다.

읽기는 매 요청 활성 서비스 계정과 현재 grant를 조회한다. 쓰기는 `UserAccount → AdminAccount → Content → Draft` 순서로 잠그고, Content/Draft 변경과 성공 감사 기록을 하나의 트랜잭션으로 커밋한다. 개발 인증 bypass, 클라이언트 role/header, 과거 세션 role로 관리자 권한을 얻을 수 없다. 권한 저장소 확인 실패는 503이며 허용으로 대체하지 않는다.

권한 부여·회수는 관리 작업 전용 advisory lock, 정렬된 사용자 행, grant 행 순서로 잠근다. `admin`만 실행할 수 있고 사유와 감사 기록이 필수다. 마지막 활성 admin 회수는 거부하며, 역할 변경은 회수 후 새 grant 부여로 처리한다.

## FE 요청·응답 계약

`POST /api/v1/admin/contents/{contentId}/review`, 최초 성공과 재응답 모두 **HTTP 200**이다. 실세션 쿠키, 현재 읽기·쓰기 권한, `X-CSRF-Token`이 필요하다.

```json
{
  "requestId": "6a019f7c-ae76-4450-b357-dd31798b91ca",
  "action": "approve",
  "rationale": "출처와 본문을 확인했습니다.",
  "expectedStatus": "pending_review",
  "expectedJudgmentStatus": "needs_review",
  "expectedUpdatedAt": "2026-11-18T09:12:34.123456+09:00"
}
```

추가 필드는 거부한다. `action`은 approve/reject, 사유는 양끝 공백 제거 후 1–2,000 Unicode code point다. 예상 상태는 active/pending_review, 예상 판정은 needs_review다.

`expectedUpdatedAt`은 상세 GET의 `updatedAt` 원문을 그대로 사용한다. 형식은 `YYYY-MM-DDTHH:mm:ss[.ffffff](Z|±HH:mm)`이며 소수는 생략하거나 1–6자리다. 7자리 이상, timezone 없는 값, 알 수 없는 offset `-00:00`, 잘못된 날짜/offset은 거부한다. JS Date 재직렬화는 마이크로초를 잃을 수 있으므로 사용하지 않는다. 버전 비교는 동일 시각·마이크로초 기준이다.

성공 응답: `contentId`, `draftId`, `action`, `status`, `judgmentStatus`, `draftStatus`, `publishedAt`, `updatedAt`, `auditLogId`, `replayed`. 두 시각 필드는 AwareDatetime으로 검증하며 UTC `Z` 또는 명시적 offset을 포함한다. 반려의 `publishedAt`은 null일 수 있다. 기존 발행 시각의 순간은 보존한다. 승인되었다고 소비자 피드 공개 조건을 우회하지 않는다.

### 상세 GET의 실행 가능 정보

```json
{
  "review": {
    "allowedActions": ["approve", "reject"],
    "disabledReason": null
  }
}
```

현재 사용자 권한과 대상 상태를 함께 계산한다. 불가 시 allowedActions는 빈 배열이고 disabledReason은 다음 값 중 하나다.

| 값 | 의미 |
| --- | --- |
| permission_denied | 현재 쓰기 권한 없음 |
| deleted | 삭제된 콘텐츠 |
| content_not_reviewable | active/pending_review + needs_review 조합이 아님 |
| draft_not_reviewable | 연결된 Draft가 없거나 checking/approved가 아님 |
| unavailable | 권한 판정을 하지 않은 내부 DTO 기본값; FE는 비활성 처리 |

실제 HTTP 권한 조회 장애는 503으로 응답한다. FE는 필드 누락·오류도 비활성으로 처리한다. 이 값은 표시용 현재 상태이며 POST 권한·버전 검사와 잠금을 대체하지 않는다. 현재 FE 실행 버튼 활성화와 이 추가 필드의 FE 타입 연결은 FE 후속 작업이다.

### 멱등·재확인·오류

- 키 범위는 **admin_account_id + contentId + requestId**다. 같은 성공 기록과 같은 정규화 요청이면 최초 응답과 동일한 auditLogId를 `replayed=true`로 반환한다. 새 상태 변경/감사 행을 만들지 않는다.
- 정규화된 요청에는 시각 offset 표현과 조치·사유·기대 상태가 포함된다. FE는 원래 6개 필드를 통째로 유지한다. 같은 시각으로 변환했더라도 offset 표현을 바꾸면 다른 fingerprint일 수 있다.
- 재응답은 최초 커밋 결과다. 최신 현재 상태는 성공 후 GET으로 확인한다. 현재 상태가 기대 결과와 같다는 이유만으로 특정 requestId의 성공을 추정하지 않는다.
- 매번 현재 권한을 먼저 검사한다. 회수된 grant는 replay도 거부한다. 회수·재부여로 grant ID가 달라지면 기존 replay 범위 밖이다. 콘텐츠 행이 물리 삭제된 경우도 기존 replay를 제공하지 않는다.
- **1차 통합의 기간 정책:** 성공 감사 기록과 grant 이력에 자동 삭제/만료 작업을 두지 않는다. 같은 grant·권한과 콘텐츠 행, 정상 감사 기록이 유지되는 동안 별도 시간 만료 없이 replay한다. 고정 24시간/30일 등의 SLA나 권한 회수 후 replay는 약속하지 않는다. 향후 보존 기간·아카이빙을 도입할 때는 replay와 적재기 상태 보호 기록을 함께 보존하는 별도 변경이 필요하다.
- 503/5xx/408/429/네트워크 유실/형식이 잘못된 200은 결과 불확실이다. 새 requestId를 자동 생성하지 않고 **동일 POST**로 확인한다. 무한 재시도하지 않는다. requestId별 별도 조회 API는 없다.
- 401은 재인증, 권한 관련 403은 중단한다. 불확실 요청 뒤 권한 회수로 재확인이 불가능하면 BE가 감사 기록으로 확인한다. CSRF 갱신은 원래 조치 payload/requestId를 바꾸지 않는다.
- 409 `REVIEW_STATE_CONFLICT`는 상태·버전·DB 잠금 충돌이다. 최신 상세와 review 메타데이터를 재조회한 뒤 사용자가 새 조치를 판단한다. 409 `REVIEW_REQUEST_CONFLICT`는 기존 키의 다른 요청이므로 원요청을 먼저 확인한다. 새 조치에만 새 ID를 부여한다.
- 422는 요청 검증 오류다. 오류에는 내부 DB/자격 증명 정보를 노출하지 않는다. 503 메시지도 확정적인 미저장을 주장하지 않는다.

감사 기록은 성공한 검수/권한 변경과 같은 트랜잭션에 저장한다. 일반 관리자에게 감사 수정·삭제 API를 제공하지 않는다. 실패·거부 보안 이벤트의 별도 저장 파이프라인과 운영 보존 기간의 최종 조직 정책은 이 성공 감사 테이블과 구분하며, 이번 구현 완료 범위에 포함하지 않는다.

## 적재기 상태 보호

적재기는 기존 Content를 먼저 FOR UPDATE로 잠그고 Draft를 잠근 뒤 ORM 값을 새로 읽는다. 관리자의 content.approve/content.reject 감사 이력이 있으면 일반 동일 재적재는 Content/Draft/AgentRun을 그대로 유지한다. 새롭거나 다른 외부 operator_review로 관리자 결정을 변경하려는 적재는 전체 배치를 중단한다. 관리자 승인 과정에서 제거한 preview 메타데이터도 복구하지 않는다.

감사 테이블이 없거나 조회에 실패하면 상태 보호를 우회하지 않는다. 따라서 기존 콘텐츠를 재적재하기 전에도 #71 Migration이 필요하다. 직접 SQL이나 별도 writer가 이 프로토콜을 우회하는 것까지 자동 보호하는 DB trigger는 도입하지 않았다. 반려 복구/재검수는 별도 정책·API가 필요하다.

## Migration 및 최초 관리자 적용 절차

1. 이 통합 변경과 #71 검토 결과를 확인하고 대상 환경을 명시한다. 현재 작업에서는 공유 DB를 변경하지 않았다.
2. 배포 대상 DB의 현재 revision을 확인한다. 백업/복구 절차를 준비한 뒤 `python -m alembic upgrade 47c1a8d6b302`를 적용한다. DB URL은 배포 환경의 비밀 설정으로 주입하고 로그에 출력하지 않는다.
3. 아래 CLI로 초기 관리자 서비스 user_id를 명시한다. **실제 계정은 임의로 선택하지 않는다.** grant 이력이 전혀 없을 때만 bootstrap이 가능하다.
4. API의 권한별 GET/POST와 감사 기록을 점검한 뒤 FE가 버튼 활성화 및 브라우저 E2E를 수행한다. 로컬 코드의 Router 등록과 실제 운영 배포는 별개다.

```text
# backend 디렉터리. ADMIN_DATABASE_URL을 의도한 PostgreSQL 주소로 별도 주입한다.
# 아래 명령은 --apply 없이는 DB에 접속하지 않는 계획 출력이다.
python scripts/manage_admin_grants.py bootstrap --database-name <DB명> --target-user-id <회원ID> --rationale "초기 관리자 지정" --confirm-first-admin
python scripts/manage_admin_grants.py grant --database-name <DB명> --actor-user-id <관리자회원ID> --target-user-id <대상회원ID> --role operator --rationale "콘텐츠 검수 담당 지정"
python scripts/manage_admin_grants.py revoke --database-name <DB명> --actor-user-id <관리자회원ID> --admin-account-id <권한ID> --rationale "담당 변경"
python scripts/manage_admin_grants.py list --database-name <DB명> --actor-user-id <관리자회원ID>
```

변경 적용 시에만 동일 명령에 `--apply`를 추가한다. URL/대상 DB 확인과 역할 검사를 통과해야 하며 감사 저장 실패 시 권한 변경도 롤백한다. URL query로 host/dbname/service를 바꾸는 입력은 거부하고, 연결 후 실제 DB 이름도 확인한다. `PGHOSTADDR`, `PGSERVICE`, `PGSERVICEFILE`이 설정된 실행 환경은 `AMBIENT_DATABASE_ROUTING`으로 거부하므로 명시한 대상만 사용하는 별도 실행 환경을 준비한다. CLI는 신뢰된 BE 운영자용이며 외부 HTTP 사용자 입력으로 감싸지 않는다.

## 재현 가능한 검증

`backend/scripts/run_admin_postgres_checks.py`는 기존 로컬 pgvector 이미지를 사용해 임의 이름의 loopback 전용 컨테이너와 tmpfs DB를 만들고, 실제 Migration·AsyncSession 테스트 후 해당 컨테이너만 제거한다. 애플리케이션 `.env`의 DB를 대상으로 사용하지 않는다.

```text
python scripts/run_admin_postgres_checks.py
# 실제 수동 CLI 양성 경로만 별도 확인
python scripts/run_admin_postgres_checks.py --cli-smoke-only
```

PostgreSQL 통과 기준: Migration 왕복/이력 보호, 활성 grant unique, GET 원문 버전→POST→감사→재조회 HTTP 흐름, microsecond 충돌/offset, 중복·상반 조치 경쟁, 실제 감사 INSERT 실패 롤백, 응답 유실 뒤 replay, grant 회수 경쟁, 적재기와 관리자 검수의 양방향 잠금 경쟁이다. Content.updatedAt은 모든 출처/슬라이드의 복합 버전이 아니다.

### 실행 결과

- 관리자 조회·검수·FE HTTP 계약·메타데이터/시각 회귀: **276 passed**.
- Migration·권한·CLI·탈퇴·알림/인증 관련 회귀: **88 passed, 8 skipped**. opt-in 외부 PostgreSQL 테스트는 지정하지 않았으며, 아래 별도 신규 통합으로 실DB를 검증했다.
- 적재기 관련 회귀: **97 passed, 2 skipped**, 신규 상태 보호 16건 포함.
- Checking 배포 설정/Bootstrap 검증: **101 passed, 15 subtests passed**. 배포는 실행하지 않았다.
- 실제 PostgreSQL **17.11 / pgvector 0.8.6**: 전용 일회성 Docker에서 **20 passed (51.71초)**. 위 통과 기준에 더해 DB trigger로 감사 INSERT 실패 및 지연 COMMIT 실패, 실제 lock_timeout을 재현했다. 생성한 컨테이너는 검증 후 제거했다.
- Windows 실제 CLI smoke: 별도 임시 DB public Migration → 합성 회원 bootstrap `--apply` → list → 권한/감사 각 1건 확인, **종료 코드 0**. 실제 계정 부여가 아니다.
- CLI 환경 경로 우회 차단 후 추가 검증: CLI 관련 **8 passed** (환경변수 회귀 3건 신규 포함). 기존 통합 결과와 중복 집계하지 않는다.

공유·운영 DB Migration, 실제 관리자 계정 부여, FE 브라우저 E2E, 배포는 수행하지 않았다. 실패/거부 보안 이벤트 별도 보존 파이프라인과 회원·신고 GET은 이 변경 범위 밖이다.
