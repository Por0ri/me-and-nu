# ERD 변경 사항 및 후속 대화 인계

작성일: 2026-10-27 (Asia/Seoul)

**후속 재확인 (2026-10-27):** 사용자는 현재 구현을 기준으로 ERD·API·문서를 정합화하기로 선택했다. 최신 비교·검증은 [현재 구현 기준 ERD·API 정합화](ERD_API_ALIGNMENT_2026-10-27.md), 전체 구현 DDL은 [current_schema.sql](erd/current_schema.sql)을 따른다. 아래의 확정 배포·로컬 미확인 설명은 이 인계 문서 최초 작성 시점의 기록이다. 생년월일 제거 코드는 후속 회귀 테스트 262개를 통과했다. 초기 접속 실패 후 로컬 DB를 읽기 전용 재조회하여 `me_nu=e7a4c2d9b812`(생년월일 컬럼 있음), `me_nu_oauth_local=d2f6a9c4e731`(생년월일 컬럼 없음)을 확인했다. 원격 Labs revision은 이번에 재조회하지 않았다.

**최신 사용자 결정:** 생년월일을 어떻게 받을지는 통합 완료 후 결정한다. 과거 코드·전용 DB의 제거 상태를 최종 정책으로 해석하지 않는다. 2026-10-28 새 Pivot 가입 화면은 생년월일 입력·전송을 보류했고, 서비스 반영 후보에는 f4 단일 head만 포함하며 d2 물리 삭제 파일을 제외했다. 원본 작업 공간의 파일·과거 DB 이력은 보존한다. 최신 실제 Kakao·가입·서비스 검증과 후보 상태는 [서비스 흐름 보고서](SERVICE_FLOW_E2E_2026-10-28.md)를 따른다.

**2026-10-28 온보딩 구현:** 작품 1~5개 → 작품 기반 장르·소재 1개 이상 → 기존 Q1 옵션 중 소비 유형 1개의 DB·API를 `codex/service-integration` 작업 공간에 구현했다. [온보딩 V2 구현 명세](ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md)를 먼저 읽는다. 새 모델 6개와 버전별 답변 저장, 검색·후보 API, v1 호환 완료 API가 추가됐다. target `e3a7b9c2d451`은 c0에서 분기해 생년월일 삭제를 포함하지 않는다. 생년월일 컬럼 유무가 다른 DB 경로는 격리 PostgreSQL에서 검증하며 기존 서비스 DB 적용은 별도다. 아래 본문은 최초 작성 시점의 기록이다.

## 1. 이 문서의 목적과 범위

현재까지 구현한 로그인·온보딩·콘텐츠 저장 기능의 ERD 변경을 다른 대화에서 이어서 검토할 수 있도록 정리한다. **커밋과 Labs DB에서 확인한 변경**과 **원본 작업 폴더의 미커밋 변경**을 구분한다.

- 저장소: `Por0ri/me-and-nu`
- 문서 위치: `C:\Users\USER\me_nu\docs\ERD_CHANGE_HANDOFF_2026-10-27.md`
- 원본 작업 폴더: `C:\Users\USER\me_nu` — 문서 작성 시 브랜치 `backend-labs`
- 기존 통합 작업 폴더: `C:\Users\USER\.codex\worktrees\fe-be-integration\me_nu` — 브랜치 `codex/fe-be-integration`
- 사용자 제약: **main 병합·배포를 진행하지 않는다. 프론트 담당자의 브랜치·배포·환경 설정을 유지한다. 기존 미커밋 작업을 덮어쓰거나 함께 커밋하지 않는다.**
- 이 문서 작성 작업은 문서 추가만 수행한다. DB 마이그레이션, 코드 변경, 커밋, 푸시, 배포를 포함하지 않는다.

## 2. 비교 기준과 확인 수준

| 항목 | 확인한 기준 |
| --- | --- |
| 비교 대상 main 커밋 | `9f1439919328541fd16000082dd2da7234e73939` |
| backend-labs 및 통합 작업 폴더 HEAD | `407039433efc0be1eb13d574f5ee7f69c5d9eb33` |
| 직전 대화에서 직접 조회한 Labs DB revision | `c0e5f3b8a124` |
| 비교 방법 | Git 모델·Alembic 변경 비교, 모델·서비스 검토, Labs `alembic_version` 및 `information_schema.columns` 읽기 |
| 최근 화면 연동·분리 배포의 스키마 변경 | `b74290a..4070394` 구간의 모델·마이그레이션 변경 없음 |

**확정 기준에서 관계형 ERD 변경은 신규 테이블 2개와 기존 컬럼 1개 삭제다.** 이 변경은 `810d21e`의 OAuth·온보딩 구현에 포함됐다. 이후 프론트 연결, Labs 환경 분리, Redis Insight 연결은 추가 DDL 변경이 아니다.

이 문서의 DB 상태는 직전 대화에서 직접 조회한 시점의 결과다. 별도 대화의 작업이나 배포가 계속될 수 있으므로, 후속 작업에서는 Git 상태와 환경별 DB revision을 다시 확인한다. 특히 8절의 미커밋 마이그레이션을 이미 배포된 변경으로 취급하지 않는다.

## 3. 확정 ERD 변경

### 3.1 `user_account.signup_channel` 삭제

- 기존 `signup_channel VARCHAR(20)` 컬럼을 삭제했다.
- `consumer/creator` 값 제한인 `ck_user_account_signup_channel` CHECK 제약도 삭제했다.
- `user_id`와 기존 PK/FK 관계는 유지된다.
- API의 `accountType: "user"` 및 호환 속성 `role`은 Python property다. **이를 대체 DB 컬럼이나 신규 회원 유형 테이블로 그리지 않는다.**
- `creator_channel` 테이블을 삭제한 변경도 아니다.

근거:

- [컬럼 삭제 마이그레이션](../backend/alembic/versions/a8c3d1f6e902_remove_signup_channel.py)
- [회원 모델](../backend/app/models/user.py)

### 3.2 `oauth_attempt` 추가

카카오·네이버 OAuth 요청을 브라우저에 연결하고, 만료 및 일회성 사용을 관리한다. 원본 OAuth state, 공급자 토큰, Client Secret을 저장하는 테이블이 아니다.

| 컬럼 | 타입 | NULL | 키·의미 |
| --- | --- | --- | --- |
| `state_hash` | `VARCHAR(64)` | 불가 | PK, OAuth state 해시 |
| `browser_session_hash` | `VARCHAR(64)` | 불가 | 요청을 시작한 브라우저 세션의 해시 |
| `provider` | `VARCHAR(20)` | 불가 | `kakao` 또는 `naver` |
| `created_at` | `TIMESTAMPTZ` | 불가 | 생성 시각, 기본값 `now()` |
| `expires_at` | `TIMESTAMPTZ` | 불가 | 만료 시각 |
| `consumed_at` | `TIMESTAMPTZ` | 허용 | 사용 완료 시각 |

제약·인덱스:

- `provider IN ('kakao', 'naver')`
- `expires_at > created_at`
- `idx_oauth_attempt_browser_created(browser_session_hash, created_at)`
- `idx_oauth_attempt_expires_at(expires_at)`
- **회원·`auth_session`·다른 테이블을 참조하는 물리 FK는 없다.** `browser_session_hash`를 FK로 표시하지 않는다.

근거:

- [모델](../backend/app/models/oauth_attempt.py)
- [추가 마이그레이션](../backend/alembic/versions/b9d4e2a7f013_add_oauth_attempt.py)

### 3.3 `onboarding_answers` 추가

사용자가 온보딩에서 고른 Q1·Q2 답변을 관심 분야 Tap에 연결해 저장한다.

| 컬럼 | 타입 | NULL | 키·의미 |
| --- | --- | --- | --- |
| `tap_id` | `BIGINT` | 불가 | PK + FK → `tap.tap_id`, 자동 증가 아님 |
| `q1_choice` | `VARCHAR(32)` | 불가 | Q1 단일 선택값 |
| `q2_choice` | `VARCHAR(32)` | 불가 | Q2 단일 선택값 |
| `created_at` | `TIMESTAMPTZ` | 불가 | 생성 시각, 기본값 `now()` |

CHECK 허용값:

- Q1: `world`, `relationships`, `ost`, `goods`, `events`, `news`
- Q2: `releases`, `characters`, `restock`, `events`

관계와 의미:

- **Tap 1개 : 답변 0~1개**다. 모든 기존 Tap에 답변이 반드시 존재하는 구조가 아니다.
- `tap_id` FK는 `ON DELETE CASCADE`다. Tap을 물리 삭제하면 연결된 답변도 삭제된다.
- 회원 및 Topic은 `tap.user_id`, `tap.topic_id`를 통해 결정된다. 답변 테이블에 회원·Topic FK를 추가하지 않는다.
- Q1·Q2는 `topic_id`나 `subtopic_id`가 아닌 고정 선택값이다.
- 기존 `tap_topic`은 별도의 세부 주제 구독 연결 테이블로 유지된다.
- 확인한 온보딩 완료 구현은 Tap과 답변을 만들며 `TapTopic`은 생성하지 않는다. 응답의 `subtopicIds`는 빈 배열이다.

근거:

- [모델](../backend/app/models/onboarding_answers.py)
- [추가 마이그레이션](../backend/alembic/versions/c0e5f3b8a124_add_onboarding_answers.py)
- [온보딩 완료 서비스](../backend/app/services/onboarding_service.py)
- [온보딩 요청 계약](../backend/app/schemas/onboarding.py)

## 4. ERD 관계 표시 기준

아래는 변경 사항을 기존 모델에 연결할 때 필요한 주요 관계다. 전체 ERD의 모든 테이블을 열거한 것은 아니다.

| 부모 → 자식 | 관계 | 주요 제약 |
| --- | --- | --- |
| `user_account → tap` | 1 : 0..N | 회원 FK, CASCADE |
| `topic → tap` | 1 : 0..N | Topic FK, RESTRICT |
| `tap → onboarding_answers` | 1 : 0..1 | `tap_id` PK+FK, CASCADE |
| `tap → tap_topic` | 1 : 0..N | Tap FK, CASCADE |
| `subtopic → tap_topic` | 1 : 0..N | Subtopic FK, RESTRICT |
| `user_account → consent_history` | 1 : 0..N | 회원 FK, CASCADE |
| `user_account → notification_setting` | 1 : 0..1 | `user_id` PK+FK, CASCADE |
| `user_account → auth_session` | 1 : 0..N | SQL 저장 경로의 FK, CASCADE. 익명 세션은 회원 FK 값 NULL 허용 |
| `user_account / content / tap → saved_item` | 각 부모 기준 1 : 0..N | 회원·콘텐츠 FK 및 `(tap_id, user_id) → tap(tap_id, user_id)` 복합 FK |

추가 제약:

- Tap에는 삭제되지 않은 행에 한해 `(user_id, topic_id)` UNIQUE 인덱스가 있다.
- `saved_item`에는 `(user_id, content_id, tap_id)` UNIQUE가 있다.
- `content_reaction`에도 회원·Tap·콘텐츠 조합 UNIQUE와 Tap 소유자를 검증하는 복합 FK가 이미 존재한다.
- `policy`는 `(policy_type, policy_version)` UNIQUE다.
- **`consent_history`에는 `policy_id` FK가 없다.** `consent_type`과 `policy_version` 값으로 동의 이력을 남기고 서비스에서 정책을 검증한다. 관계를 표시한다면 물리 FK와 구분한 논리적 참조로 표시한다.

근거: [기존 v1 모델](../backend/app/models/v1.py), [정책 검증 서비스](../backend/app/services/policy_service.py).

## 5. Redis 세션은 관계형 ERD와 구분

현재 Labs는 `SESSION_BACKEND=redis`다. PostgreSQL `auth_session` 테이블이 삭제된 것이 아니라, 세션 저장·조회 경로를 Redis로 선택한 구성이다. Redis 사용 시 같은 세션을 PostgreSQL에도 이중 저장하는 구조가 아니다.

| 항목 | Labs 구성 |
| --- | --- |
| 키 | `me_nu_labs:auth-session:v1:<session_token_hash>` |
| 저장 형태 | Redis String에 JSON 저장 |
| JSON 필드 | `session_token_hash`, `csrf_token_hash`, `user_id`, `session_state`, `created_at`, `expires_at` |
| 상태 | `anonymous`, `onboarding_pending`, `active` |
| 수명 | 설정상 30분, 절대 만료 시각에 맞춘 TTL |
| 폐기 | 해당 Redis 키 삭제 |

- Redis JSON의 `user_id`는 **논리적 참조**이며 DB FK가 아니다.
- Redis 값에는 SQL 모델의 `auth_session_id`, `revoked_at`, `last_accessed_at`이 없다.
- `oauth_attempt`는 Redis 세션 설정과 관계없이 PostgreSQL에 저장된다.
- 로그아웃 후 CSRF 준비를 위한 익명 세션이 새로 생길 수 있다. 키의 존재만으로 로그인 상태를 판단하지 않는다.
- Redis 세션 삭제는 회원·동의 이력·온보딩 답변·북마크를 삭제하지 않는다.
- Redis Insight 연결은 관리 도구 설정이며 테이블·컬럼·FK 변경이 아니다.
- Labs의 별도 DB/Redis/쿠키 구성은 배포 환경 분리다. ERD에 `labs_user_account` 같은 새 테이블을 추가하는 변경이 아니다.

근거:

- [Labs Compose](../compose.labs.yaml)
- [Redis 세션 모델·저장·폐기](../backend/app/repositories/redis_session_store.py)
- [SQL/Redis 저장소 분기](../backend/app/repositories/auth_session_repository.py)

## 6. 화면 연동에서 재사용한 기존 구조

다음은 새로 만들어진 테이블이나 컬럼으로 표시하지 않는다.

| 기능 | 기존 구조 |
| --- | --- |
| 소셜 회원 식별 | `user_account.auth_provider`, `provider_user_id` 및 두 컬럼의 UNIQUE |
| 프로필 | `nickname`, `birth_date` 등 기존 회원 컬럼 — 생년월일 후속 작업은 8절 참조 |
| 가입 완료 여부 | `user_account.onboarding_completed_at` |
| 약관 및 동의 기록 | `policy`, `consent_history` |
| 관심 분야 | `topic`, `tap`, `tap_topic` |
| 홈·상세 콘텐츠 | `content`, `content_tag` 및 기존 콘텐츠 관련 테이블 |
| 북마크·Saved | `saved_item` |
| 반응 | `content_reaction` |

온보딩 대기 회원도 `user_account`의 회원이다. 별도 pending-user 테이블 없이 `onboarding_completed_at=NULL`과 세션의 `onboarding_pending`으로 구분한다.

확정 커밋 기준 온보딩 완료 시 회원 프로필과 완료 시각을 갱신하고, 동의 이력·Tap·Q1/Q2 답변·알림 설정을 저장한다. 기존 가입 계정은 세션을 폐기해도 이 정보가 유지되므로 재로그인 시 온보딩이 반복되지 않는다.

## 7. 마이그레이션 기준

확정 커밋의 후속 마이그레이션 체인:

```text
e7a4c2d9b812  기존 notification 추가
  → a8c3d1f6e902  signup_channel 삭제
  → b9d4e2a7f013  oauth_attempt 추가
  → c0e5f3b8a124  onboarding_answers 추가
```

직전 대화에서 Labs DB의 `alembic_version`을 읽어 `c0e5f3b8a124`를 확인했고, 정보 스키마에서도 신규 두 테이블 및 `signup_channel` 부재를 확인했다. 당시 `user_account.birth_date`와 PostgreSQL `auth_session`은 존재했다.

이 문서는 추가 migration 실행을 지시하지 않는다. 기존 마이그레이션을 중복 생성하지 않는다.

## 8. 별도 로컬 미커밋 작업: 생년월일 저장 제거

문서 작성 중 원본 `C:\Users\USER\me_nu`에서 아래 변경을 발견했다. **위의 확정 변경 3건과 분리해서 다뤄야 한다.**

- 원본 브랜치와 HEAD는 `backend-labs` / `4070394`였지만 여러 파일에 미커밋 변경이 있었다.
- `backend/app/models/user.py`에서 `birth_date` 매핑을 삭제하는 변경이 있었다.
- 온보딩 요청·프로필 응답에서 생년월일 필드를 제거하는 변경이 있었다.
- 온보딩 서비스의 나이 검증·생년월일 저장을 제거하는 변경도 있었다.
- 신규 **미추적** 파일: `backend/alembic/versions/d2f6a9c4e731_remove_user_birth_date.py`
- 해당 revision은 `c0e5f3b8a124` 다음이며, upgrade는 `user_account.birth_date` 컬럼을 삭제한다.
- downgrade는 nullable DATE 컬럼만 복원한다. 삭제된 생년월일 값은 복원하지 못한다.
- migration 주석은 생년월일 매핑이 없는 코드로 먼저 전환하거나, 기존 API 인스턴스를 중지한 상태에서 적용하도록 명시한다.

**이 생년월일 제거 작업의 완료·테스트·커밋·원격 DB 적용은 이 문서에서 확인한 사실이 아니다.** 기존 통합 작업 폴더와 원본 작업 폴더의 내용이 다를 수 있다. 다음 대화는 먼저 원본의 변경 상태와 해당 작업의 진행 맥락을 확인해야 한다.

확인한 로컬 작업 파일:

- 과거 생년월일 제거 migration `d2f6a9c4e731_remove_user_birth_date.py` — 원본·통합 작업 공간의 이전 자료이며 현재 서비스 후보에서는 제외
- [회원 모델](../backend/app/models/user.py)
- [온보딩 DTO](../backend/app/schemas/onboarding.py)
- [프로필 DTO](../backend/app/schemas/profile.py)
- [온보딩 서비스](../backend/app/services/onboarding_service.py)

위 상대 링크는 원본 작업 폴더의 현재 파일을 가리킨다. 확정 배포 기준을 확인할 때는 `4070394` 시점 파일과 비교한다. 다른 대화나 체크아웃에 아직 미커밋 파일이 없다면 이 링크의 대상이 없을 수 있다.

## 9. 후속 대화에서 확인할 사항

1. `git branch --show-current`, `git status --short`, `git rev-parse HEAD`로 작업 위치·기준을 확인한다.
2. 이 문서의 확정 기준과 이후 커밋·미커밋 작업을 구분한다. 원본의 진행 중 작업을 reset·덮어쓰기·무단 병합하지 않는다.
3. 사용자가 수정하려는 ERD 원본 위치와 기준 버전을 확인한다. 현재 로컬 조사에서는 독립 ERD/DBML/drawio 원본 파일을 찾지 못했다.
4. ERD를 갱신한다면 확정 변경 3건과 FK/논리 참조 구분을 반영한다. 생년월일 제거는 후속 작업의 실제 상태를 확인한 뒤 반영 여부를 결정한다.
5. [`CONTENT_EVENT_TABLE_DESIGN.md`](CONTENT_EVENT_TABLE_DESIGN.md)의 콘텐츠 이벤트 테이블 및 `content_action_request`는 설계 제안이다. 확인한 모델·마이그레이션에는 없으므로 구현 완료 ERD에 섞지 않는다.
6. 필요할 때만 환경별 실제 DB revision을 읽기 전용으로 확인한다. 옛 문서의 "DB 미적용" 기록을 현재 상태로 그대로 인용하지 않는다.
7. 사용자 요청 없이 main 병합·배포 또는 다른 담당자의 환경 변경을 진행하지 않는다.

다음 대화에 전달할 문장 예시:

> `docs/ERD_CHANGE_HANDOFF_2026-10-27.md`를 읽고 ERD 후속 작업을 이어가줘. 먼저 배포 확인 기준과 로컬 미커밋 생년월일 제거 작업을 구분해서 확인해줘. main과 프론트 담당자 환경은 그대로 두고, 기존 미커밋 변경을 덮어쓰지 마.

## 10. 참고 문서

- [Labs 분리 배포 기록](BACKEND_LABS_ENVIRONMENT_2026-10-27.md)
- [OAuth 설정·검증 기록](OAUTH_T02_1_SETUP.md)
- [API V1.1 연동 기록](API_V1_1_LOCAL_ALIGNMENT.md)
- [Labs 운영 절차](../deploy/labs/README.md)

과거 문서에는 작성 당시 검증 범위나 DB 적용 상태가 남아 있다. 후속 화면·실제 로그인 테스트와 현재 구현 상태는 날짜 및 커밋을 함께 대조한다. 이 인계 문서에는 비밀번호, SSH 개인키, OAuth Secret, 원본 세션 토큰을 포함하지 않는다.
