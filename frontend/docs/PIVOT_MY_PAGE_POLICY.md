# MY PAGE 정책 열람 — 2026-11-09

- 작업 트리: `C:\dev\me-and-nu-nav-fidelity-1105`
- 브랜치: `codex/my-page-policy-1109` (upstream 없음)
- 기준: 최신 `origin/Checking`, `85df72f7fe040679cf2700b4f676da6406f8c1bf`
- 보존한 선행 커밋: `7e44fc5f0af08f40e86fb6a568963395050c1ec9` (`feat(pivot): add gated my page policy viewing`). 개발 검토 기능은 이 커밋 위에 추가한다.
- 이전 문의 브랜치 `codex/my-page-inquiry-1109` / `9ab17e6343632ac1c94ca840599260157f26eeb3` 보존. 해당 커밋은 기준 Checking에 포함됨.

## 구현 범위

MY PAGE의 이용약관·개인정보처리방침 버튼이 기존 온보딩에서 추출한 공통 native Dialog를 연다. Service에서는 기존 `loadPivotPolicies` → `getPolicies` → `GET /api/v1/policies`를 사용한다. 본문·원문 URL 렌더러, 조회 중·실패·누락·공개 미확인 안내와 재시도를 제공한다. 공개 확인이 된 경우 API의 실제 버전만 표시하며 시행일은 추정하지 않는다.

완전한 HTTP(S) URL은 새 탭 원문 링크로 표시한다(`noopener noreferrer`). 일반 본문은 줄바꿈을 보존한 텍스트로 출력한다. HTML 실행, 임의의 URL scheme, 자격증명이 포함된 URL은 허용하지 않는다.

Dialog의 닫기·Escape·브라우저 뒤로가기는 MY PAGE를 언마운트하지 않으며 진입 버튼으로 포커스를 돌려준다. 같은 URL의 history entry를 사용하고 기존 history state를 보존한다. 닫으면 조회를 취소하고, 취소 후 도착한 응답은 세션 만료 이벤트나 화면 갱신을 일으키지 않는다.

기본 Demo는 원문 미제공 안내만 표시하며 API를 호출하지 않는다. 아래의 명시적 로컬 검토 모드만 개발 문서를 읽는다. 정책 열람에서는 동의·계정 변경 API를 호출하지 않는다. 온보딩의 선택·진행·제출 로직은 유지하고 공통 Dialog·URL 처리를 재사용한다. 기존 테스트 안내는 실제 법적 문서가 아님을 명시한다.

## 팀 정책 원문: 로컬 개발 전용 검토

2026-10-23 팀 원본 `menu_서비스정책문서_통합본_v1.1.md`를 Downloads에서 확인하고, 바이트 변경 없이 `frontend/dev/policy-review/team-policy-2026-10-23.md`에 복사했다. 이 파일은 로컬 전용이며 Git에 포함하지 않는다. 참고 PDF는 `menu_서비스정책문서_v1.6.pdf`이며, 파일명 버전과 아래 정책별 버전은 별개다. 원본 MD 및 Figma의 내용은 수정하지 않았다.

| 문서 | 버전 | 문서 기재 시행일 | 조항 수 | 표 수 | Figma 전문 텍스트 노드 |
| --- | --- | --- | --- | --- | --- |
| 이용약관 | v1.2 | 2026-11-02 | 31 | 0 | 1363:8750 |
| 운영정책 | v0.6 | 2026-11-02 | 15 | 3 | 1363:8755 |
| 개인정보처리방침 | v0.3 | 2026-11-02 | 17 | 6 | 1363:8766 |

두 Figma 프레임의 전문과 MD를 대조했다. 공백·Markdown 구분자·목록 마커를 제외한 모든 본문과 표 셀 텍스트가 일치한다. 비교 길이는 각각 5,334 / 3,583 / 3,241자다. 출처, 원문 SHA-256, Figma 비교 해시는 `frontend/dev/policy-review/provenance.json`에 보관한다. 조항·항목 번호와 중첩 목록, 표는 별도 구조 검증으로 보존을 확인한다. 특히 운영정책 제6조는 표 전후의 1~7항 번호를 유지한다.

실행 방법: 팀이 별도로 공유한 원본 MD를 위의 로컬 전용 경로에 보존한 뒤, `frontend`에서 `npm run dev:policy-review`를 실행하고 `http://127.0.0.1:3196/pivot/demo?view=my&policyReview=1`을 연다. 새 clone에는 원문이 없으므로 검토 API는 404로 비활성화된다. 테스트 fixture나 운영 API로 대체하지 않는다. MY PAGE의 이용약관 메뉴에서 이용약관·운영정책을 함께 보고, 개인정보처리방침 메뉴에서 별도 전문을 본다. 제목·버전·시행일을 원문에서 읽어 표시하며, 검토 중·정식 게시 미승인 및 가입 동의와 별개라는 안내를 Dialog 상단에 유지한다.

기존 `PolicyDialog`를 확장했다. 문서 본문만 스크롤하고 제목·검토 안내·닫기 버튼은 고정한다. 표는 의미 있는 HTML table과 헤더를 사용하며 좁은 화면에서는 표 영역만 좌우 스크롤한다. 문서는 HTML 실행이나 자동 링크 없이 텍스트로 렌더링한다. 닫기·Escape·브라우저 Back과 MY PAGE 포커스·상태 복원은 기존 구현을 재사용한다.

### 로컬 경계와 공개 차단

- Public 저장소에 원문을 넣지 않는다. `frontend/.gitignore`는 `dev/policy-review/`의 내용을 제외하고 원문 없는 출처·해시 메타데이터 `provenance.json`만 허용한다. 원문을 강제 Stage하거나 공개 fixture/문서에 복사하지 않는다.
- 실행 스크립트는 127.0.0.1:3196에만 바인딩하고 실제 계정 API 연결을 끈다. 배포 환경에서는 시작을 거부한다.
- 클라이언트 진입은 development + loopback + `/pivot/demo` + `policyReview=1`에 한정한다. 서비스 MY PAGE, 기본 Demo 및 온보딩에는 연결하지 않는다.
- FE 개발 서버의 `GET /api/dev/policy-review`가 별도로 development, 서버 전용 `PIVOT_POLICY_REVIEW_ENABLED=true`, 배포 환경 아님, loopback Host·포트·Origin·Referer 및 프록시 헤더를 검사한다. production/preview는 플래그가 있어도 404이며 원문 파일을 읽지 않는다.
- Next가 내부 URL을 localhost로 정규화하는 경우 실제 Host가 127.0.0.1이어도 양쪽 loopback과 포트 일치를 확인한 뒤 허용한다. 외부 Host나 다른 포트는 거부한다.
- 실행 스크립트만 서버 런타임의 `PIVOT_POLICY_REVIEW_SOURCE`를 설정한다. route에는 원문 경로 fallback이나 원문 import가 없다. 실제 Next 파일 추적기로 원문을 수집하지 않는지 검증하고, `outputFileTracingExcludes`로 배포 추적에서도 로컬 검토 디렉터리를 제외한다. Git 제외와 런타임 접근 차단은 별도로 적용한다.
- 허용된 요청만 원문을 읽고 SHA-256을 확인한다. 경로 미설정·파일 누락은 404, 읽기 실패·변경·파싱 실패는 일반 503이며 임의 문서/API 데이터로 대체하지 않는다. `VERCEL_ENV` 또는 `VERCEL`이 있으면 development도 차단한다.
- 원문은 `public/`에 두거나 클라이언트 모듈에서 import하지 않는다. 공개 정적 경로는 404이고, 브라우저 JS에 원문이 포함되지 않는지 확인한다. 응답은 `private, no-store`, noindex, CORS 미허용이다. 클라이언트 조회는 `credentials: omit`으로 계정 쿠키도 보내지 않는다.
- 기존 `policy-document.ts`의 공개 승인 목록과 `GET /api/v1/policies` 경로는 변경하지 않았다. 개발용 ReviewDocument는 Policy/ConsentInput과 다른 타입이다. 실제 정책 승인 이후의 교체는 별도 작업이며 이 개발 경로를 공개 기능으로 활성화하지 않는다.

### Public 저장소 검수

원문 MD는 한 번도 Git 추적/커밋되지 않았으며, 보완 전의 신규 파일 상태에서 제외했다. 원본 바이트 SHA-256은 그대로다. 공개 후보 코드·JSON·테스트·문서에는 정책 전문이나 담당자 실명을 넣지 않는다. 단위 테스트는 명백한 인공 문자열로 파서/접근 제어를 확인하고, 원문이 있을 때만 별도의 전체 해시·Figma 정합성 검사를 추가한다. 브라우저 검증도 원문을 로컬에서 읽어 비교하며 본문을 테스트 코드에 복사하지 않는다.

원문에는 개인정보 보호책임자 실명이 포함되어 있다. 확인한 문서에서 인증정보·API 키·토큰·개인 비밀번호는 발견되지 않았다. 미정 연락처는 그대로 보존했다. 검토용 Figma 추출본, 스크린샷과 실행 기록은 기존에 무시된 `agent/out/` 안에 두며 공개 산출물로 취급하지 않는다.

## 정책 확인 결과 및 게시 차단

2026-11-09 [Checking 공개 API](https://api.checking.me-nu.kr/api/v1/policies)를 읽기 전용으로 확인했다.

| 항목 | Figma 표기 | Checking API | 결과 |
| --- | --- | --- | --- |
| 이용약관 | v1.2 | `terms`, `checking-20261028-v1` | 불일치, 내부 검증용 테스트 안내 |
| 운영정책 | v0.6 | 해당 문서/항목 없음 | 원문 및 API 매핑 미확정 |
| 개인정보처리방침 | v0.3 | `privacy`, `checking-20261028-v1` | 불일치, 정식 서비스용이 아님을 명시 |
| 시행일 | 2026-11-02 | 응답 필드 없음 | FE에 표시·추정하지 않음 |

Figma는 [이용약관·운영정책 1363:8740](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-8740), [개인정보처리방침 1363:8756](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-8756)을 직접 확인했다. 위 Checking 조회는 기존 공개용 Slice의 확인 기록이며 이번 작업에서는 운영 API를 수정하지 않았다.

**Blocker — 실제 문서 게시:** 운영 승인 및 승인된 원문의 배포가 완료되지 않았다. `policy-document.ts`의 공개 승인 목록은 비어 있어 Service MY PAGE는 정책 API 본문/URL을 법적 정책으로 게시하지 않는다. 표본 문자열·버전 탐지만으로 게시를 허용하지 않는다. 개인정보처리방침의 `[연락용 이메일 주소]` 확정도 필요하며 임의로 채우지 않았다.

해제하려면 담당자가 승인한 문서와 실제 배포 API를 대조한 뒤 `type`, `policyVersion`, 정확한 API `text`의 SHA-256을 공개 승인 목록에 등록해야 한다. URL 방식이면 승인된 원문 URL 문자열을 해시한다. 같은 버전에서 본문/URL이 변경되어도 다시 확인할 때까지 차단된다. 런타임 환경변수나 Demo로 우회하는 경로는 없다.

**Major — 문서 계약:** 현재 `privacy`는 개인정보 수집·이용 동의 항목이며 전체 개인정보처리방침과의 대응은 미확정이다. 운영정책의 별도/통합 API 제공 방식도 미확정이다. API에는 제목·시행일·승인 상태 필드가 없다. 로컬에서는 MD의 독립된 세 문서를 사용하며 이 API 항목에 억지로 대응시키지 않는다. BE·DB·정책 원문을 변경하지 않았다.

## 생년월일: 2026-11-09 최신 PM 결정

확정된 방향은 **OAuth에서 생년월일 확보 → 만 14세 이상 판별 → 가입 허용 여부 결정**이다. 생년월일 수집 방식 자체가 계속 미정인 상태로 취급하지 않는다.

현재 API V1.4/온보딩 요청은 birthDate를 받지 않고(`extra=forbid`), BE OAuthIdentity 및 카카오·네이버 응답 변환도 생년월일을 처리하지 않는다. 레거시 FE API 타입에는 birthDate가 남아 있다. 10/23 원문의 개인정보처리방침 제2조 제3항은 생년월일 미수집을, 수집 항목 표는 회원이 만 14세 이상임을 확인하는 방식을 적고 있어 최신 결정과 다르다. 원문을 조용히 고치거나 현재 연령 확인이 구현됐다고 표시하지 않는다.

별도 연령 확인 Slice에서 확정할 사항은 제공자 미제공 시 처리, 실제 생년월일 DB 저장 필요 여부, 14세 미만/확인 불가 가입 차단과 FE 안내, 개인정보처리방침 수정 범위다. OAuth·생년월일·연령 판별 코드 및 DB는 이번 작업에서 수정하지 않았다.

## 검증 및 한계

이번 커밋 대상은 안전한 코드·테스트·설정·메타데이터·문서 15개다. 로컬 원문 MD는 별도로 보존하며 커밋에서 제외한다.

| 파일 (`frontend/` 기준) | 변경 |
| --- | --- |
| `.gitignore` | 미승인 로컬 원문 및 사본 추적 제외 |
| `next.config.ts` | 로컬 검토 디렉터리 배포 파일 추적 제외 |
| `components/pivot/my-page-policy-dialog.tsx` | 명시적 로컬 Demo 검토 진입 분기 |
| `components/pivot/policy-dialog.tsx` | 공통 검토 안내 슬롯 |
| `components/pivot/pivot.module.css` | 문서·목록·표와 고정 Dialog 안내/닫기 |
| `components/pivot/local-policy-review.tsx` | 세 문서 열람·탐색·조회/재시도 |
| `lib/pivot/policy-review.ts` | 별도 검토 타입·원문 파서·로컬 URL 판별 |
| `lib/pivot/policy-review-access.ts` | FE 개발 endpoint 접근 차단 |
| `app/api/dev/policy-review/route.ts` | 접근 확인 후 원문 읽기와 해시 확인 |
| `dev/policy-review/provenance.json` | 출처·버전·Figma 대조 증거 |
| `scripts/dev-policy-review.cjs` | loopback 전용 실행 |
| `package.json` | `dev:policy-review` 스크립트 |
| `tests/pivot-policy-review.test.cjs` | 원문·렌더러·환경/출처 차단 테스트 |
| `tests/pivot-policy-review-browser-check.cjs` | 실제 로컬 읽기와 모바일 검증 |
| `docs/PIVOT_MY_PAGE_POLICY.md` | 실행·검증·정책/PM 인계 |

- 기존 커밋 검증: 관련 10개 테스트 파일 **229/229 통과**, TypeScript·diff·모바일 통과. 이번 작업에서 전체 테스트를 반복하지 않았다.
- 원문 연결 구현 시: 정책 관련 **53개**(개발 검토 30 + 기존 정책 23), 온보딩 계약·동의 흐름 **68개** 통과. 이번 보완에서 변경하지 않은 온보딩 테스트는 반복하지 않았다.
- 공개 노출 보완 검증: 관련 개발 검토 테스트 **36개 통과**. production/preview 차단과 파일 IO 미발생, 원문 누락 비활성화, 실제 해시·Figma 정합성, 인공 fixture 거부, 파일 추적 제외, loopback 실행 조건을 확인한다.
- 원문 파일 접근을 막은 환경에서도 같은 테스트 **35개 통과 / 원문 정합성 1개 skip**. 로컬 MD를 삭제·이동·변경하지 않고 파일 부재 환경을 재현했다.
- 최종 TypeScript(`npm run typecheck`)와 `git diff --check` 통과.
- 개발 검토 모바일 브라우저: **375px·320px 모두 통과**, page error 0건. 실제 로컬 FE 읽기 endpoint로 세 문서 전문을 확인하며 계정 API나 쓰기는 전혀 호출하지 않는다. 기본 Demo 차단, 온보딩 선택 유지, 정적 경로·cross-origin 차단, JS에 원문 없음도 검증한다.
- 보안 보완 후 모바일 검증도 다시 통과했다. OS의 실제 수신 주소는 `127.0.0.1:3196`이며 공개 후보 873개 텍스트 파일에서 원문 문장·담당자 실명 중복은 발견되지 않았다. 이번 검증 기록은 무시된 `agent/out/policy-public-check/`에 보관한다.
- 이번 재현 스크립트: `tests/pivot-policy-review.test.cjs`, `tests/pivot-policy-review-browser-check.cjs`. 결과와 스크린샷은 무시된 `agent/out/pivot-policy-review/`에 보관한다. 전체 FE 테스트 및 Production Build는 수행하지 않았다. production 환경 차단은 route 단위 테스트로 검증했다.
- 로컬 서버는 기존 node_modules 링크에 대한 Turbopack 오류를 피하기 위해 `next dev --webpack`을 사용했다. 저장소 빌드 설정은 변경하지 않았다.
- 전체 FE 테스트·Production Build는 요청대로 통합 전 최종 HEAD에서 한 번 수행하도록 남겨둔다. 이번 범위는 공개 노출 보완 및 조건부 로컬 Commit까지이며, Push·PR·Merge·수동 배포는 수행하지 않는다.
