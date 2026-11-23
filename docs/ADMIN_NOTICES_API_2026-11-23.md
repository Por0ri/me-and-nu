# 관리자 공지 작성과 예약 발송

`/admin/notices`의 기존 화면을 실제 공지 저장소와 연결한다. 작성한 공지는 임시 저장하거나 즉시 게시하고, 한국 시간으로 예약할 수 있다. 게시 시점의 활동 회원에게 기존 서비스 내 알림함으로 전달한다. 외부 이메일·문자·브라우저 푸시는 이 기능에 포함하지 않는다.

## 완료 범위와 정책

- `draft`: 다시 열어 제목·본문 수정, 즉시 게시, 예약이 가능하다.
- `scheduled`: 예약 취소만 가능하다. 취소하면 초안으로 돌아가며 수정 후 다시 예약할 수 있다.
- `published`: 게시된 내용은 고정한다. 게시 후 수정·삭제 API는 제공하지 않는다.
- 제목 1–100자, 본문 1–5,000자. 서버에서 앞뒤 공백을 정리하고 길이를 검증한다. 임시 저장에도 제목과 본문이 필요하다.
- `scheduledAt`은 타임존 offset을 포함한 미래 시각이다. FE는 한국 시간 입력을 `+09:00`으로 보내며, DB는 `TIMESTAMPTZ`로 저장한다. 게시 후에도 원래 예약 시각을 보존한다.
- 수신 대상은 게시 시점에 `active` 또는 `warned`, 온보딩 완료, 미탈퇴인 회원이다. 필수 운영 공지이므로 기존 정책대로 선택적 알림 설정과 무관하게 알림함에 전달한다. 게시 후 가입한 회원에게 과거 공지를 소급 발송하지 않는다.
- `readCount`는 해당 공지 알림을 읽음 처리한 회원 수다. 화면에는 **읽은 회원**으로 표시하며 페이지 조회수나 노출수로 해석하지 않는다.

## 관리자 API

| 메서드·경로 | 동작 |
| --- | --- |
| `GET /api/v1/admin/notices` | 제목 검색·상태·페이지별 목록, 상태별 건수, 현재 작성 권한 |
| `GET /api/v1/admin/notices/{noticeId}` | 본문과 버전 시각, 현재 가능한 작업 |
| `POST /api/v1/admin/notices` | `save_draft`, `publish`, `schedule` 중 한 작업으로 생성 |
| `POST /api/v1/admin/notices/{noticeId}/actions` | 초안 저장·게시·예약 또는 `cancel_schedule` |

목록 쿼리는 `status=all|draft|scheduled|published`, `page`, `pageSize`이다. 제목 검색어는 기존 관리자 계약과 같이 percent-encoded `X-Admin-Search` 헤더에만 전달한다. 상태별 통계에는 검색 조건을 적용하고 상태 필터는 적용하지 않는다. 통계의 증감 수치를 임의로 생성하지 않는다.

목록의 `capabilities`, 상세의 `actions`에 `allowedActions`, `disabledReason`, `scope`를 제공한다. `scope`는 `actorUserId`, `adminAccountId` 문자열로 구성한다. viewer는 조회만, operator/admin은 작성과 게시가 가능하다. 요청마다 실제 세션과 현재 권한을 확인하며, 쓰기에는 CSRF와 `X-Admin-Notice-Actor`, `X-Admin-Notice-Grant`를 요구한다. 이 헤더는 요청을 계정·권한에 묶는 정보이며 권한을 부여하지 않는다.

생성 요청 예시:

```json
{
  "requestId": "7dc8dba7-b481-4217-94f1-928502e8ebda",
  "action": "schedule",
  "title": "서비스 점검 안내",
  "body": "예정된 서비스 점검 내용을 안내합니다.",
  "scheduledAt": "2030-01-01T09:00:00+09:00"
}
```

기존 초안 작업은 위 필드에 `expectedStatus`, `expectedUpdatedAt`을 추가한다. `cancel_schedule` 요청은 `requestId`, `action`, `expectedStatus`, `expectedUpdatedAt`만 전송한다. 예약 외 작업에는 예약 시각을 지정할 수 없다. 버전 시각은 GET의 마이크로초와 offset을 그대로 보존한다.

성공은 HTTP 200이며 `noticeId`, `status`, `scheduledAt`, `publishedAt`, `updatedAt`, `auditLogId`, `replayed`를 반환한다. 409이면 최신 상세를 재조회해 상태를 판단한다. 503·5xx·응답 유실은 실패로 단정하지 않고 같은 UUID·전체 본문·scope로 결과를 재확인한다. 다른 내용에 같은 UUID를 재사용하면 409다. 멱등 기록은 감사 로그에 저장하며 자동 만료 작업은 없다. 원래 권한이 회수되거나 계정이 바뀐 경우 쓰기 재시도를 허용하지 않는다.

## 저장·자동 실행

Migration `b7d4e2a9c601`은 `83a2f9c7d104` 다음에 적용한다.

- `notice`: 제목·본문·상태·예약/게시 시각·최초/최종 관리자·생성/수정 시각.
- `notification.notice_id`: 기존 알림에 nullable FK 추가. 기존 알림 데이터는 유지한다.
- `(user_id, notice_id)` 부분 유일 인덱스: 같은 공지를 같은 회원에게 두 번 전달하지 않는다. 내용이 같아도 다른 공지 ID면 별도 공지다.
- 상태·시각, 본문 길이, 공지 알림 유형 CHECK를 DB에서도 강제한다. 공지 데이터가 있으면 downgrade를 거부한다.

공지 상태 변경, 수신자별 알림 생성, 감사 기록은 한 트랜잭션이다. 본문 전문은 `notice`에 저장하고 알림 행에는 500자 미리보기를 저장한다. 본인 알림함 조회는 게시된 공지를 조인해 전문을 반환하므로 기존 회원 화면에서 전체 내용을 볼 수 있다. 기존 환영 공지·새 글·추천 알림 계약은 유지한다.

예약 실행 명령은 기본적으로 미리보기이며 `--apply`를 지정해야 게시한다.

```bash
python -m app.jobs.publish_scheduled_notices --expected-database me_nu_checking
python -m app.jobs.publish_scheduled_notices --expected-database me_nu_checking --apply
```

`deploy/checking/systemd/me-nu-checking-notices.timer`는 매분 Checking 전용 wrapper를 실행한다. 정상 실행 중에는 예약 시각 이후 다음 실행에서 게시하며, 정지 중 놓친 예약은 재시작 후 처리한다. 공지별 트랜잭션이므로 일부 성공 뒤 장애가 나면 성공한 공지는 유지하고 남은 예약을 다음 실행에서 재시도한다. 공지 쓰기와 worker는 공통 PostgreSQL 트랜잭션 잠금, 행 잠금, 유일 인덱스로 취소·게시 경합과 중복 실행을 제어한다.

자동 게시 감사는 `notice.dispatch`와 `execution=scheduled_job`으로 식별한다. 예약을 승인한 관리자를 참조하며, 사전에 승인한 예약은 해당 관리자의 이후 권한 회수만으로 취소되지 않는다. 취소하려면 게시 전에 예약 취소 작업을 실행해야 한다. 감사 로그에 공지 본문을 복제하지 않는다.

API와 worker의 PostgreSQL 트랜잭션은 잠금 대기를 5초, 개별 쿼리를 30초로 제한한다. 잠금 충돌은 409로 최신 상태 재조회를 유도하고, 결과를 확정할 수 없는 오류는 503으로 같은 요청의 재확인을 유도한다. worker 전체 실행은 180초 이내로 제한한다.

## 통합 적용 순서

1. Checking DB 백업과 현재 revision을 확인한다.
2. `b7d4e2a9c601` Migration을 적용한다. 지연 중인 다른 migration head를 함께 적용하지 않는다.
3. 같은 커밋의 BE와 FE를 배포하고 공지 GET·권한을 확인한다.
4. Checking wrapper 미리보기를 실행한 후 notice service/timer를 설치·활성화한다.
5. 합성 공지로 임시 저장, 즉시 게시, 미래 예약, 예약 취소, 기한 도래 게시, 회원 알림함과 읽은 회원 수·감사 기록을 확인한다.

구현 완료와 서버 적용은 별도 단계다. 이 문서의 명령·unit 파일 존재만으로 Checking Migration이나 timer가 실제 적용됐다고 판단하지 않는다. 적용 결과와 검증 수치는 PR의 검증 기록에 남긴다.

## 구현 검증 기록

- 공지 HTTP·SQLite 계약 32개, 기존 관리자 권한·콘텐츠·회원·신고/문의 및 관련 migration 회귀 170개 통과.
- 신규 migration·Alembic 경로·명시적 migration 정책 37개 통과. ORM DDL snapshot 일치 확인.
- 실제 PostgreSQL 검증 12개 통과: 동일 요청 동시 실행, 운영자 간 게시 경쟁, 감사 실패 롤백, 예약 취소 경쟁, worker 중복 실행·장애 후 재개, 잠금 제한과 동일 요청 재시도.
- 기존 회원 알림 API 및 신규 전달 단위 검증 24개 통과. Checking bootstrap·예약 실행 wrapper 검증 16개 통과.
- FE 공지 계약·쓰기 흐름·기존 공지·회원 알림함 회귀 40개, TypeScript 검사와 production webpack build 통과.
- 과거 `admin-notices-browser-check.cjs`는 API 미연결 시기의 디자인 fixture 전용이며 이번 통합 검증 수치에 포함하지 않는다.

로컬 화면 검증은 `backend/tests/serve_admin_notices_fixture.py`와 `frontend/tests/prepare-admin-notice-api-preview.cjs`로 재현한다. 별도 tmpfs PostgreSQL에 실제 migration·세션·권한·라우터를 사용하고, FE의 운영 공지 route를 그대로 연결한다. 테스트 로그인과 장애 제어 endpoint는 이 loopback fixture에만 존재하며 운영 앱에 등록하지 않는다.

실제 Edge 화면에서 목록·상세, 탭 변경 중 입력 보존, 초안 재편집·저장, 즉시 게시와 응답 유실 후 동일 요청 재확인, 예약 취소 후 초안 복귀를 확인했다. 예약 worker는 첫 실행에 1건·알림 4개를 생성하고 재실행에는 0건을 생성했다. 회원 알림함에서 즉시·예약 공지를 모두 수신하고 전문을 열어 읽음 처리하는 흐름도 확인했다. 모두 합성 회원과 별도 로컬 DB를 사용한 결과이며 Checking 실환경 검증은 아니다.

## 별도 남은 관리자 기능

이 변경으로 관리자 전체 기능이 완료되는 것은 아니다. 콘텐츠 등록·수정 저장, 콘텐츠 상태별 통계·일부 필터, 회원 전용 통계·CSV, 회원 정보/제재·메모 쓰기는 기존 미연결 범위다. 대시보드의 예약 공지 요약도 기존 분석 API가 아직 미지원으로 반환하므로 별도 집계 연결이 필요하다. 대시보드·분석·추천 성능 조회 API 자체는 연결되어 있으며, 추천 평가 자료 미집계는 API 미연결과 구분한다.
