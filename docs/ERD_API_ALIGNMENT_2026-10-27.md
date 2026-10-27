# 현재 구현 기준 ERD·API 정합화

기준일: 2026-10-27 (Asia/Seoul). 사용자 선택에 따라 **현재 구현을 기준으로 ERD·API·개발 문서를 맞춘다.** 첨부 `ERD V1.1.sql`은 비교 자료이며, 여기에만 있는 기능을 이번 작업의 신규 구현 범위로 해석하지 않는다.

**최신 사용자 결정 (2026-10-27): 생년월일을 어떻게 받을지는 통합 완료 후 결정한다.** 아래 생년월일 제거 설명은 현재 로컬 코드와 일부 테스트 DB에서 관찰한 상태이며 최종 수집·저장·연령 확인 정책이 아니다. 이번에는 적용 상태만 재조회하고, 생년월일 관련 코드·프론트·DB를 추가 변경하거나 기존 변경을 되돌리지 않았다.

**2026-10-28 후속 구현:** [온보딩 V2 DB·API](ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md)에 이어 [캐러셀 DB·API](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md)를 통합 브랜치에 구현했다. 현재 ORM은 **38개 테이블·FK 67개·CHECK 54개·UNIQUE 19개·인덱스 45개**이며 통합 작업 공간에서 `current_schema.sql` 생성 결과의 `--check`도 통과했다. 현재 서비스 반영 후보에는 온보딩 e3의 자식 **`f4b8c1d9a620`** head만 있고 생년월일 삭제 d2 파일은 제외했다. 후보 스키마를 재생성·재검증한 결과는 [서비스 흐름 보고서](SERVICE_FLOW_E2E_2026-10-28.md)에서 별도로 관리한다. 아래 31개 테이블·Q1/Q2 비교와 환경별 revision은 **2026-10-27의 과거 조사 기록**이다. 이번 구현을 기존 서버 DB에 적용했다는 의미가 아니다.

## 1. 코드·DB·배포의 확인 수준

| 대상 | 이번 확인 결과 |
| --- | --- |
| 작업 폴더 | `C:\Users\USER\me_nu` |
| 브랜치 / HEAD | `backend-labs` / `407039433efc0be1eb13d574f5ee7f69c5d9eb33` |
| 시작 시 로컬 작업 | 추적 파일 23개 수정, 미추적 파일 5개. 생년월일 제거, 로컬 OAuth runner, 통합 seed와 문서 등이 이미 존재했다. |
| 구현 기준 | HEAD에 기존 미커밋 생년월일 제거 작업을 포함한 현재 `backend/app/models`, 라우터·DTO·서비스 |
| 코드의 Alembic head | `d2f6a9c4e731` 하나. 이전 revision은 `c0e5f3b8a124`. |
| 기본 로컬 DB 재확인 | `me_nu`: `e7a4c2d9b812`. `signup_channel`, nullable DATE `birth_date` 존재. `oauth_attempt`, `onboarding_answers` 없음. |
| 전용 테스트 DB 재확인 | `me_nu_oauth_local`: `d2f6a9c4e731`. `signup_channel`, `birth_date` 없음. `oauth_attempt`, `onboarding_answers` 존재. 로컬 migration head와 일치한다. |
| 조회 방식·한계 | 초기 제한 실행에서 연결 오류가 있었으나, 연결 재활성화 후 제한 밖 로컬 조회에서 성공. `SET TRANSACTION READ ONLY` 뒤 revision·정보 스키마만 SELECT하고 rollback했다. 회원 데이터와 생년월일 값은 조회하지 않았다. |
| 과거 로컬 DB 기록 | [OAuth 안내](OAUTH_T02_1_SETUP.md)의 전용 DB 적용 기록과 이번 revision·컬럼 확인은 일치한다. 과거의 행 데이터 보존 검증은 이번에 재수행하지 않았다. |
| 과거 Labs DB 기록 | [원 인계 문서](ERD_CHANGE_HANDOFF_2026-10-27.md)의 마지막 직접 조회는 `c0e5f3b8a124`. 이번에는 원격 DB·배포 상태를 조회하거나 변경하지 않았다. |

기존 미커밋 변경을 reset하거나 별도 커밋에 포함하지 않았다. 신규 migration, DB 데이터 변경, main 병합, 커밋·푸시·배포, 프론트 소스·환경 설정 변경은 수행하지 않았다. 현재 코드에서 이미 완료된 DB/API 변경은 재작성하지 않고 검증했다.

이번 추가 코드 변경은 스키마 생성 도구, `app.db`의 `get_db` 지연 로딩, 프로필 API의 Swagger 설명 보완이다. `app.db`가 모델만 읽어도 설정·DB 엔진을 초기화하던 의존성을 분리하여 스키마 생성 시 환경 파일을 읽지 않도록 했다. 기존 `from app.db import get_db` 경로와 실제 요청의 세션 생성 방식은 유지한다.

## 2. ERD 산출물과 사용 방법

- 구현 기준 PostgreSQL DDL: [erd/current_schema.sql](erd/current_schema.sql)
- 재생성 도구: [export_current_schema.py](../backend/scripts/export_current_schema.py)
- 입력: 현재 SQLAlchemy 모델의 전체 테이블·컬럼·PK·FK·UNIQUE·CHECK·인덱스와 Alembic head.
- DB 접속이나 환경 파일 없이 생성한다. 실행 중인 DB를 덤프한 결과가 아니며, 현재 모델의 최종 구조를 설명한다.
- 실제 기존 DB의 변경에는 Alembic migration을 사용한다. 이 SQL을 기존 DB에 실행하거나 `stamp`의 근거로 사용하지 않는다. 마이그레이션 적용 순서·데이터 이동·환경별 revision을 대신하지 않는다.
- 제약 이름은 ORM metadata 기준으로, 과거 migration이 만든 물리 이름과 다를 수 있다. Python의 `default`·`onupdate`는 SQL 서버 기본값이나 트리거로 변환하지 않는다.
- 첨부 원본 `C:\Users\USER\Downloads\ERD V1.1.sql`은 수정하지 않았다.

저장소 루트에서:

```powershell
.\backend\.venv\Scripts\python.exe backend/scripts/export_current_schema.py
.\backend\.venv\Scripts\python.exe backend/scripts/export_current_schema.py --check
```

재생성 파일에는 모델에 없는 미래 기능을 넣지 않는다. 모델을 바꾸면 새 migration과 함께 산출물을 갱신하고 `--check`로 일치 여부를 확인한다.

## 3. 첨부 SQL과 구현의 차이

첨부 SQL은 **38개 테이블**, 구현 모델은 **31개**다. 공통 17개, SQL에만 21개, 구현에만 14개다. SQL은 백틱·인라인 `COMMENT`와 PostgreSQL 자료형을 혼용하므로 바로 실행할 PostgreSQL migration이 아니다. FK 18개만 선언되어 있고 UNIQUE·CHECK 선언은 없다. 구현 모델에는 FK 제약 57개가 있다.

| 항목 | 첨부 SQL | 구현 기준으로 반영할 내용 |
| --- | --- | --- |
| 회원 소셜 식별 | `sns_provider`, `sns_user_no` | `auth_provider`, `provider_user_id`와 조합 UNIQUE. 이름을 SQL에 맞춰 변경하지 않는다. |
| 회원 생년월일·유형 | `birth_date` 존재 | 로컬 모델에 `birth_date`, `signup_channel` 없음. `accountType=user`는 API 속성으로 DB 컬럼이 아니다. |
| 가입 완료 | 별도 완료 컬럼 없음 | `onboarding_completed_at`. NULL인 회원도 `user_account`에 존재한다. |
| OAuth·온보딩 | 해당 테이블 없음 | `oauth_attempt`, `onboarding_answers` 추가 반영. |
| 세션·정책 | 해당 테이블 없음 | `auth_session`, `policy`, `notification_setting` 반영. Redis 사용 여부와 SQL 테이블 존재는 별개다. |
| 반응 PK | `(content_id,user_id)` | `content_reaction_id` PK, `(user_id,tap_id,content_id)` UNIQUE. Tap별 반응을 보존한다. |
| Tap 소유권 | 해당 복합 FK 없음 | 반응·저장·채팅에 `(tap_id,user_id) → tap(tap_id,user_id)` 복합 FK. |
| 저장·채팅 Tap | `saved_item.tap_id`, `chat_session.tap_id` NULL 허용 | 구현은 NOT NULL. `saved_tags`도 NOT NULL이며 기본값 `[]`. |
| 콘텐츠 태그 | `(content_id,subtopic_id)` PK | `content_tag_id` PK, 콘텐츠·세부주제 조합 UNIQUE, `topic_id` 포함. |
| 콘텐츠 상태·이미지 | `status=draft`, `thumbnail_url` | `status=active`, `active/hidden/pending_review/deleted` CHECK, `image_url`. API의 이미지 명칭과 DB 컬럼명을 구분한다. |
| 콘텐츠 확장 | `event_detail_id`, `is_changed_notice`, `valid_until` | 현재 모델에는 없다. 구현된 초안·Agent·클러스터·심사·삭제 필드를 반영한다. |
| 임베딩 | `VECTOR(1024)`, `tap.taste_vector` | 콘텐츠는 차원 제약 없는 `VECTOR`; `tap.taste_vector`는 미구현이다. |
| 콘텐츠 출처 | `(source_url,content_id)` PK | `content_source_id` PK와 두 컬럼 조합 UNIQUE, Agent 출처 및 역할·인용 순서. |
| 알림 | `receiver_user_id`, `message_body`, `sent_at`, `is_read` | `user_id`, `body`, `created_at`, `read_at`; 공개 API는 `system_notice`만 제공한다. |
| 동의 | 철회·재확인·통지 필드 | 구현의 `revoked_at`, `created_at`, nullable `agreed_at` 사용. `policy_id` FK 없음. |
| Topic·Subtopic | `topic.is_active` NOT NULL 기본 TRUE, `subtopic.sort_order` | 현재 `is_active` nullable/기본값 없음; Subtopic은 부모·깊이·콘텐츠 수 필드. 공개 API는 평면 목록이다. |
| 판정 로그 | `stage`, `token_cost`, `verdict` | `stage_name`, `agent_run_id`, `draft_id` 등 현재 모델 기준. `content_id`는 선택값이다. |

**첨부에만 있는 21개 테이블 — 이번 구현 ERD에 추가하지 않음:**

`ad`, `ad_impression`, `admin_account`, `admin_action_log`, `content_entity_link`, `debate`, `debate_comment`, `debate_comment_like`, `debate_option`, `debate_upvote`, `debate_vote`, `entity`, `entity_relation`, `event_detail`, `raw_document`, `recommendation_item`, `recommendation_round`, `review_case`, `sanction`, `tap_entity`, `user_event`.

**구현에만 있는 14개 테이블 — 구현 ERD에 포함:**

`agent_run`, `agent_run_source`, `agent_run_step`, `auth_session`, `chat_fact_cache`, `chat_unanswered_question`, `creator_channel`, `draft`, `notification_setting`, `oauth_attempt`, `onboarding_answers`, `policy`, `topic_cluster`, `user_operation`.

[콘텐츠 이벤트 테이블 설계](CONTENT_EVENT_TABLE_DESIGN.md)의 이벤트·`content_action_request`도 제안 상태다. 첨부의 단일 `user_event`와 같은 설계로 간주하지 않는다.

## 4. 핵심 관계와 논리 참조

| 관계 | 물리 제약·의미 |
| --- | --- |
| User → Tap | 회원 1 : Tap 0..N. 삭제되지 않은 `(user_id,topic_id)` UNIQUE. |
| Tap → 온보딩 답변 | 1 : 0..1. `tap_id` PK+FK, 자동 증가 없음, CASCADE. |
| Tap / Subtopic → TapTopic | 별도 구독 연결. 온보딩 Q1/Q2 완료가 이 행을 자동 생성하지 않는다. |
| Tap / User / Content → 저장·반응 | Tap 소유자 복합 FK와 회원·콘텐츠 FK를 보존한다. |
| OAuth attempt | 다른 테이블을 참조하는 FK 없음. 브라우저 세션 해시는 FK가 아니다. |
| Policy ↔ ConsentHistory | `consent_type`·`policy_version`으로 서비스가 검증하는 논리 참조. 물리 FK가 아니다. |
| Redis 세션 → User | Redis JSON의 `user_id`는 논리 참조. PostgreSQL `auth_session`의 FK와 구분한다. |

세션 저장소를 Redis로 선택하면 같은 세션을 SQL에 이중 저장하지 않는다. `oauth_attempt`는 PostgreSQL에 유지한다. Redis 키 삭제는 회원·동의·답변·북마크 삭제가 아니다.

## 5. 현재 API 계약

로컬 앱의 OpenAPI를 DB·Redis 연결 없이 생성하여 **공개 V1 33개 + 상태 확인 3개**, 개발 API 활성화 시 **6개 추가 / 총 42개**를 확인했다. 세부 목록은 [API V1.1 로컬 정합화](API_V1_1_LOCAL_ALIGNMENT.md)를 따른다. 라우트 등록 확인은 실제 공급자 인증·LLM 답변·배포 성공 확인과 다르다.

| API | 현재 계약 |
| --- | --- |
| `POST /api/v1/onboarding/complete` | 필수 `nickname`, `accountType:"user"`, `consents`, `topicId`, `answers:{q1,q2}`. pending 세션·CSRF 필요. |
| 온보딩 제거 필드 | `birthDate`, `profileImageId`, `subtopicIds`는 NULL도 포함해 422. 대체 연령 확인 필드 없음. |
| 온보딩 저장 | 회원 프로필·완료 시각, 동의, Tap, Q1/Q2, 기본 알림 설정을 저장. 응답 `activeTopic.subtopicIds=[]`. |
| `GET /api/v1/users/me` | `birthDate` 없는 본인 프로필. `accountType=user`. |
| `PATCH /api/v1/users/me` | 닉네임 변경 및 `profileImageId:null` 연결 해제. 양수 이미지 ID는 업로드 미지원으로 404. `birthDate` 등 추가 필드는 422. 응답에도 생년월일 없음. |

Q1: `world`, `relationships`, `ost`, `goods`, `events`, `news`. Q2: `releases`, `characters`, `restock`, `events`. 각각 한 문자열이며 Q2는 알림·마케팅 동의가 아니다.

## 6. 기존 생년월일 제거 작업과 보류 범위

기존 로컬 migration 체인은 다음과 같다. 이번에 새 revision을 추가하지 않았다.

```text
e7a4c2d9b812 → a8c3d1f6e902 → b9d4e2a7f013 → c0e5f3b8a124 → d2f6a9c4e731
                유형 제거       OAuth attempt    온보딩 답변      생년월일 제거
```

`d2f6a9c4e731`은 `user_account.birth_date`만 삭제한다. downgrade는 nullable DATE 컬럼만 복구하고 이전 값을 복원하지 못한다. 새 모델은 기존 nullable 컬럼이 남아 있는 DB에서도 동작하지만 구 API는 컬럼 삭제 후 조회에 실패할 수 있다.

읽기 전용 재확인 결과:

| DB | 적용 revision | `signup_channel` | `birth_date` | `oauth_attempt` / `onboarding_answers` |
| --- | --- | --- | --- | --- |
| `me_nu` | `e7a4c2d9b812` | 있음 | 있음 (nullable DATE) | 모두 없음 |
| `me_nu_oauth_local` | `d2f6a9c4e731` | 없음 | 없음 | 모두 있음 |

기본 DB는 코드 head보다 네 revision 이전 상태이며, 전용 DB는 head와 일치한다. 따라서 기본 DB를 현재 OAuth·온보딩 스키마가 적용된 통합 DB로 간주하지 않는다. 이 차이는 상태 확인 결과이며 기본 DB를 지금 `upgrade head`하라는 지시가 아니다.

1. 통합에 쓰는 환경의 실행 코드·DB 조합을 기준으로 검증하고, 로컬 미커밋 코드와 배포 코드의 계약을 섞지 않는다.
2. 생년월일 수집 방식·필수 여부·저장·연령 확인 정책은 통합 완료 후 사용자와 결정한다. 기존 Pivot의 `birthDate` 전송과 로컬 백엔드의 422는 현재 계약 차이로 기록한다. 이를 이유로 프론트 생년월일 제거 또는 `d2f6a9c4e731`의 다른 DB 적용을 지금 진행하지 않는다.
3. 결정 후 필요한 API·프론트·DB 변경과 적용 순서를 정한다. 이미 전용 DB에서 삭제된 값을 되살리거나 컬럼을 추가하는 작업도 이번에 수행하지 않았다.
4. 실제 브라우저의 통합 검증과 자동 테스트 결과를 구분한다. 이번 재확인은 DB 스키마 조회이며 신규 사용자 가입 검증이 아니다.

## 7. 이번 검증

- 생년월일 제거와 관련된 회귀 테스트: **262 passed**, Starlette/AnyIO 사용 중단 예정 경고 1개. DB·공급자 대역 및 메모리 SQLite 기반이다.
- `app.db` 지연 로딩 변경 후 모델·Alembic 테스트 3개 파일을 재실행해 **18 passed**. 앱 import와 기존 `get_db` export 호환성을 확인했다. 위 262개와 일부 중복되는 재검증이다.
- 현재 Alembic 단일 head와 upgrade/downgrade SQL, 제거 전 nullable 컬럼이 남은 상태와 제거된 상태의 호환성, 온보딩 트랜잭션·답변·동의 보존, 추가 필드 거절, 프로필 응답, 재로그인, Redis 경로, 로컬 OAuth runner를 확인했다.
- 후속 요청에서 로컬 PostgreSQL 두 DB의 revision·테이블·회원 컬럼 조회에 성공했다. 연결 실패는 최초 시도 기록으로 남기고 최신 적용 상태는 위 표를 따른다. 실제 API의 DB 쓰기 동작·행 보존 재검증은 수행하지 않았다. 원격 DB, 실제 OAuth 공급자, 프론트 브라우저, LLM 호출도 이번 재확인 범위 밖이다.
- `export_current_schema.py --check` 통과. 31개 테이블, 57개 FK 및 모델의 모든 제약·인덱스 이름이 SQL에 포함되는지 확인했다. 회원의 제거 컬럼 부재와 온보딩 답변 PK의 자동 증가 부재도 확인했다.
- 서로 다른 실행 디렉터리 3곳·Python 해시 seed 3종에서도 스냅샷 일치. 누락 파일의 `--check`는 파일을 쓰지 않고 실패한다. 반복 생성은 ORM metadata를 바꾸지 않으며 설정·세션 모듈을 불러오지 않는다.
- 최신 API 목록의 메서드·경로 33개가 OpenAPI와 정확히 일치하며, 온보딩·프로필 schema의 `birthDate` 부재와 GET/PATCH Swagger 설명을 확인했다. `git diff --check` 통과.

회귀 테스트 대상은 다음 명령과 같다. 실행 시 DB URL은 `127.0.0.1:1`의 테스트용 주소로 강제하고 Redis·실제 OAuth를 비활성화했다. Windows 제한 실행 환경에서 asyncio 내부 loopback 생성이 정지하여 자동 승인 검토를 거친 제한 밖 실행으로 확인했다. 실제 서비스용 DB로 테스트한 결과가 아니다.

```powershell
# backend 디렉터리
.\.venv\Scripts\python.exe -m pytest tests/test_alembic.py tests/test_auth_api.py tests/test_oauth_accounts.py tests/test_oauth_postgres_flow.py tests/test_onboarding_answers.py tests/test_redis_auth_api.py tests/test_user_account_contract.py tests/test_user_model.py tests/test_local_oauth_runner.py -q -o faulthandler_timeout=30
```
