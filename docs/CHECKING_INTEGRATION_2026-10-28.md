# Checking 통합 브랜치 — 2026-10-28

`Checking`은 작업 브랜치의 변경을 모아 QA한 뒤 `main` PR을 준비하는 통합 브랜치다. **사본을 제외한 실제 Checking 작업 공간에서 프론트 테스트·타입 검사·프로덕션 빌드를 통과했다.** 이후 사용자 요청으로 GitHub에 게시하고 팀 검증용 도메인과 전용 환경 연결을 진행했다. 이 문서는 통합 커밋의 범위와 검증 결과를 기록한다.

## 생성 기준과 범위

- 최신 원격 `main`의 `ca949ac`에서 시작해 검증된 로컬 `backend-labs`의 `a05483a`를 통합한다.
- `backend`, `frontend`, compose 파일은 `backend-labs`와 동일하게 유지한다. 에이전트 코드는 `main`과 동일하게 유지하며 기존 `codex/service-integration`의 에이전트 복원 변경을 가져오지 않는다.
- 중복 미리보기 `.preview-pivot` 251개 파일은 통합 결과에서 제외하고 루트 `.gitignore`에도 제외 규칙을 추가한다.
- 실행·빌드 코드에 사본 참조가 없다. 프론트는 `frontend/`를 사용하고 백엔드 Docker의 빌드 context는 `backend/`다. 기존 사본은 `backend-labs`와 과거 Git 이력에 보존된다.
- 원본 작업 공간과 보존된 미커밋 파일, 기존 `backend-labs` 참조를 유지한다. 이 작업은 `main`을 변경하지 않는다.
- 서비스 스키마는 `f4b8c1d9a620` 단일 head다. 생년월일 삭제 d2 파일을 포함하지 않으며 기존 컬럼·데이터를 보존한다. 수집 정책은 계속 보류한다.

## 검증 상태

| 구분 | 현재 상태 |
| --- | --- |
| 이번 Checking 프론트 테스트·타입 검사·빌드 | 297개 통과, TypeScript 검사 성공, Webpack production build 성공·정적 페이지 19개. 로그는 `frontend/out/checking-validation/` |
| 이전 서비스 후보 백엔드 검증 | 서로 다른 테스트 947개 통과. Redis 2개·Windows 1개 미실행. 이번 Checking에서 새로 실행한 결과는 아님 |
| 이전 실제 사용자 흐름 | Kakao 로그인·V2 온보딩 완료 후 피드·상세·북마크·로그아웃·재접근 차단 확인. Naver는 로그인 폼 진입만 확인 |
| 실제 에이전트 콘텐츠 | 생성·LLM 검수 보류. 로컬 화면 자료는 명시된 테스트 캐러셀 |
| 병합 기준 | 첫 부모 `ca949ac`(최신 원격 main), 병합 대상 `a05483a`(검증된 로컬 backend-labs). 이 문서가 포함된 커밋에서 사본 제외를 함께 기록 |

앞선 서비스 검증의 범위와 증거는 [서비스 흐름 보고서](SERVICE_FLOW_E2E_2026-10-28.md)를 따른다. 실행 중인 `localhost:3331` 서버는 기존 `C:/Users/USER/.codex/worktrees/service-integration/me_nu` 소스를 사용한다. ignored 런타임·DB 설정·이미지·검증 산출물은 Checking 작업 공간에 복사하지 않았다.

## 사용 절차와 보호 범위

작업 브랜치 → `Checking` 통합·QA → 검증한 결과로 `main` PR을 준비한다. 변경을 추가하면 해당 변경과 통합 결과를 다시 검증하고, PR에서는 실제 반영 파일·검사 결과·남은 제한을 확인한다.

브랜치 추가만으로 main의 실수 머지를 막을 수는 없다. main의 PR 필수, 검토 승인·필수 CI, 직접 push 제한은 별도 보호 설정으로 검토할 사항이며 **이번에는 설정하지 않았다.**

`Checking`은 `origin/Checking`을 추적한다. 사용자 요청에 따라 `97a2496` 통합 커밋과 `26d3410` 전용 배포 구성을 원격에 게시했다. Vercel Preview의 `Checking` 브랜치에는 `checking.me-nu.kr`과 `NEXT_PUBLIC_API_BASE_URL=https://api.checking.me-nu.kr`을 연결했다. Checking push는 프론트 자동 배포를 유발하므로 팀에 공유할 상태만 게시한다. 백엔드는 전용 서버 경로에서 검증한 이미지 SHA를 지정해 별도로 갱신한다.

`main`과 원격 `backend-labs` 참조 및 기존 환경은 변경하지 않았다. 실제 Checking 배포와 외부 접근 검증 결과는 [Checking 배포 기록](CHECKING_DEPLOYMENT_2026-10-28.md)을 따른다. 배포 절차와 초기화 보호 조건은 [Checking 배포 안내](../deploy/checking/README.md)에 있다.
