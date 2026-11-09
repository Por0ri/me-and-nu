# MY PAGE 정책 열람 — 2026-11-09

- 작업 트리: `C:\dev\me-and-nu-nav-fidelity-1105`
- 브랜치: `codex/my-page-policy-1109` (upstream 없음)
- 기준: 최신 `origin/Checking`, `85df72f7fe040679cf2700b4f676da6406f8c1bf`
- 이전 문의 브랜치 `codex/my-page-inquiry-1109` / `9ab17e6343632ac1c94ca840599260157f26eeb3` 보존. 해당 커밋은 기준 Checking에 포함됨.

## 구현 범위

MY PAGE의 이용약관·개인정보처리방침 버튼이 기존 온보딩에서 추출한 공통 native Dialog를 연다. Service에서는 기존 `loadPivotPolicies` → `getPolicies` → `GET /api/v1/policies`를 사용한다. 본문·원문 URL 렌더러, 조회 중·실패·누락·공개 미확인 안내와 재시도를 제공한다. 공개 확인이 된 경우 API의 실제 버전만 표시하며 시행일은 추정하지 않는다.

완전한 HTTP(S) URL은 새 탭 원문 링크로 표시한다(`noopener noreferrer`). 일반 본문은 줄바꿈을 보존한 텍스트로 출력한다. HTML 실행, 임의의 URL scheme, 자격증명이 포함된 URL은 허용하지 않는다.

Dialog의 닫기·Escape·브라우저 뒤로가기는 MY PAGE를 언마운트하지 않으며 진입 버튼으로 포커스를 돌려준다. 같은 URL의 history entry를 사용하고 기존 history state를 보존한다. 닫으면 조회를 취소하고, 취소 후 도착한 응답은 세션 만료 이벤트나 화면 갱신을 일으키지 않는다.

Demo는 원문 미제공 안내만 표시하며 API를 호출하지 않는다. 정책 열람에서는 동의·계정 변경 API를 호출하지 않는다. 온보딩의 선택·진행·제출 로직은 유지하고 공통 Dialog·URL 처리를 재사용한다. 기존 테스트 안내는 실제 법적 문서가 아님을 명시한다.

## 정책 확인 결과 및 게시 차단

2026-11-09 [Checking 공개 API](https://api.checking.me-nu.kr/api/v1/policies)를 읽기 전용으로 확인했다.

| 항목 | Figma 표기 | Checking API | 결과 |
| --- | --- | --- | --- |
| 이용약관 | v1.2 | `terms`, `checking-20261028-v1` | 불일치, 내부 검증용 테스트 안내 |
| 운영정책 | v0.6 | 해당 문서/항목 없음 | 원문 및 API 매핑 미확정 |
| 개인정보처리방침 | v0.3 | `privacy`, `checking-20261028-v1` | 불일치, 정식 서비스용이 아님을 명시 |
| 시행일 | 2026-11-02 | 응답 필드 없음 | FE에 표시·추정하지 않음 |

Figma는 [이용약관·운영정책 1363:8740](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-8740), [개인정보처리방침 1363:8756](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-8756)을 직접 확인했다. 원문을 코드에 복사하지 않았다.

**Blocker — 실제 문서 게시:** 운영 승인 및 승인된 원문의 배포가 확인되지 않았다. `policy-document.ts`의 공개 승인 목록은 비어 있어 현재 MY PAGE는 어떤 API 본문/URL도 법적 정책으로 게시하지 않는다. 표본 문자열·버전 탐지만으로 게시를 허용하지 않는다.

해제하려면 담당자가 승인한 문서와 실제 배포 API를 대조한 뒤 `type`, `policyVersion`, 정확한 API `text`의 SHA-256을 공개 승인 목록에 등록해야 한다. URL 방식이면 승인된 원문 URL 문자열을 해시한다. 같은 버전에서 본문/URL이 변경되어도 다시 확인할 때까지 차단된다. 런타임 환경변수나 Demo로 우회하는 경로는 없다.

**Major — 문서 계약:** 현재 `privacy`는 개인정보 수집·이용 동의 항목이며 전체 개인정보처리방침과의 대응은 미확정이다. 운영정책의 별도/통합 제공 방식도 미확정이다. API에는 제목·시행일·승인 상태 필드가 없다. 실제 게시 전 문서 대응과 승인 근거가 필요하며 이 작업에서는 BE·DB·정책 원문을 변경하지 않았다.

## 검증 및 한계

- 최종 결과: 관련 10개 테스트 파일 **229/229 통과**, `npm run typecheck` 통과, `git diff --check` 통과. HOME·DETAIL·SAVED·내 활동·문의 회귀 테스트를 포함한다.
- 모바일 Edge **375px·320px 모두 통과**, page error 0건. 초기 포커스와 중앙 정렬을 보완한 후 재검증했다.
- 정책 전용 단위 테스트: 본문/URL, 안전하지 않은 주소, 원문 불변성, 승인 없는 문서·seed 차단, 누락/중복/비정상 데이터, GET/재시도, 취소 후 늦은 401 억제.
- 모바일 브라우저: 375px·320px Edge의 MY 메뉴, 로딩·오류·재시도, 닫기·Escape·Back, 포커스, Demo 상태·스크롤 보존, API 호출 없음, 온보딩 본문 스크롤·URL·선택 유지. 모든 API는 loopback에서 가로채며 실제 계정에 요청하지 않는다.
- 승인 후 정상 문서의 선택/검증/렌더링은 단위 테스트의 합성 승인 근거로 검증한다. 앱의 실제 승인 목록을 열거나 승인된 정책이 배포됐다고 가정하지 않는다. 브라우저의 정상 본문/URL은 같은 공통 Dialog를 사용하는 온보딩에서 합성 데이터로 검증한다.
- 재현: `tests/pivot-policy-document.test.cjs`, `tests/pivot-policy-browser-check.cjs`. 브라우저 도구는 기존 설치를 `PIVOT_PLAYWRIGHT_MODULE`로 지정한다. 결과·스크린샷은 무시된 `agent/out/pivot-policy/`에 기록한다.
- 로컬 서버는 기존 node_modules 링크에 대한 Turbopack 오류를 피하기 위해 `next dev --webpack`을 사용했다. 저장소 빌드 설정은 변경하지 않았다.
- 전체 FE 테스트·Production Build는 요청대로 통합 전 최종 HEAD에서 한 번 수행하도록 남겨둔다. Commit·Push·PR·Merge·수동 배포는 수행하지 않는다.
