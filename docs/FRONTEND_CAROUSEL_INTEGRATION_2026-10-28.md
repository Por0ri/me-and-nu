# Pivot 홈·상세 캐러셀 연결

기준: 2026-10-28, `codex/service-integration` 작업 공간. 최신 프론트 홈 API 변경을 선별 반영하고 서비스 DB에 저장된 카드 이미지·텍스트·상세 본문을 Pivot에 연결하는 후속 기록이다. [DB·API 및 적재 계약](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md)과 [온보딩 연결 기록](ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md)을 함께 따른다.

현재 소스는 API 연결을 유지하면서 원래 홈·상세 구성을 복원했다. 홈에서 캐러셀을 탐색하고 상세는 첫 표지 한 장과 바로 이어지는 독립 본문을 표시한다. 단위 테스트·타입 검사·빌드와 모바일·데스크톱 브라우저 재검증을 통과했다. 3절은 복원 전 최초 연결 검증 기록이며, 이번 복원과 최종 결과는 5절에서 구분한다.

에이전트의 생성·렌더·검수 코드, 원본 생성 DB와 기존 산출물은 이번 프론트 작업에서 수정하지 않는다. 생년월일 정책은 보류하며 main 병합·푸시·배포나 실제 서비스 DB 적용을 포함하지 않는다.

## 1. 프론트 기준선

| 커밋 | 커밋 시각(KST) | 역할 |
| --- | --- | --- |
| `cbbd75b` | 2026-10-27 12:00:56 | 회원 로그인·저장 콘텐츠 흐름의 기존 기준선 |
| `3f656eb` | 2026-10-27 14:53:33 | 이후 추가된 Pivot 홈 피드 API 연결. `home-route`, `home-service`, 홈 모델과 API 타입, 관련 테스트 변경 |

두 시각은 Git 커밋의 작성·커밋 시각이다. GitHub 연결의 브랜치 목록에서 원격 `feature/pivot-user-fe`의 최신 tip이 `3f656eb`임을 확인했고, 로컬 `origin/feature/pivot-user-fe`의 해시와 일치함도 확인했다. `3f656eb`의 전체 브랜치를 덮어쓰지 않고 필요한 홈 연결 변경을 현행 온보딩 V2·세션·북마크 구현과 맞춘다. 원격에 아직 없는 개인 작업까지 최신이라고 보장하는 기록은 아니다.

기존 홈 어댑터는 피드의 `imageUrl`과 `summary`로 슬라이드 한 장을 만들었고, 상세는 대표 이미지와 `body`/`excerpt`를 표시했다. DB·API 단계에서 추가한 `slides[]`, 카드별 배치, 보호된 상대 이미지 URL과 전체 출처 목록을 이번 홈·상세 어댑터에 연결한다.

## 2. 구현 파일과 서비스 계약

| 구현 | 역할 |
| --- | --- |
| [home-route.tsx](../frontend/components/pivot/home-route.tsx), [home-service.ts](../frontend/lib/pivot/home-service.ts) | 사용자별 홈 source를 만들고 응답 대기 중 세션·사용자가 바뀌면 이전 응답을 사용하지 않는다. 인증·권한·네트워크·응답 형식·서버 오류를 구분한다. |
| [api.ts](../frontend/lib/api.ts) | 공개 슬라이드·배치·출처 DTO를 추가한다. 취소된 요청에서 뒤늦게 도착한 401이 현재 세션을 만료시키는 이벤트를 발생시키지 않도록 한다. |
| [carousel-data.ts](../frontend/lib/pivot/carousel-data.ts) | API 카드·출처·발행 메타데이터 검증. 3~10장의 연속 page, 1308×1632 크기, 텍스트·정규화 layout, 콘텐츠/Topic/page에 정확히 맞는 보호 이미지 경로를 확인한다. |
| [content-carousel.tsx](../frontend/components/pivot/content-carousel.tsx), [스타일](../frontend/components/pivot/content-carousel.module.css) | 홈의 `ContentCarousel`과 상세의 단일 `ContentCover`, 보호 PNG 요청·수명 관리, 카드 텍스트 오버레이를 제공한다. 이동·위치 조작은 홈에서 제공한다. |
| [home-screen.tsx](../frontend/components/pivot/home-screen.tsx), [홈 스타일](../frontend/components/pivot/home.module.css) | 기존 비율의 카드 안에 캐러셀·우측 세로 반응·자세히·pagination을 배치한다. 기존 demo·비캐러셀 경로는 유지한다. |
| [detail-service.ts](../frontend/lib/pivot/detail-service.ts), [detail-screen.tsx](../frontend/components/pivot/detail-screen.tsx) | 첫 표지와 독립 상세 본문을 연결하고 본문 하단에 출처·발행 메타데이터·AI 고지를 둔다. 북마크 상태를 유지한다. |
| [saved-service.ts](../frontend/lib/pivot/saved-service.ts), [saved-screen.tsx](../frontend/components/pivot/saved-screen.tsx) | 저장 목록의 캐러셀 표지를 같은 콘텐츠/Topic의 page 1 보호 이미지로 연결한다. |

- 홈은 활성 세션과 본인의 활성 Topic을 확인한 뒤 `GET /api/v1/topics/{topicId}/feed`를 사용한다. 활성 anime Topic을 우선하며 콘텐츠 ID·Topic ID·서버의 북마크 상태를 유지한다. `3f656eb`의 첫 페이지만 조회하는 동작으로 되돌리지 않고 기존 최대 10페이지·페이지당 100개 제한, 중복 콘텐츠 제거와 반복 cursor 차단을 유지한다. 데모 데이터를 서비스 응답으로 대신하지 않는다.
- 홈 캐러셀은 `slides[]`의 `page` 순서, `titleLines`, `bodySentences`, `eyebrow`, `layout.textOverlayBox`와 PNG를 함께 사용한다. 상세는 같은 배열의 첫 장만 표지로 표시하며 상세 내 캐러셀 탐색 UI는 두지 않는다. API의 슬라이드 배열과 3~10장 검증 계약은 유지한다. PNG는 글자를 포함하지 않는 카드 이미지 판이므로 구조화된 텍스트를 별도로 표시해야 한다. HTML 문자열을 실행하거나 카드 문장을 이어 붙여 상세 본문을 만들지 않는다.
- 이미지 경로는 `/api/v1/contents/{contentId}/slides/{page}/image?topicId=...`이다. 프론트와 다른 API origin을 기준으로 해석하고 현재 세션을 포함한다. 원본 출처 URL이나 producer BLOB 키로 대체하지 않는다.
- 상세는 기존 `GET /api/v1/contents/{contentId}?topicId=...`의 독립 `body` 또는 `excerpt`, `sources[]`, `notices[]`를 사용한다. `displayMode=link_excerpt`의 원문 이동과 슬라이드 없는 기존 글을 유지한다.
- 서비스 이미지·상세·북마크의 인증과 공개 가능 여부는 서버 계약을 따른다. 미검토·거절·만료·불완전 캐러셀의 공개를 프론트에서 우회하지 않는다.

보호 PNG는 `credentials:include`, `cache:no-store`, `redirect:error`로 가져와 `image/png`와 1바이트 이상·20MiB 이하인지 확인한 뒤 컴포넌트 소유 blob URL로 표시한다. 화면 근처 300px 영역에 들어온 현재 장·인접 장만 요청하며, 영역을 벗어나거나 URL 변경·컴포넌트 해제 시 요청을 취소하고 blob URL을 폐기한다. 다시 돌아올 때 폐기된 URL을 재사용하지 않도록 로딩 상태와 현재 유효 URL을 함께 확인한다. 인증된 이미지를 사용자 사이에 공유하는 전역 캐시는 두지 않는다. 401은 세션 만료 처리로 연결하고, 404·전송 실패·이미지 디코딩 실패는 안내와 재시도를 제공한다. 저장 목록 표지도 같은 로더를 사용하되 카드 버튼 안에 재시도 버튼을 중첩하지 않으며 상세에서 이미지를 다시 시도할 수 있다.

PNG는 비율을 유지해 표시하고 `textOverlayBox`를 기준으로 eyebrow·제목 줄·본문 문장을 React 텍스트로 배치한다. 긴 문구는 배치 영역 안에서 스크롤할 수 있다. 홈은 좌우 터치, 이전·다음·위치 버튼, 방향키·Home·End를 지원하며 현재 장의 번호를 알린다. 상세 출처는 안전한 HTTP(S) 링크만 중복 제거·인용 순서 정렬해 표시하고, 새 탭에는 `noopener noreferrer`를 사용한다. 이 동작의 코드 반영과 실제 브라우저 검증 결과는 구분한다.

현재 DB target은 캐러셀 `f4b8c1d9a620`에 추천 연결 표 4개를 더한 `150a601074af`이다. 이 프론트 연결을 위해 생년월일 삭제 분기나 별도 에이전트 migration을 적용하지 않는다.

## 3. 최초 연결 검증 기록 — 디자인 복원 전

아래는 홈·상세에 공통 캐러셀을 적용했던 이전 UI의 검증이다. 기존 디자인 복원 후 결과는 5절에 별도로 기록한다.

| 검증 | 확인한 결과 |
| --- | --- |
| 프론트 `npm test` | **293개 통과, 실패 0·skip 0** |
| `npm run typecheck` | 통과 |
| `npm run build -- --webpack` | 로컬 프로덕션 빌드 통과(exit 0) |
| 실제 Chrome 모바일 390px·데스크톱 1280px | 3장·10장 캐러셀과 기존 글 동시 표시, 실제 쿠키 PNG, 키보드·터치 탐색 통과. 스크롤이 실제 페이지 경계에서 1px 미만 오차로 정착할 때까지 기다리는 강화 검사도 통과 |
| 상세·이동·저장 | 독립 본문·출처 2개·AI 고지·게시일, 실제 북마크 저장·새로고침·저장 목록 표지, 뒤로가기 후 홈 슬라이드 위치 유지 통과 |
| 이미지 오류·수명 | 404 → 명시적 재시도 → PNG 복구, End/Home 재진입, 이미지 401 → 로그인 이동·보호 blob URL 전부 폐기 통과 |
| 세션·화면 오류 | 로그아웃 후 보호 콘텐츠 제거, 브라우저 `pageerror` 0건 |

Windows Next 개발 서버가 `.next/dev/server/app/pivot/home/page.js`를 여는 중 `UNKNOWN` 오류로 중단되어 `npm run build -- --webpack` 후 로컬 `start`로 브라우저 검증 환경을 전환했다. 위 최종 브라우저 결과는 이 프로덕션 빌드를 로컬에서 실행한 결과이며 배포가 아니다. 1280px에서 전체 페이지 스크린샷 촬영이 스크롤 트랙을 재설정하는 검증 도구의 영향을 확인해 viewport 캡처로 고정했고, 제품의 뒤로가기 복귀 로직을 바꾸지 않은 상태에서 위치 복원을 확인했다.

이전 온보딩 단계의 프론트 **259개 통과**와 이전 캐러셀 DB·API 단계의 백엔드 **951 passed, 3 skipped, 1 warning**은 각 단계의 기록이다. 이번 홈·상세 캐러셀 화면이 이미 검증됐다는 근거로 사용하지 않는다.

브라우저 검증 환경은 Next `http://localhost:3331`, FastAPI `http://localhost:8211`, 별도 PostgreSQL 포트 `63362`의 `carousel_ui_test` DB다. 산출물은 Git에서 제외된 `frontend/out/carousel-e2e/`에 두며 기존 `agent/out/onboarding-v2-e2e/`는 변경하지 않는다. [브라우저 확인 스크립트](../frontend/tests/pivot-carousel-browser-check.cjs)는 mock API 서버 없이 합성 회원 세션으로 실제 서비스 API를 호출한다.

생성·검수 LLM을 실행하지 않고, 합성 3장·10장 카드와 기존 글 fixture로 서비스 적재→피드→상세→보호 이미지→북마크 경로를 확인했다. 결과는 `frontend/out/carousel-e2e/browser-results.json`과 모바일·데스크톱 화면 PNG에 남겼다. 실제 운영자 검토를 마친 원본 캐러셀이나 실계정 OAuth 공급자 검증으로 확대 해석하지 않는다. 합성 세션 토큰·DB 접속 비밀은 문서에 기록하지 않는다.

검증 후 Next·FastAPI를 종료하고 전용 `me-nu-carousel-ui-20261028` 컨테이너를 중지·제거했으며 `frontend/out/carousel-e2e/sessions.json`도 삭제했다. 포트 3331·8211·63362에 listener가 없음을 확인했다. 기존 `me-nu-postgres`(5433)와 Redis(6379)는 그대로 실행 중이며 이번 검증에서 변경하지 않았다.

## 4. 남는 적용 범위

작품·태그 선택 기반 개인화 순위, 실제 승인 산출물 선정·운영자 검토, 실서비스 데이터 적재, 배포와 실계정 OAuth 확인은 별도 적용 범위다. 원본 producer의 검수 상태와 저장 경합 문제를 이 프론트 연결로 수정했다고 간주하지 않는다. 원본 검수 점수만으로 발행하지 않는 서비스 importer의 보류·승인 경계를 유지한다.

## 5. API를 유지한 기존 디자인 복원 — 2026-10-28

디자인 기준은 `3f656eb`의 홈·상세 구성이다. 홈의 카드 위 이전·다음 버튼은 원래 존재했으며, 2026-10-21 18:57:32 KST의 `d5e6384`에서 이어진 구성이다. 기본 `opacity:0`에서 hover·키보드 focus 시 보이는 동작을 원본 홈 CSS로 재사용한다. 버튼이 원본에 없었다고 해석해 기능을 제거하지 않는다.

- 홈은 카드의 `327:408` 비율을 복원하고, 우측 세로 반응 버튼과 `자세히`를 카드 안에 둔다. 서비스용 자동 높이·카드 아래 metadata 행·가로 반응 행과 원본에 없던 추가 제목·흰 요약 상자는 제거한다.
- 이전·다음·현재 장수·점 표시에는 원본 홈 CSS를 사용한다. 최대 10개 점은 기존 하단 행 안에서 폭을 줄여 `자세히` 영역과 겹치지 않게 한다. 3장·5장의 시작점은 기존 52px을 유지하고, 두 자리 장수 표시가 있는 10장은 60px에서 시작해 카운터와 첫 점을 분리한다.
- API의 HTML 텍스트 오버레이와 실제 보호 PNG는 유지한다. 홈 텍스트 영역은 우측 52px·하단 48px을 예약해 반응·이동 조작을 가리지 않게 제한한다.
- 상세는 첫 슬라이드의 보호 PNG와 텍스트를 단일 표지로 표시한다. 표지 바로 아래에 원래의 흰 본문 영역(`margin-top:-8px`)을 이어 붙이고 별도 큰 제목은 시각적으로 숨긴다. 상세의 슬라이드 이동·장수·점 UI는 제거한다.
- 상세의 독립 본문은 API 값을 유지하며 출처·게시일·AI 고지는 본문 하단 footer에 둔다. 북마크·뒤로가기·보호 이미지·피드 데이터 연결은 유지한다.

이번 복원은 프론트 표시 변경이다. 백엔드 DB·API·에이전트·생년월일 정책은 수정하지 않는다. 실행 중인 미리보기의 상태와 사용자 확인 순서는 [로컬 미리보기](LOCAL_CAROUSEL_PREVIEW_2026-10-28.md)를 따른다.

| 복원 후 검증 | 결과 |
| --- | --- |
| `npm test` | **294개 통과, 실패 0·skip 0**. 단일 표지 확인 1개 추가 |
| `npm run typecheck` | 통과 |
| `npm run build -- --webpack` | 통과 |
| 로컬 서버 재시작·health | 통과 |
| 실제 Chrome 390×844·1280×900 | 3장·5장·10장 홈 카드와 상세 단일 표지·독립 본문 확인 통과, 페이지 오류 0개 |
| 홈 디자인·조작 | `327:408` 비율, 버튼의 기본 숨김·focus 표시·데스크톱 hover 표시, 카드 안 조작·우측 세로 반응 통과. 장수·점·텍스트·자세히 영역 겹침 없음 |
| 상세 디자인 | 첫 표지 1개, 흰 본문 `-8px` 연결, 큰 제목의 시각적 숨김, 본문 하단 출처·발행 고지 통과 |
| API 연결 회귀 | 쿠키 보호 PNG·`private/no-store`, 키보드·모바일 터치·긴 문구 세로 스크롤, 독립 본문·출처 HTTP, 저장 후 새로고침·저장 표지, 홈 복귀 위치 오차 1px 미만 통과 |

새 결과는 `frontend/out/actual-carousel-preview/design-restoration-browser-results.json`에 저장했고, 이전 `carousel-browser-results.json`은 보존했다. 대표 viewport 화면은 `design-restored-home-top-{390,1280}.png`, `design-restored-detail-{390,1280}.png`다. 테스트 콘텐츠 3개·실제 에이전트 콘텐츠 0개인 `carousel_preview` 환경에서 확인했으며 생성·검수 LLM은 호출하지 않았다. 이 복원 단계는 소셜 OAuth를 검증하지 않고 로컬 helper 세션을 사용했다. 후속 실제 Kakao 인증·가입·서비스·로그아웃 결과는 [서비스 흐름 보고서](SERVICE_FLOW_E2E_2026-10-28.md)로 구분한다. 로컬 API·프론트·전용 DB와 ignored 결과·이미지는 `C:/Users/USER/.codex/worktrees/service-integration/me_nu`에서 유지하며 서비스 반영 후보에는 런타임 폴더를 복사하지 않았다.
