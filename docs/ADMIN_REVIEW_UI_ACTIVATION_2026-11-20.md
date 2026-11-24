# Checking 관리자 검수 UI 활성화

사용자가 2026-11-20에 Checking 승인·반려 UI 활성화와 실제 화면 최종 검증을 승인했다. 기준은 원격 Checking `233b3bb7884c0ba16c4273dc7f6260617933ebe6`이다. 이번 변경은 `ACTIVATION`에 승인된 API origin과 승인 기록을 지정하며 기존 요청·권한·scope·CSRF·이탈 보호 로직을 변경하지 않는다.

`https://api.checking.me-nu.kr`를 사용하는 빌드에서만 쓰기 gate가 열린다. Main `https://api.me-nu.kr`, Test, Labs, localhost, 다른 scheme/port/host는 계속 차단된다. query·storage·환경변수로 새 origin을 승인하는 우회는 추가하지 않는다. 서버가 허용한 역할·콘텐츠 상태·scope를 매번 확인하며, 현재 grant가 없는 계정은 UI 활성화만으로 쓰기 권한을 얻지 않는다.

활성화 전 확인한 근거:

- [PR #93](https://github.com/Por0ri/me-and-nu/pull/93) Main 관리자 탈퇴 보호 배포와 [PR #94](https://github.com/Por0ri/me-and-nu/pull/94) Checking scope 계약 배포 완료. migration `47c1a8d6b302` 적용 상태 유지.
- 기존 사용자에 최초 admin grant를 부여한 뒤 실제 OAuth 세션으로 관리자 조회, 승인·반려, 동일 요청 재전송, 잘못된 grant의 409를 확인했다.
- 전용 합성 콘텐츠 2건의 감사가 각각 1건이며, 원 요청과 before/after가 일치했다. Main/Checking 공개 predicate 비노출, Checking 공개 상세 404, 전용 콘텐츠 정리와 감사 보존까지 확인했다.
- 이번 UI 검증 직전에도 양쪽 API 이미지·health·Main 보호·grant를 읽기 전용으로 재확인했다.

앞선 증거는 `out/admin-live-validation-20261120/RELEASE_VALIDATION.md`, `release-summary.json`, `live-review-ui.json`, `live-review-db-cleaned.json`에 보존한다. 이번 증거는 별도 `out/admin-ui-final-validation-20261120/`에 기록하며 사전 상태는 `api-readiness.json`에 있다. 이전 문서의 `ACTIVATION=null`은 그 단계의 검증 결과다.

실환경에서 확인한 역할은 admin 1명이다. viewer/operator·권한 회수·재부여·계정 전환·응답 유실·이탈 경고의 장애 경로는 격리 테스트 결과와 구분한다. 이번 사용자 승인은 그 제한을 유지한 Checking UI 활성화이며 다른 역할의 운영 검증 완료를 뜻하지 않는다.

배포 후 실제 UI 검증은 새 전용 합성 콘텐츠 2건에서 빈 사유 차단, 입력 확인, 취소 시 무변경, 승인 빠른 중복 클릭 후 감사 1건, 반려 후 상태·감사 1건, 목록·재조회·공개 404 및 정리를 확인한다. Main과 Checking은 DB를 공유하므로 기존 서비스 콘텐츠는 시험에 사용하지 않는다. 네트워크 장애나 권한 변경은 운영에 주입하지 않는다.

로컬 후보 검증은 관련 unit/HTTP 55/55, 실제 Edge 격리 패널 18/18, 독립 typecheck 및 Checking API origin의 production webpack build(정적 페이지 30/30)를 통과했다. 기본 registry가 Checking에서는 1회 실행하고 Main에서는 POST를 보내지 않는 경로를 포함한다. 390px 승인 완료 화면의 가로 넘침도 없었다. loopback fixture 외의 서비스 요청은 보내지 않았고 브라우저·서버는 종료했다. 제품 변경은 gate의 승인 상수·주석 1파일이며 함수 본문과 기존 안전성 로직은 유지했다. 독립 리뷰에서 추가 수정 사항은 없었다.

증거는 `out/admin-ui-final-validation-20261120/focused-tests.log`, `typecheck.log`, `production-build.log`, `browser/results.json`, `local-validation.json`에 있다. 이 로컬 결과를 배포·실제 UI 최종 검증 완료로 해석하지 않는다. UI 중단이 필요하면 `ACTIVATION=null`로 되돌리는 코드 변경을 검토한다. 관리자 이력이 있으므로 Main의 탈퇴 보호가 없는 이전 이미지로 되돌리지 않는다.
