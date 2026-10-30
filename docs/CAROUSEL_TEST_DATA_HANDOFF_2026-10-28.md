# 로컬 캐러셀 테스트 데이터와 실제 콘텐츠 교체 — 2026-10-28

대상은 `codex/service-integration`의 전용 로컬 DB `carousel_preview`다. 사용자는 화면 확인용 **3개 캐러셀(3장·5장·10장)** 적재를 허용했다. 실제 에이전트 생성·독립 검수는 0건이며 LLM 실행과 키 사용은 보류한다. 테스트 자료는 실제 뉴스·공식 검수 완료 콘텐츠로 표시하지 않는다.

## 현재 상태

| 구분 | 상태 |
| --- | --- |
| 실제 작품 카탈로그 | 30개 준비 |
| 실제 에이전트 콘텐츠 | 0개 |
| 공식 기사 후보 / 수집 | 후보 5개, 그중 3개 페이지 수집 완료. 모두 미검증 자료 |
| 테스트 캐러셀 | 콘텐츠 3개·PNG 18개 적재. 기존 디자인 복원 후 모바일·데스크톱 재검증 완료 |
| 실제 소셜 로그인·신규 가입 E2E | 후속 실제 OAuth 모드에서 사용자가 Kakao 로그인·V2 가입 완료, 이후 서비스 UI·로그아웃 검증 통과. Naver는 폼 진입만 확인. [전체 흐름](SERVICE_FLOW_E2E_2026-10-28.md) 참조 |

접속 주소, 시작·종료, 사용자 확인 순서는 [로컬 미리보기](LOCAL_CAROUSEL_PREVIEW_2026-10-28.md)를 따른다. 전용 helper는 온보딩 완료 테스트 계정의 세션을 발급하며 그 자체를 실제 소셜 로그인·관심사 입력 검증으로 간주하지 않는다. 아래 runtime·결과·이미지·helper는 `C:/Users/USER/.codex/worktrees/service-integration/me_nu`의 ignored 자료다. 서비스 반영 후보에는 복사하지 않았으므로 실행 명령은 통합 작업 공간을 기준으로 사용한다.

## 테스트 자료의 식별과 재실행

| Content ID | 테스트 작품 | 장수 | 정확한 request ID |
| --- | --- | --- | --- |
| 1 | 별빛 우체국 | 3 | `synthetic-preview-20261028-post-office-3` |
| 2 | 구름 열차 | 5 | `synthetic-preview-20261028-cloud-train-5` |
| 3 | 달빛 극장 | 10 | `synthetic-preview-20261028-moon-theater-10` |

세 자료의 원본 package에는 `isTestData=true`, `fixtureNamespace=local-carousel-preview-20261028`이 있다. 제목·카드 문구·상세·출처 제목에 `[테스트]`를 표시하고 실제 에이전트 생성·LLM 검수 결과가 아니라고 밝힌다. 출처 링크는 `http://localhost:8211/__preview__/fixture-sources/<slug>`의 테스트 설명 페이지다. 테스트 자료의 `menu_work_ids`는 비어 있어 실제 작품 카탈로그와 잘못 연결하지 않는다.

최초 apply는 `created=3`, 같은 입력 재실행은 `unchanged=3`이며 기존 콘텐츠는 변경되지 않았다. 만료는 `2026-11-26T18:30:51+00:00`으로 고정한다. 증거는 Git에서 제외된 `frontend/out/actual-carousel-preview/test-fixtures/import-report.json`이다. 같은 폴더의 `bundle.json`, `synthetic-preview-review.json`, `fixture-metadata.json`, `sources.json`, `cards/`, `fixture-sources/`에 입력과 PNG·출처 설명을 보존한다.

`synthetic-preview-review.json`은 사용자가 허용한 테스트 화면의 표시·구조를 위한 별도 기록이다. 실제 뉴스의 사실 검증이나 LLM 독립 검수 통과를 의미하지 않으며 실제 데이터에 재사용하지 않는다.

```powershell
# 저장소 루트. 아래 스크립트는 전용 localhost:63362/carousel_preview만 허용한다.
$previewPython = 'C:\Users\USER\me_nu\backend\.venv\Scripts\python.exe'
Push-Location 'C:/Users/USER/.codex/worktrees/service-integration/me_nu/frontend/out/actual-carousel-preview'
try {
    & $previewPython ./seed_test_carousels.py          # DB dry-run
    & $previewPython ./seed_test_carousels.py --apply  # 명시한 테스트 3개 적재
} finally {
    Pop-Location
}
```

스크립트의 기본 실행은 DB dry-run이며 fixture 파일을 만들거나 검증할 수 있다. 기존 fixture의 검토 시각·만료·digest를 유지해 같은 request의 내용을 임의 갱신하지 않는다. 테스트를 숨긴 뒤 다시 보이게 하기 위한 수단으로 seed를 사용하지 않는다.

## 복원 전 UI의 화면 검증 기록

Chrome 390×844·1280×900에서 다음 항목을 확인했고 페이지 오류는 0개였다. 근거는 `frontend/out/actual-carousel-preview/carousel-browser-results.json`과 같은 폴더의 viewport 스크린샷이다.

- 3장·5장·10장 캐러셀과 테스트 표기, 쿠키를 포함한 보호 PNG 및 `private/no-store` 응답
- 키보드 탐색, 모바일 터치, 긴 카드 문구의 세로 스크롤
- 각 상세의 독립 본문, localhost 테스트 출처 페이지의 HTTP 200·새 탭 열기
- 북마크를 저장한 뒤 새로고침해도 유지되는 상태, 저장 목록의 PNG 표지
- 홈으로 돌아온 뒤 슬라이드 위치 복원 오차 1px 미만

데스크톱 터치는 검사 대상이 아니며 모바일에서만 확인했다. 저장 표지는 기존 UI의 PNG 썸네일로, 홈의 HTML 텍스트 오버레이를 합성한 이미지가 아니다. 세션은 로컬 helper로 발급했으므로 이 결과에 실제 OAuth·신규 가입·온보딩·관심사 저장 검증을 포함하지 않는다. 실제 에이전트 생성·검수 결과도 아니다.

## 기존 디자인 복원 후 확인 결과

같은 테스트 데이터·DB·API를 유지하고 홈·상세의 표시만 원래 구성으로 복원했다. 홈에서는 3장·5장·10장을 원래 비율의 카드 안에서 넘기며 우측 세로 반응·`자세히`·하단 pagination을 사용한다. 상세에서는 첫 장의 보호 PNG와 텍스트만 표지로 쓰고 곧바로 흰 독립 본문을 읽는다. 전체 `slides[]`와 실제 내용·출처·만료 계약은 바뀌지 않는다. 출처·게시일·AI 고지는 본문 하단에 남긴다.

복원 후 Chrome **390×844·1280×900**에서 홈 조작 영역의 겹침 없음, 단일 표지와 흰 본문의 연결, 상세 탐색 UI 제거, 보호 이미지·키보드/모바일 터치·긴 문구 스크롤·출처·저장·뒤로가기를 재검증했고 페이지 오류는 0개였다. 새 결과 `frontend/out/actual-carousel-preview/design-restoration-browser-results.json`과 `design-restored-*.png`를 추가했으며 앞 절의 결과는 보존했다. 당시 단위 테스트 294개·타입 검사·webpack 빌드·서버 health도 통과했다. 세부 원본 기준과 최종 결과는 [프론트 캐러셀 기록](FRONTEND_CAROUSEL_INTEGRATION_2026-10-28.md)을 따른다. 이 복원 단계는 OAuth 검증을 포함하지 않았고 후속 실제 Kakao 검증은 [서비스 흐름 보고서](SERVICE_FLOW_E2E_2026-10-28.md)에 따로 기록했다. 실제 에이전트 콘텐츠 0개·LLM 호출 0회는 그대로다.

## 실제 콘텐츠로 교체할 때

1. 소유권·과금 주체가 확인된 실행 자격 증명이 준비된 뒤 기존 에이전트 코드와 설정을 유지한 격리 실행으로 생성한다. 원문·팩트·상세 본문·출처·PNG·독립 검수 결과를 보존한다. 이미지 누락, 검수 실패, 만료, 내용 불일치가 있으면 중단한다. 점수나 `ready` 문자열을 근거로 강제 승인하지 않는다.
2. 실제 검토 대상의 본문·카드 텍스트·레이아웃·PNG·출처·만료 시각을 고정한 export bundle을 만든다. export 이후 수정한 PNG나 `render-manifest` 결과를 이전 검수본으로 취급하지 않는다. 패키지와 실제 PNG 해시를 함께 묶는 서비스 digest를 확인한다.
3. 기존 [캐러셀 importer](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md#3-적재-경계)를 기본 dry-run으로 실행한다. 입력·DB 대상·해시·장수·이미지 규격·출처 참조·만료를 확인하며 아직 DB를 변경하지 않는다.
4. 해당 고정본을 운영자가 실제로 검토한 별도 review record를 명시적으로 제공한다. 기록에는 digest, decision, reviewedBy, reviewedAt, comment가 필요하다. 테스트 fixture용 기록을 실제 콘텐츠에 재사용하거나 producer 점수로 자동 생성하지 않는다. 기록이 없으면 서비스는 보류 상태로 저장하며 공개하지 않는다.
5. review record를 포함한 dry-run 결과를 검토한 뒤 같은 입력으로 `--apply`를 실행한다. `--expected-database carousel_preview`로 대상을 고정한다. 공개 가능한 실제 콘텐츠의 홈·상세·PNG·출처·만료·저장 상태를 확인한다.
6. 실제 콘텐츠 확인이 끝난 뒤, 이번 테스트의 정확한 request ID·`isTestData=true`·`fixtureNamespace`가 모두 일치하는 active 자료만 `hidden`으로 전환한다. 데이터를 삭제하거나 DB를 초기화하지 않는다. 숨김 계획은 먼저 미리보기로 확인하며 다른 콘텐츠와 사용자 데이터는 대상에서 제외한다.

현재 importer는 기본 dry-run이고 `--apply`만 변경한다. 승인 없는 적재는 Content `pending_review/needs_review`와 Draft `checking`으로 보류한다. 별도 검토 기록과 digest가 일치하는 승인만 공개 상태로 전환한다. 해시는 파일 일치성을 검사하며 검토 행위를 증명하거나 과거 LLM 검수 증거를 만들어내지 않는다.

### 실제 에이전트 결과의 stage

로컬 helper `frontend/out/actual-carousel-preview/stage_agent_result.py`는 **파일 준비만** 수행한다. 생성·LLM 호출·DB 적재·승인 기록 작성은 하지 않는다. 결과 JSON과 원래 내보낸 `card_01.png` 등의 폴더를 받아, 현재 미리보기 폴더 아래 새 디렉터리에 bundle과 PNG 사본을 만든다. 기존 대상 폴더를 덮어쓰지 않는다.

```powershell
# frontend/out/actual-carousel-preview 디렉터리, 위 $previewPython 사용
& $previewPython ./stage_agent_result.py --result <실제_생성결과.json> --cards <원본_PNG_폴더> --out ./staged/<새_이름>
```

입력은 `ready_for_feed`, `review_mode=llm`, `editorial_decision=approved_for_feed`, 검수 `pass_candidate`, 언어 검토와 review 단계 사용량 기록이 있어야 한다. hard blocker 또는 error/critical QA가 남았거나 합성 테스트 표시가 있으면 거절한다. 결과 JSON은 한 번 읽은 바이트를 파싱과 해시에 함께 사용한다. 각 PNG의 실제 SHA-256이 카드의 `rendered_image_key`와 일치해야 하며, 이어서 기존 importer의 bundle·이미지·출처·만료 검증을 통과해야 한다. 임시 폴더에서 검증한 뒤 최종 폴더로 옮겨 검증 실패 시 불완전한 최종 폴더를 남기지 않는다. 이를 위해 원본 에이전트 결과나 검수 필드를 수정해서 통과시키지 않는다.

성공 결과는 `staged_not_published`이며 `bundle.json`, `cards/`, `review-context.json`을 만든다. review-context에는 digest·장수·별도 운영자 검토 필요 여부를 기록하며 **승인 record가 아니다**. 명시적 운영자 검토 전에는 다음 apply로 넘어가지 않는다. 실제 생성이 보류된 현재 시점에는 실제 검수 결과의 stage 성공을 확인하지 않았다.

```powershell
# backend 디렉터리. 로컬 전용 DB 연결은 격리 실행기의 설정을 사용한다.
python -m app.seeds.carousel_contents --file <bundle.json> --expected-database carousel_preview
python -m app.seeds.carousel_contents --file <bundle.json> --expected-database carousel_preview --review-record-file <review.json>
python -m app.seeds.carousel_contents --file <bundle.json> --expected-database carousel_preview --review-record-file <review.json> --apply
```

이번 로컬 환경의 `frontend/out/actual-carousel-preview/import_reviewed_bundle.py`는 격리된 DB 설정을 사용하며 위 importer를 감싼다. `--bundle`은 필수, `--reviews`는 별도 기록, 기본은 dry-run이다. 이 helper는 실제 LLM 검수의 결과·언어 검토·사용량 기록과 차단 오류가 없는지 추가 확인한다. apply에는 모든 항목의 정확한 digest에 대한 별도 `approved` 기록도 필요하다. 기록을 스스로 만들거나 검수 실패를 승인으로 바꾸지 않는다.

```powershell
# frontend/out/actual-carousel-preview 디렉터리, 위 $previewPython 사용
& $previewPython ./import_reviewed_bundle.py --bundle <bundle.json> --reviews <review.json>
& $previewPython ./import_reviewed_bundle.py --bundle <bundle.json> --reviews <review.json> --apply
# 실제 홈·상세 확인 후 숨길 테스트 ID를 먼저 확인한다.
& $previewPython ./import_reviewed_bundle.py --bundle <bundle.json> --reviews <review.json> --hide-test-data
& $previewPython ./import_reviewed_bundle.py --bundle <bundle.json> --reviews <review.json> --hide-test-data --apply
```

`--hide-test-data`의 결과는 `testContentIdsToHide`로 확인한다. apply 시 새 실제 콘텐츠가 모두 서비스 공개 조건을 만족하는지 검사한 뒤 식별된 테스트만 숨긴다. 실제 import와 테스트 숨김은 같은 transaction에서 처리되므로 실패하면 함께 rollback한다. 현재 실제 bundle이 없어 이 교체 작업은 실행하지 않았다.

### 교체 도구의 차단 검증

`frontend/out/actual-carousel-preview/handoff-guard-results.json`에 **9개 차단 검사 통과**를 기록했다. 실제 import 경로의 테스트 bundle 혼입·실제 검수 자료 누락·운영자 승인 없는 apply 및 테스트 숨김을 DB 연결 전에 거절했다. stage 경로의 테스트 표시·검수 자료 누락·이미 존재하는 출력 폴더·PNG 해시 불일치도 거절했으며, 잘못된 bundle이 최종 출력 폴더를 남기지 않는 것을 확인했다. 이 검사에서 DB 연결과 LLM 호출은 각각 0회였으며 helper 스크립트 3개의 문법 검사도 통과했다. 실제 에이전트 생성부터 검수·stage·게시까지의 전체 실행은 아직 하지 않았다.

## 적용 범위

실제 에이전트 코드·출처 설정·원본 생성 DB, 기존 서비스 DB, 생년월일 정책은 변경하지 않는다. main 머지·푸시·배포는 포함하지 않는다. 로컬 서버와 전용 DB는 사용자의 화면 확인을 위해 유지하며, 런타임 비밀값·세션·생성 산출물은 Git에서 제외한다.
