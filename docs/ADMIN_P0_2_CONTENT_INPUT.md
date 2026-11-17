# ADMIN P0-2: 콘텐츠 입력 첫 Slice

- 기준: `origin/Checking` `71312a48a6891311e8b2782c4cb3d37b3e86611e`, 2026-11-13.
- 범위: 생성·수정 공통 폼, 입력 검증, 요청 DTO, 읽기 기반 변경 제안. **DB 저장/쓰기 HTTP API는 구현·등록하지 않는다.**
- PR #67·#69·#70의 관리자 레이아웃, 세션/공통 Client, 읽기 훅, Repository/Service, `require_admin`을 재사용한다. 기존 운영 앱의 관리자 읽기 API도 계속 미등록이다.

## 화면과 입력 계약

- 목록 → `/admin/contents/new`, 상세 → `/admin/contents/[contentId]/edit`. 목록 검색·필터·페이지 query를 복귀 URL에 보존한다.
- 공통 필드: 제목(필수, 300자), 요약(선택, 10,000자), 본문(선택, 100,000자), 출처 최대 50개. 출처를 추가한 경우 URL(필수, 1,000자), 출처 제목(선택, 300자).
- 제목·출처 길이는 기존 모델 제약을 따른다. Text 필드/출처 개수 상한은 이 입력 계약의 요청 크기 제한이며 DB 제약이나 최종 기획 확정이 아니다.
- 문자열 앞뒤 공백 제거, 빈 선택 텍스트는 null. 내부 줄바꿈/탭은 유지하며 NUL 등 제어 문자와 단독 surrogate는 거부한다. 문자 수는 Unicode code point 기준이다. 입력은 평문이며 HTML로 렌더링하지 않는다.
- 출처는 계정 정보가 없는 절대 HTTP(S) URL만 허용한다. 공백·역슬래시·잘못된 포트/호스트는 거부한다. URL을 가져오거나 DNS 조회하지 않는다. 중복은 앞뒤 공백 제거 후 동일 문자열 기준으로 거부하며 Worker URL 정규화/중복 접수 정책은 복제하지 않는다.
- `AdminContentCreateRequest`: `title` 필수, 생략 가능한 `summary`/`body`는 null, `sources`는 빈 배열.
- `AdminContentUpdateRequest`: 네 필드 모두 필수인 전체 **입력값** 계약이다. PATCH가 아니며, null은 선택 텍스트 비우기 의도다. DTO 자체가 DB 교체 명령은 아니다.
- 출처 입력은 `{url, title}`만 허용한다. 발행처, 역할, 인용 순서, source/agent ID는 서버 소유다. 최상위/출처의 미허용 필드, 타입 강제 변환, 상태·발행·판정·owner·topic·slide·embedding 입력을 거부한다.

```json
{
  "title": "직접 작성한 제목",
  "summary": null,
  "body": "본문",
  "sources": [{"url": "https://example.org/article", "title": "원문 제목"}]
}
```

수정 시 기존 읽기 DTO의 값을 초기화하고 새로고침/권한 재조회 응답으로 작성 중 값을 덮어쓰지 않는다. 조회 중/장애 시 폼을 숨기고 비활성화하며, 성공 후 기존 입력을 복원한다. 401/403/콘텐츠 없음은 폼과 작성값을 폐기한다. 브라우저 저장소에는 저장하지 않는다.

미저장 변경은 필드·출처 추가/삭제를 포함한다. 링크/사이드바 이동은 확인창, 새로고침·탭 닫기·브라우저 뒤로 가기는 브라우저 `beforeunload` 확인을 사용한다. 편집 진입/복귀를 일반 문서 링크로 유지해 브라우저 이력 이동에도 이 경계를 적용한다. 향후 진입을 SPA `Link`/`router.push`로 바꾸면 이력 이동 guard도 함께 변경·검증해야 한다. 브라우저가 beforeunload 확인을 제한하는 강제 종료는 보호할 수 없다.

`입력 확인`은 클라이언트 검증이다. `수정 중`, `입력 오류`, `입력 확인 완료·미저장`, `접근 권한 재확인` 상태를 구분하고 저장 버튼은 항상 비활성화한다. 실제 요청이 없는 저장 중/저장 성공 상태는 표시하지 않는다. Mock 저장 함수나 관리자 Demo 경로를 추가하지 않았다.

## 모델/PR #68 경계

- `Content`: title/summary/body와 ContentSource만 입력 대상으로 삼는다. `status=active` 기본값을 사용하는 객체를 생성하지 않는다. `status`와 `judgment_status`는 별개이며 폼이 바꾸지 않는다.
- `Draft`: topic과 agent_run 또는 creator_channel owner가 필요하다. 관리자 작성자의 매핑이 미정이므로 Draft 생성도 하지 않는다. Draft에는 summary 필드가 없어 Content 입력을 그대로 Draft에 대입할 수 없다.
- `ContentSource`: 출처 역할·인용 순서·출처 사이트·Agent provenance는 기존 값을 보존해야 한다. 현재 입력 계획의 sources는 원하는 URL/제목 목록이며 일괄 삭제/재생성 지시가 아니다. 실제 저장 시 신뢰 가능한 식별자, 기존 메타데이터 보존, 신규/변경/삭제 정책을 먼저 확정한다.
- `ContentSlide`: 기존 immutable 카드 이미지/레이아웃을 수정하지 않는다. 본문/제목이 바뀔 때 기존 카드 재생성과 불일치 방지 정책은 BE·에이전트 협의 대상이다.
- PR #68 (`889c7a0`, 조사 시 open): CLI 접수 → Worker → Agent → `ready_for_staging` 번들 → 기존 적재기 → 승인 후 공개의 책임 범위다. 이번 Slice는 생성·적재·승인·발행·재시도·중복 URL 처리를 호출하거나 복제하지 않는다.

## Service와 권한

`prepare_create`는 재검증한 요청만 포함하는 `AdminContentInputPlan`을 반환한다. `prepare_update`는 기존 `get_content`를 사용해 존재 여부/저장 enum/출처 메타데이터를 읽고 원본과 입력 의도를 계획에 담는다. ORM 객체 생성, Repository write, flush/commit, 상태 전환, audit/worker 호출이 없다. 누락 콘텐츠·읽기 장애를 성공으로 바꾸지 않는다.

계획 생성은 영속 저장 권한을 뜻하지 않는다. 별도 HTTP Router를 만들거나 기존 읽기 Router에 쓰기 메서드를 추가하지 않았다. FE는 기존 세션 확인 뒤 보호된 읽기 API를 이용하며, 신규 폼도 최소 크기 목록 조회로 같은 접근 경계를 통과한다. 읽기 성공을 쓰기 권한으로 해석하지 않는다. 운영에서는 이전 Slice와 동일하게 미등록 API/권한 거부 안내가 나오며, 완전한 폼 흐름은 테스트 HTTP interception에서 검증한다.

## 실제 저장 연결 전 필요한 결정

1. **BE**: 관리자 권한 저장소 및 쓰기 권한·대상 범위, 실제 사용자/세션 검증 재사용. 읽기 권한만으로 쓰기 허용 금지. 기존 `require_admin`과 별도 쓰기 판단, CSRF 경계 적용.
2. **BE/기획**: Content 직접 등록 또는 Draft 우선 작성, topic/owner 매핑, 초기 상태와 허용 수정 상태, 승인 이후 재검수/발행 정책. 검증된 enum 외 새 상태 도입 금지.
3. **BE**: 갱신 시점/version 비교로 동시 수정 충돌 감지, 출처 provenance 보존, 본문/출처의 원자적 저장, 작업자·변경 전후·실패를 포함한 감사 로그 정책과 트랜잭션.
4. **BE/에이전트**: 텍스트 변경과 ContentSlide 재생성·적재·검수 책임, PR #68 산출물과 수동 편집의 소유권. 신규 Worker/자동 발행 구현은 이번 범위 밖.
5. **디자인/기획**: 최종 폼 구조, 분류/소유자 입력 방식, 저장·검수 UX 확정 후 화면과 저장 응답/오류 계약 연결.

## 디자인 확인

제공된 Figma `2205:11006`은 관리자 전체 페이지다. 해당 페이지의 메타데이터/렌더와 기존 확정 목록 `2344:13128`의 design context/렌더를 확인했다. 기존 관리자 레이아웃·폰트·색·로컬 SVG를 재사용하며 신규 에셋은 없다. 확인 범위에서 등록·수정 전용 확정 폼은 없었다. 이번 기본 필드 세로 배치는 기능 검증용 최소 구현이며 확정 Figma 사양으로 취급하지 않는다. 분류/분야 선택, 예약발행, 승인·반려, 임의 검수 단계는 추가하지 않는다.

## 검증

- BE: `test_admin_content_input.py`, `test_admin_content.py`, `test_admin_content_api.py` — 새 입력/서비스 검사 58개 및 관련 기존 읽기·권한 검사 65개, 합계 123개 통과. 처음 세 파일 120개 통과 후 URL 호스트 검증 3건을 추가하고 변경된 입력 검사 58개만 재실행했다. SQLite 메모리/테스트 객체만 사용하며 실서비스 DB·Redis 연결 없음.
- FE: `node --test tests/admin-content-input.test.cjs tests/admin-content.test.cjs` — 17개 통과.
- `npm run typecheck`, `npm run build -- --webpack` 통과. 이 worktree의 node_modules junction에 맞춰 기존 문서의 Webpack 검증 방식을 사용.
- 브라우저: `tests/admin-content-input-browser-check.cjs`의 1728px/390px 모두 통과. 등록/수정, 필수값·URL·중복 오류, 기존값, 저장 비활성화, 재조회 입력 보존, 링크/사이드바/새로고침/뒤로가기 미저장 확인, 변경 폐기/원복, query 복귀, 평문 안전성, 재시도, 401/403/404·비로그인, 권한 회수 시 입력 제거, 가로 넘침 없음을 확인했다. 모든 API를 로컬 테스트 응답으로 가로채며 GET 외 요청을 실패 처리한다. 실제 저장 E2E 검증을 뜻하지 않는다.

검증 환경은 기존 Python venv + 이 worktree의 `.venv-deps`, 테스트용 `DATABASE_URL`을 사용한다. 프런트 브라우저 도구는 기존 `.preview-pivot/nav-validation/browser-tools/node_modules/playwright`를 `PIVOT_PLAYWRIGHT_MODULE`로 지정한다. 스크린샷/결과는 ignored `.preview-pivot/admin-p0-2/`에 있다.

Consumer, DB 모델/Migration, 관리자 계정, Worker 코드는 변경하지 않았다. 로컬 커밋까지만 수행하며 Push·PR·Merge·배포는 하지 않는다.

## 변경 파일 (15개)

- `backend/app/schemas/admin_content_input.py`
- `backend/app/services/admin_content_input_service.py`
- `backend/tests/test_admin_content_input.py`
- `frontend/app/admin/contents/new/page.tsx`
- `frontend/app/admin/contents/[contentId]/edit/page.tsx`
- `frontend/components/admin/content-editor-screen.tsx`
- `frontend/components/admin/content-form.tsx`
- `frontend/components/admin/use-content-input-guard.ts`
- `frontend/components/admin/contents-screen.tsx`
- `frontend/components/admin/content-detail-screen.tsx`
- `frontend/components/admin/admin.module.css`
- `frontend/lib/admin-content-input.ts`
- `frontend/tests/admin-content-input.test.cjs`
- `frontend/tests/admin-content-input-browser-check.cjs`
- `docs/ADMIN_P0_2_CONTENT_INPUT.md`

이번 입력 전용 Slice의 미해결 Blocker/Major 결함은 발견하지 않았다. 실제 저장 활성화는 위 권한·상태·감사·동시 수정·출처/카드 동기화 결정에 막혀 있으며, 이를 우회하는 쓰기 경로는 없다.
