# 검수된 캐러셀의 서비스 DB·API 연결

기준: 2026-10-28, 구현·검증은 `codex/service-integration`에서 수행했다. 현재 `codex/backend-labs-service` 후보에는 서비스 변경만 선별했고 에이전트 코드·생년월일 삭제 d2 파일을 포함하지 않았다. 아래 migration 안내는 이 후보 기준이며 과거 통합 작업 공간의 검증 결과와 후보 재검증은 [서비스 흐름 보고서](SERVICE_FLOW_E2E_2026-10-28.md)에서 구분한다. 실제 사용자 DB migration·콘텐츠 적재, main 병합, 원격 푸시와 배포는 수행하지 않는다.

## 1. 범위와 기존 작업의 관계

이 문서의 DB·API 단계는 에이전트가 생성한 캐러셀을 서비스 DB에 보관하고 기존 피드·상세 API로 조회하기 위한 최소 확장이다. 이 단계에서는 에이전트의 생성·렌더·검수 코드와 프론트 화면을 수정하지 않았다. 서비스는 원본 이미지 URL을 다시 다운로드하거나 LLM을 호출하지 않고 전달된 PNG와 구조화된 텍스트를 저장한다. 이후 진행하는 홈·상세 화면 연결은 [프론트 캐러셀 연결 기록](FRONTEND_CAROUSEL_INTEGRATION_2026-10-28.md)에서 구현 상태와 검증 결과를 따로 관리한다.

생년월일의 수집·저장·연령 확인 정책은 계속 보류한다. [온보딩 V2](ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md)의 작품 1~5개 → 장르·소재 1개 이상 → 소비 유형 1개 계약도 유지한다.

## 2. DB 변경과 명시적 migration 대상

기존 `content`, `agent_run`, `draft`, `content_source`를 재사용한다. 새 테이블은 `content_slide` 하나이며, 기존 `content`에는 nullable `feed_valid_until`만 추가한다. 바이너리는 공개 피드 JSON에 포함하지 않고 보호된 이미지 조회 경로로 제공한다.

현재 ORM 전체는 **38개 테이블, FK 67개, CHECK 54개, UNIQUE 19개, 인덱스 45개**다. 이는 코드의 스키마 통계이며 실제 사용자 DB의 적용 상태가 아니다.

| 저장 위치 | 컬럼·의미 |
| --- | --- |
| `content_slide` PK | `(content_id,page)`. `content_id`는 `content` FK이며 콘텐츠 삭제 시 cascade. DB의 page 범위는 1~10이고 importer·공개 조회는 3~10장의 1부터 연속된 완성본을 요구한다. |
| 카드 텍스트·배치 | `role`, `eyebrow`, `title_lines` JSONB, `body_sentences` JSONB, `layout_json` JSONB. PNG 위의 HTML 텍스트는 이 구조화된 값을 사용한다. |
| 카드 이미지 | `image_data` BYTEA, `image_sha256`, `image_width=1308`, `image_height=1632`. 1바이트 이상·20MiB 이하 제약. ORM의 바이너리는 지연 로딩하여 피드·상세 메타데이터 조회에서 제외한다. |
| `content.feed_valid_until` | timezone 포함 만료 시각. 기존 콘텐츠는 NULL 허용, 새 캐러셀은 미래 시각 필수. 만료되면 공개 조회에서 제외하며 이 작업에서 원본·회원 데이터를 삭제하지 않는다. |
| 기존 실행·초안 | `AgentRun`에 원본 패키지·원본 검수 자료·digest를 보존하고 `Draft`에 상세 본문을 저장한다. `menu_work_ids`는 원본과 초안 구조 정보에 보존하며 온보딩 `entity_id`로 추정 변환하지 않는다. |
| 기존 공개 글·출처 | `Content`에 상세 본문·상태·검토 기록을 저장하고 `ContentSource`에 URL·제목·인용 순서를 저장한다. `primary`·`citation` 역할은 보존하고 `material/secondary/reference/editorial/discovery/trend`는 서비스의 `material`로 매핑한다. primary를 먼저 두고 나머지 원본 순서를 보존한다. 원래 역할과 publisher 등 원본 필드는 실행 패키지에 보존한다. |

새 revision은 `f4b8c1d9a620`, 부모는 온보딩 V2의 `e3a7b9c2d451`이다.

```text
c0e5f3b8a124
└─ e3a7b9c2d451  온보딩 V2
   └─ f4b8c1d9a620  캐러셀 DB/API
      └─ 150a601074af  추천 연결 표 4개 — 지금의 유일한 head와 명시적 target
```

대상 환경과 변경 승인을 별도로 확인한 뒤 적용할 명령은 다음과 같다. 이 문서 갱신 과정에서는 실행하지 않는다.

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade 150a601074af
```

이 후보는 f4 단일 head이며 생년월일 삭제 d2 파일을 제외했다. e3는 온보딩의 도입 revision이고 f4는 e3를 포함한다. 현재 대상 revision을 고정하기 위해 `upgrade head/heads` 대신 f4를 명시한다. 기존 생년월일 컬럼과 값은 보존하며, ORM에 없는 열이 autogenerate에서 삭제되지 않도록 보호한다. 과거 통합 작업 공간에는 d2 분기가 있었지만, 이미 d2가 적용된 DB를 이 후보만으로 자동 업그레이드할 수 있다고 해석하지 않는다. 해당 이력은 별도 검토하며 임의로 stamp·컬럼 재생성·값 추정을 하지 않는다. 최신 ORM의 DDL은 [current_schema.sql](erd/current_schema.sql)에서 확인하되 기존 DB에 실행하는 migration SQL로 사용하지 않는다.

[f4 migration](../backend/alembic/versions/f4b8c1d9a620_add_service_carousel.py)의 downgrade는 저장된 슬라이드가 하나라도 있거나 `feed_valid_until`이 설정된 콘텐츠가 있으면 중단한다. 캐러셀과 만료 정책을 삭제하는 자동 rollback을 제공하지 않으며, 테이블을 잠근 뒤 SQL에서 검사하므로 offline downgrade SQL에도 같은 보호가 적용된다.

## 3. 적재 경계

실행 모듈은 [app.seeds.carousel_contents](../backend/app/seeds/carousel_contents.py)다. 서비스 import는 기본 dry-run이며 `--apply`만 DB를 변경한다. 원본 `ready` 문자열이나 검수 점수만으로 발행하지 않으며, 미검수 콘텐츠를 강제로 발행하는 옵션은 제공하지 않는다. 외부 다운로드·에이전트 실행·생성 DB 접근은 하지 않는다.

```powershell
# backend 디렉터리. 실제 대상 DB명을 지정한다. 기본 실행은 미리보기다.
python -m app.seeds.carousel_contents --file <bundle.json> --expected-database <대상_DB명>

# 실제 운영자가 해당 digest의 내용·이미지·출처를 검토한 별도 기록으로 미리보기
python -m app.seeds.carousel_contents --file <bundle.json> --expected-database <대상_DB명> --review-record-file <review.json>

# 저장이 승인된 대상에서만 위 명령에 --apply 추가
```

`--database-url`을 별도로 지정할 수 있으며 생략하면 서비스 설정을 사용한다. PostgreSQL DB명은 필수 `--expected-database`와 정확히 일치해야 한다. URL·비밀번호는 출력하지 않는다. 기본 실행은 읽기 전용 트랜잭션이며 파일 전체 검증은 DB 접근 전에 완료한다.

### 입력 묶음

`bundle.json`은 UTF-8 JSON이다. 최상위는 `{schemaVersion:1,producer:"ani_menu_agent",topicCode:"anime",items:[...]}`이며 1~50개 항목을 받는다.

| 항목 | 계약 |
| --- | --- |
| `items[].package` | 원본 ContentPackage JSON. 고유 `request_id`, 허용된 `content_type`, 미래 `feed_valid_until`, 출처·팩트·카드·비어 있지 않은 상세 본문과 근거 ID가 필요하다. `menu_work_ids`는 누락·빈 배열을 허용하며, 있으면 양의 고유 정수여야 한다. 개인화 작품 매핑이 없어도 캐러셀을 표시할 수 있다. 원본의 추가 메타데이터도 보존한다. |
| `items[].assets` | 각 카드의 `{page,path,sha256}`. page가 카드와 정확히 일치하고 `sha256`은 실제 파일 및 카드 `rendered_image_key`와 같아야 한다. |
| PNG `path` | bundle 폴더 안의 상대 파일 경로. 절대 경로·상위 폴더 이동·심볼릭 링크·junction은 거절한다. 원격 URL은 다운로드하지 않는다. |
| `items[].reviewEvidence` | 선택적인 원본 검수 자료. 보존용이며 서비스 발행 승인으로 사용하지 않는다. |

카드는 3~10장이고 page는 1부터 연속·고유해야 한다. 각 PNG는 단일 프레임 1308×1632, 최대 20MiB이며 전체 묶음의 PNG 합계는 200MiB 이하이다. JSON 파일은 16MiB 이하이다. 형식·SHA-256뿐 아니라 실제 디코딩을 검사한다. 정규화된 배치 영역이 캔버스 밖으로 나가거나 제목·본문·근거 참조가 잘못되면 거절한다. `detail_source_ids`는 상세글에서 참조한 팩트들의 실제 출처를 모두 포함해야 하며, `sources`에 선언한 보충 출처를 추가할 수 있다. 선언되지 않은 팩트·출처 ID는 거절한다. 이 importer는 상세글까지 제공하는 범위이므로 비어 있지 않은 `detail_body`가 필수이며 카드 문장을 이어 붙여 본문을 만들어내지 않는다. `feed_valid_until`은 timezone이 있는 미래 시각이고 `purge_at`이 있으면 만료보다 앞설 수 없다.

### digest와 운영자 검토 기록

각 항목의 digest는 아래 객체를 Python `json.dumps(sort_keys=True, ensure_ascii=False, separators=(',',':'), allow_nan=False)`로 직렬화한 UTF-8 바이트의 SHA-256이다. `assets`는 page순으로 정렬한다.

```text
{producer, topicCode, package, assets:[{page, sha256}, ...]}
```

PNG의 실제 바이트는 검증된 sha256을 통해 결합한다. 원본 package 전체와 만료 시각은 digest에 포함하고, 로컬 파일 경로와 `reviewEvidence`는 제외한다. 이 해시는 묶음의 일치성을 확인하며 과거 LLM 검수 승인 증거를 새로 만들지 않는다.

별도 `--review-record-file`은 `{schemaVersion:1,records:[...]}` 형식이다. 각 record에는 `digest`, `decision`(`approved` 또는 `rejected`), `reviewedBy`, timezone 포함 `reviewedAt`, `comment`가 모두 필요하다. digest는 입력 묶음의 항목과 일치해야 하며 중복 기록·미래 검토 시각·빈 검토자와 의견은 거절한다. **이 파일을 제공하는 로컬 운영자의 실제 검토 기록이 신뢰 경계다.** 서비스는 운영자의 신원을 인증하거나 검토 행위를 암호학적으로 입증하지 않는다. producer의 점수를 보고 승인 기록을 자동 생성하지 않는다.

| 운영자 기록 | `Content.status` / `judgment_status` | `Draft.status` / 실행 outcome | 공개 조회 |
| --- | --- | --- | --- |
| 없음 | `pending_review` / `needs_review` | `checking` / `on_hold` | 차단 |
| `approved` | `active` / `confirmed` | `published` / `publish_candidate` | 다른 공개·만료 조건까지 통과한 경우 허용 |
| `rejected` | `hidden` / `failed` | `discarded` / `rejected_archive` | 차단 |

보류·거절도 Content와 슬라이드를 서비스 DB에 보관하되 공개 API에서 제외한다. 동일 producer·request ID와 동일 digest는 원문을 다시 만들지 않고, 다른 digest는 충돌로 처리하여 기존 원문을 덮어쓰지 않는다. 같은 digest에 대한 별도 검토 기록만 갱신할 수 있으며 이전 기록보다 늦은 `reviewedAt`이어야 한다. 검토 파일을 생략한 재실행은 기존 검토 결정을 취소하지 않는다. 실행 기록·초안·콘텐츠·출처·슬라이드와 검토 상태 전환은 한 트랜잭션으로 처리하고 동시 적재는 advisory lock으로 직렬화한다. 한 항목이라도 실패하면 묶음 전체를 rollback한다.

재실행은 저장된 Content·슬라이드의 텍스트·배치·실제 PNG 해시·출처·Topic·소유 실행·method·digest도 원본과 대조한다. 누락·변조가 있으면 동일 digest라는 이유로 건너뛰거나 자동 수리하지 않고 실패한다. `Content.summary`는 표지의 body 문장(없으면 표지 제목), 기존 `image_url`은 1페이지의 서비스 이미지 경로로 채운다.

원본 producer의 `render-manifest`와 검수·queue 갱신 경합 문제는 이번 서비스 변경으로 고쳐지지 않는다. 수정 가능한 producer의 상태만 신뢰하지 않고, 실제로 검토한 고정 묶음과 별도 운영자 기록을 서비스 경계에서 다시 확인한다. 검토 API·승인 화면은 이번 범위에 추가하지 않는다.

CLI 출력은 `mode`(`dry_run`/`apply`), `created`, `review_updated`, `unchanged`와 `items[]`의 `request_id`, `digest`, `content_id`, `status`다. 미리보기에서 아직 없는 콘텐츠의 `content_id`는 null이다. dry-run의 예상 created 수는 실제 저장 결과가 아니다. 거절되면 종료 코드 1과 민감한 접속정보를 제외한 오류를 반환한다.

## 4. 공개 조회 계약

기존 `GET /api/v1/topics/{topicId}/feed`의 카드와 `GET /api/v1/contents/{contentId}?topicId=...`의 상세에 `slides[]`를 추가한다. 상세에는 `sources[]`도 추가한다. 기존 `imageUrl`, `sourceUrl`, `body`, `displayMode`는 유지하며 슬라이드 없는 기존 콘텐츠의 `slides`는 빈 배열이다. 승인되어 적재된 AI 원본 글은 기존 `displayMode=full_body`를 사용한다.

| 응답 | 필드 |
| --- | --- |
| `slides[]` | `imageUrl`, `page`, `role`, `eyebrow`, `titleLines`, `bodySentences`, `layout`, `width`, `height` 모두 필수·nonnull. page 오름차순. |
| `slides[].layout` | 공개 허용 필드만 camelCase로 반환: `template`, `fitMode`, `backgroundStyle`, `imageBox`, `copyBox`, `textOverlayBox`, `imageScale`, `imageOffsetY`, `contrastMode`, `blurBackground`. 각 box는 `{x,y,width,height}`. DB의 원본 snake_case 배치와 구분하며 미허용 키는 응답에서 제외한다. HTML 문자열을 받거나 실행하지 않는다. |
| `sources[]` | `url`, `role`; `title`, `publisher`, `citationOrder`는 null 가능. citationOrder 오름차순(NULL은 뒤), 같은 순서에서는 출처 행 ID순. |
| AI 표시 | 새 캐러셀은 `productionType:"ai"`와 `notices:[{code:"AI_GENERATED",message:"AI가 생성한 콘텐츠입니다."}]`를 반환한다. `body`는 상세 본문이고, 출처·AI 고지는 별도 필드로 제공하므로 프론트는 함께 표시해야 한다. |

`imageUrl`은 `/api/v1/contents/{contentId}/slides/{page}/image?topicId=...` 형태의 서비스 상대 경로다. FE와 API origin이 다르면 API base URL을 기준으로 해석하고 현재 세션 쿠키를 포함해야 한다. 출처 URL이나 producer의 blob key를 이미지 경로로 대신 사용하지 않는다.

새 이미지 경로는 `GET /api/v1/contents/{contentId}/slides/{page}/image?topicId=...`다. 로그인·온보딩 완료, 본인의 활성 Topic, Content의 공개 상태·출처·만료, 확인된 검토 상태와 3~10장 완성 조건을 확인한다. 성공은 `200 image/png`, `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`다. GET이므로 CSRF 헤더는 필요하지 않다. 응답은 JSON이 아닌 바이너리다.

| 상태 | 조건 |
| --- | --- |
| `401 AUTH_REQUIRED` | 세션 인증 없음·만료 |
| `403 ONBOARDING_REQUIRED` | 가입 완료 전 |
| `404 TOPIC_NOT_FOUND` 또는 `CONTENT_NOT_FOUND` | 비활성·타인 Topic, 다른 Topic의 콘텐츠, 보류·거절·만료·불완전 캐러셀, 없는 page |
| `422 VALIDATION_ERROR` | 누락·범위 밖 topicId/contentId/page. page 입력 범위는 1~10이며 실제 없는 장은 404. |

만료·불완전 캐러셀은 피드와 북마크 목록에서 제외하고 상세·이미지 및 반응 설정 PUT에서 404로 차단한다. 기존 비캐러셀의 공개 기준과 `displayMode=link_excerpt`는 유지한다. 기존 이미지·대표 출처 필드도 유지하므로 기존 소비자가 새 배열을 즉시 사용하지 않아도 응답 형식은 깨지지 않는다. DB·API 검증 당시에는 프론트가 새 배열을 사용하지 않았지만, 현재 후속 통합 코드의 홈·상세 어댑터와 공통 캐러셀은 `slides`, 구조화된 텍스트, 보호 이미지와 전체 `sources`를 사용하도록 연결되어 있다. 실제 화면 검증 결과는 [별도 프론트 기록](FRONTEND_CAROUSEL_INTEGRATION_2026-10-28.md)을 따른다.

## 5. 검증과 미실행 항목

DB에 연결하지 않고 `create_app(enable_dev_api=...).openapi()`를 생성하여 공개 V1 **36개**, 상태 확인 **3개**, 개발 API 사용 시 추가 **6개**를 확인했다. 총 작업 수는 개발 API 비활성 시 39개, 활성 시 45개다. 이미지 GET 하나가 추가되며 공개 import·승인 API는 없다.

`backend` 전체 `python -m pytest -q`의 최종 결과는 **951 passed, 3 skipped, 1 warning**(81.05초)이다. 앞선 관련 통합 배치의 88 passed, 1 skipped는 부분 실행 기록이며 전체 판정은 이 최종 결과를 따른다.

- 새 임시 PostgreSQL 컨테이너의 `carousel_test`·`onboarding_test` DB를 사용했고 일반 `DATABASE_URL`은 연결 불가 테스트 주소, 실제 OAuth는 비활성화했다. 캐러셀 PostgreSQL 6개와 온보딩 PostgreSQL 8개가 전체 통과 수에 포함된다.
- 캐러셀 검증은 두 생년월일 상태와 기존 데이터의 migration 보존, DB 제약과 데이터가 있을 때 downgrade 차단, dry-run·보류 적재·명시적 승인·거절·동일본 재실행·변경본 충돌·원자적 rollback·동시 적재, 인증된 피드→상세→PNG 왕복을 포함한다. API 검증은 만료·비공개·불완전·다른 Topic의 차단과 기존 글 호환을 포함한다.
- skip 3개는 Windows 심볼릭 링크 생성 권한에 따른 1개와 별도 설정이 필요한 기존 실제 Redis 통합 2개다. 경고 1개는 기존 Starlette/AnyIO 사용 중단 예정 경고다.
- `export_current_schema.py --check`와 `git diff --check`가 통과했다. 문서의 로컬 링크와 코드 펜스를 확인했고 에이전트 소스 변경이 없음을 확인했다.

최초 전체 실행의 로깅 관련 실패 2개는 Alembic 테스트의 `fileConfig`가 다른 로거를 비활성화하는 간섭이었다. 테스트 설정을 격리한 뒤 위 최종 결과를 얻었으며 에이전트 코드를 고친 결과가 아니다.

온보딩 화면 연결에서 기록한 259개 프론트 테스트 및 브라우저 결과는 캐러셀 화면 검증을 뜻하지 않는다. 위 951개 통과는 프론트 캐러셀 연결 이전 DB·API 단계의 결과이며, 후속 화면 작업의 최신 실행 결과는 [프론트 연결 기록](FRONTEND_CAROUSEL_INTEGRATION_2026-10-28.md#3-검증-기록)에 구분한다. 실제 사용자 DB의 migration·콘텐츠 적재, 에이전트 재생성, main 병합과 배포는 수행하지 않았다.
