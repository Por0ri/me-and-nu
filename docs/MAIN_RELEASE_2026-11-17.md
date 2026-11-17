# 메인 서비스 전환 실행 (2026-11-17)

사용자가 검증 변경의 Checking 커밋·푸시, 복원 가능한 백업, 기존 공개 3,530건의
실제 일반 공개 전환, 메인 도메인 연결·배포를 승인했다. 자동 수집·유료 생성은
계속 비활성 상태로 유지한다. 일일 20건 목표의 예산/시도 상한은 이번 승인에 포함하지 않는다.

준비 검증은 [이전 기록](MAIN_CONNECTION_PREPARATION_2026-11-17.md)을 따른다.
이 문서는 그 이후의 운영 전환 기록이며, 이전 문서의 '아직 운영 적용하지 않음'은
준비 단계 당시 상태다.

## 승인 범위와 실행 경계

- 대상은 공개 ID SHA-256
  `59d795b06e5cbf43b718c820bd70cfbfcfb69e50161c96eeb482af486396dded`의 정확한 3,530건이다.
- 기존 as_of `2026-11-17T00:44:31.246551+00:00`와 원본 자료로 재구성한 manifest는
  `1447100eba9050097cc75ba0e02c414070b07207150486d7fd83a6f817c2da38`와 일치해야 한다.
  변경·만료·새 대상이 확인되면 승인 범위를 자동으로 넓히지 않는다.
- `apply_checking_publication_plan`은 정확한 Checking PostgreSQL 주소/DB/계정,
  위 고정 ID 집합, 별도 전달한 manifest SHA, 명시 실행 문구와 복구 검증 receipt를
  요구한다. 기존 `apply_isolated_plan`의 테스트 DB 제한은 그대로 유지한다.
- receipt의 선언만 신뢰하지 않는다. 운영 하니스가 서버 내부 백업 파일의 권한·SHA와
  실제 격리 복원 결과를 확인한 다음 전달한다. 회원 개인정보/PNG 원본을 로컬로 반출하지 않는다.
- 전환은 Content/Draft 상태와 기존 JSON 안의 감사 이력에 한정한다. 회원·추천·작품 연결·
  이미지·출처·검수 로그를 다시 만들거나 삭제하지 않는다. 새 품질 검수 수행으로 기록하지 않는다.

운영 가드 포함 전환 회귀 **66건 통과**. 기존 추천/세션/로그인 205건 + 11 subtests,
메인 설정 10건, FE 645건(기존 skip 1), production build/typecheck 검증을 유지한다.

## 배포 연결

- 기존 canonical `me-nu.kr → www.me-nu.kr` 유지, API는 `api.me-nu.kr`.
- Vercel Production만 `NEXT_PUBLIC_API_BASE_URL=https://api.me-nu.kr`를 사용한다.
  Checking Preview의 API 주소는 변경하지 않는다.
- main은 Checking의 ancestor가 아니지만 main 고유 커밋은 PR18 merge 한 개이고,
  그 tree가 공통 조상과 동일하다. 정상 merge 후 최종 tree가 Checking와 동일한지
  검증한 뒤 일반 push로 Production을 승격한다. 강제 push하지 않는다.
- 메인 API는 기존 Checking DB/Redis, 별도 쿠키/prefix, 풀 2/overflow 0,
  loopback 8003만 사용한다. Worker/Beat 또는 LLM 키를 함께 활성화하지 않는다.

백업/전환/배포 결과는 실제 확인이 끝난 뒤 이 문서에 추가한다.
