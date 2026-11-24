# Checking 작업의 Main 통합

## 범위와 현재 상태

2026-11-24 사용자는 Checking 작업을 Main에 반영하되 **Checking 브랜치 정리 직전까지** 진행하도록 요청했다.
이 후보는 Main `460b427debf2ba61c31bba3d0ad86e5bb6019da2`와 Checking
`6bccf07591ad94c65bf61a43ea0ddfdfb32d5df2`를 실제 병합한 결과를 바탕으로 한다.
원래 `backend-labs` 작업 폴더와 사용자의 미추적 파일은 수정하지 않았다.

이 문서는 운영 반영 완료 기록이 아니다. 서버 SSH와 로컬 Docker daemon은 실행 환경의
접근 제한으로 사용할 수 없었다. 실제 PostgreSQL 검증, 서버 변경, Main 프론트 배포,
운영 작업 인계, Checking API 종료 및 Git 브랜치 삭제는 완료되지 않았다.

Main 병합으로 Vercel 운영 프론트가 배포될 수 있으므로, 아래의 API·DB 준비가 끝나기 전에는
통합 PR을 병합하지 않는다. 사용자 요청은 진행 권한이며 실제 검증 결과를 대신하지 않는다.

## 보존하는 자원

| 자원 | 처리 |
|---|---|
| `me-nu-checking-postgres-1`, `me_nu_checking`, `menu_dict` | 기존 운영 저장소로 유지 |
| `me-nu-checking-redis-1`, Redis DB 0 | 유지; Main prefix·cookie도 유지 |
| `me-nu-checking_data` 및 PostgreSQL/Redis 볼륨 | 유지; 이름만 보고 삭제하지 않음 |
| 기존 Checking API·추천기·timer | 각 기능의 Main 인계가 검증될 때까지 보존 |
| 기존 평가 관측·HMAC·미처리 결과·백업 | 경로와 내용 유지; 새 수집기와 동시 실행 금지 |
| Checking Git 브랜치 및 미병합 PR #111 | 유지; #111의 RAG pgvector 변경은 이 후보에 포함하지 않음 |

전체 DB 복사·초기화·테스트 데이터 삭제, 생성 워커·유료 자동 수집 활성화는 포함하지 않는다.
별도로 준비했던 `compose.checking-isolated.yaml`은 이번 통합에 사용하지 않는다.

## 직접 확인한 운영 상태

사용자가 MobaXterm에서 2026-11-24 조회하여 전달한 값:

- DB revision: `b7d4e2a9c601`.
- 루트 디스크: 전체 154G, 사용 142G, 남음 13G, 사용률 92%.
- 메모리: 전체 7.6Gi, available 4.8Gi, swap 없음.
- 앞서 제공된 컨테이너 목록: Main API 이미지 `e939090…`(8003), Checking API 이미지
  `57056bf…`(8002), 기존 추천기 및 PostgreSQL/Redis 실행 중.
- 실제 Main checkout: `/home/ubuntu/apps/me-nu-main-20261118`.
- 실제 Checking checkout: `/home/ubuntu/apps/me-nu-checking`.
- Checking 공지·평가 timer: 두 항목 모두 `enabled`, `active`.

새 서비스의 목표 revision은 `94d2da693c10`이다. 기존 DB에서는
`a67ddf3dc55f`(토론 테이블)와 `94d2da693c10`(알림 종류 확장) 두 단계가 남는다.
새 토론 기능은 기본 비활성 상태로 유지한다. 기존 데이터가 들어간 뒤의 임의 downgrade는
마이그레이션 자체에서도 거부될 수 있으므로, API 롤백과 DB 되돌리기를 혼동하지 않는다.

## 코드 변경과 검증

- 회원 탈퇴 충돌 3개 해결: Checking 관리자 ORM과 Main의 DB 오류 시 rollback·503·세션 보호를 함께 유지.
- Main 테스트의 수동 관리자 테이블 생성 제거: 실제 마이그레이션 스키마와 중복되지 않도록 정리.
- 관리자 승인·반려: Main과 Checking의 명시된 HTTPS API 주소만 허용. 서버 권한·CSRF·버전 검사는 유지.
- Main 배포 설정: 기존 저장소 유지, 평가 집계 파일만 읽기 전용 연결, 토론 기본 비활성.
- Main 추천·공지·평가 도구: 기존 작업 중지 상태와 실제 서비스 DB revision을 확인한 뒤 실행.
  운영 명령 및 평가 상태 보존 방법은 [OPERATIONS.md](../deploy/main/OPERATIONS.md)에 있다.
- `deploy/main/preflight.py`: 환경변수·접속 문자열·Docker 명령·오류 로그를 출력하지 않는 읽기 전용 현황 조회.
  `runtimeChecksPassed`와 종료 코드 0은 관측 성공만 뜻하며 배포 승인, 백업 또는 사용자 기능 검증을 뜻하지 않는다.

검증 결과는 통합 PR 본문과 최종 작업 보고에 함께 기록한다. 테스트를 건너뛴 항목을 통과로 계산하지 않는다.

현재 확인된 검증 결과:

- 프론트 전체: 818 통과, 1개 선택적 로컬 Figma 자료 검사 건너뜀. 타입 검사와 운영 빌드 성공.
- Main 배포·작업 도구, preflight, 임시 PostgreSQL 실행 도구: 85 통과.
- 탈퇴·인증 관련 백엔드: 76 통과, 실제 PostgreSQL 전용 13개 미실행.
- 백엔드 최종 전체 검사: 2,154개 중 2,023 통과, 12 실패, 119 건너뜀. 추가 subtest 11개 통과.
  이후 추가된 migration PG 파일은 전체 수집에 포함되지 않았다. 해당 파일의 대상 제한 검사 9개는
  별도로 통과했고, 실제 PG 사례는 미실행이다. 임시 컨테이너와 동일한 환경에서 focused 15개 수집을 확인했다.
- 전체 검사의 `test_agent_pr17_contract.py` 12개 실패를 재현했다. 관련 agent 저장소,
  테스트 및 서비스 importer는 Checking 원본과 동일하다. 저장 단계는 게시 전 노출기한을 비우지만,
  계약 테스트와 `run_carousel_batch.py`의 `local_test_preview` staging은 서비스에 전달할 날짜를
  별도로 채우지 않는다. importer의 필수 날짜 계약과 충돌한다. 임의 날짜를 넣거나 서비스 검증을
  완화하지 않았으며 **전체 백엔드가 통과했다고 표시하지 않는다**. 이 증거만으로 모든 운영 publisher가
  동일하게 실패한다고 판단하지 않는다. 해당 테스트용 경로의 정책·handoff 수정은 별도 미해결 항목이다.
- 첫 전체 실행의 개발 인증 7개 실패는 테스트용 `ENABLE_DEV_API=false` 설정 때문이었다.
  테스트 환경을 올바르게 설정한 관련 API/auth 재검증은 23개 통과했다. 운영 설정은 계속 false다.

## 운영 적용 순서

1. **서버의 실제 경로와 현재 상태를 확인한다.** 후보를 `/home/ubuntu/apps/<검토한-폴더>`의
   깨끗한 별도 Git checkout에 준비한다. 필요 없는 RAG 인계 데이터는 Git LFS smudge를 실행하지 않고
   포인터 상태로 둔다. API 빌드 context는 `backend/`만 사용한다.

   ```sh
   sudo python3 deploy/main/preflight.py --sudo
   ```

   실행 중인 Main/Checking의 working directory, 이미지 ID, DB·Redis 실제 접속 대상, DB revision,
   timer 상태, 디스크 여유를 검토한다. 이 JSON에 비밀번호는 포함되지 않지만 운영 경로가 있으므로
   공개 저장소의 증거 파일로 자동 커밋하지 않는다.

2. **복구 수단과 실제 PostgreSQL 테스트를 검증한다.** 현재 디스크 여유만으로 전체 DB 덤프가
   들어간다고 가정하지 않는다. 기존 백업의 범위·위치·복구 가능성을 확인하고, 필요한 추가 백업은
   충분한 공간의 별도 대상으로 준비한다. 과거의 `content_slide` 데이터 제외 백업은 전체 복구본이 아니다.
   실제 DB 테스트는 서비스 DB 대신 합성 데이터의 폐기 가능한 PostgreSQL에서 실행한다.
   후보 API 이미지를 준비한 뒤 다음 도구를 사용한다. 호스트에는 Python 표준 라이브러리와 Docker만
   필요하며, 애플리케이션 의존성을 호스트에 설치하지 않는다.

   ```sh
   python3 deploy/main/postgres_rehearsal.py plan
   sudo python3 deploy/main/postgres_rehearsal.py apply --image-tag <검토한-전체-커밋-SHA>
   ```

   `apply`는 깨끗한 현재 checkout과 이미지 태그가 같은지 확인한다. 기존 pgvector 이미지와 후보
   API 이미지만 사용하고 다운로드하지 않는다. 네트워크가 없는 임시 PG와 그 내부 공간을 공유하는
   테스트 컨테이너만 실행하며, DB는 512MiB tmpfs에 둔다. 운영 접속 정보·서비스 `.env`·Docker 소켓은
   전달하지 않는다. 합성 회원·콘텐츠·슬라이드·세션·관리자·추천·활동·공지·알림 데이터를 넣고
   `b7 → 94` 변경 전후 값과 sequence를 비교하며, 탈퇴의 실제 PG 잠금도 검사한다.
   실패·skip이 있으면 성공으로 반환하지 않고 이번 실행의 임시 컨테이너만 정리한다.
   실제 PG 검증이 되기 전에는 완료로 표시하지 않는다.

3. **Main 환경과 이미지를 준비한다.** 기존 Main의 비공개 `.env` 값을 서버 안에서 보존한다.
   이미지 태그만 검증한 전체 커밋 SHA로 맞추고, 신규 선택 설정은 기본값을 확인한다.
   OAuth 앱·쿠키·Redis prefix·LLM 일일 한도를 임의로 교체하지 않는다.
   Main 평가 디렉터리는 0755, 집계 파일은 0644로 준비하고 기존 집계 내용을 보존한다.
   원문 관측·HMAC·개인 사례는 이 디렉터리에 복사하지 않는다.

   ```sh
   python3 deploy/main/validate.py prepare --env deploy/main/.env --check-compose
   ```

   후보 API는 `backend/Dockerfile`과 `backend/` context로 빌드한다. 저장 공간을 확인하며
   기존 이미지·컨테이너·볼륨을 자동 prune하지 않는다. 검증되지 않은 `.env`의 확인 플래그를
   일괄 `true`로 바꾸지 않는다.

4. **DB 변경과 Main API 교체를 순서대로 수행한다.** 검증한 Compose 파일 하나와
   `--env-file deploy/main/.env -p me-nu-main -f compose.main.yaml`을 명시한다.
   공유 DB 변경은 Main 및 Checking rollout lock을 함께 잡은 상태에서 수행한다.
   후보 API의 일회성 컨테이너에서 `python -m alembic upgrade 94d2da693c10`을 실행하며,
   기존 API·DB·Redis를 일괄 재기동하지 않는다. 성공한 revision을 조회한 뒤 Main API만 교체한다.
   `/health`, `/health/db`, `/health/redis`와 실제 회원·관리자 읽기 기능을 확인한다.

5. **Main 프론트를 반영한다.** API와 스키마가 준비된 후 통합 PR을 main에 병합하고,
   Vercel의 운영 빌드가 `NEXT_PUBLIC_API_BASE_URL=https://api.me-nu.kr`를 사용하는지 확인한다.
   기존 OAuth 계정의 로그인·로그아웃·탈퇴 보호, 피드·상세·저장, 관리자 권한·승인·반려·공지
   흐름을 검증한다. 운영 콘텐츠를 임의로 변경하는 테스트는 하지 않고 전용 합성 대상을 사용한다.

6. **운영 작업을 하나씩 인계한다.** 공지·평가는 기존 timer를 비활성화하고 실행 중 작업이
   끝난 것을 확인한 뒤 Main 도구로 전환한다. 추천기는 기존 컨테이너의 자동 재시작을 해제하고
   중지한 뒤 Main 추천기를 시작한다. 새 도구들은 이 상태가 아니면 실행을 거부한다.
   추천 생성·예약 공지·평가 파일 갱신과 기존 관측 기록의 연속성을 각각 확인한다.

7. **Checking 종료 경계를 확인하고 브랜치는 남긴다.** Main 기능과 운영 작업 인계가 확인된 뒤에만
   Checking 공개 진입 및 API 종료를 검토한다. Checking 이름의 DB·Redis·네트워크·볼륨은 유지한다.
   PR #111은 코드 통합 후 main 대상으로 바꾸어 추가 변경만 남는지 확인한다.
   Checking 브랜치 삭제는 이번 요청의 종료 지점 밖이다.

## 문제가 생겼을 때

Main 운영 작업을 중지하고 비활성 상태를 확인한 뒤 기존 Checking 작업을 재개한다.
API·프론트는 기록한 이전 이미지/배포로 복귀하되, 실제 DB 변경 후 이전 코드와의 호환성을 확인한다.
운영 DB를 테스트 데이터나 옛 덤프로 덮어쓰지 않으며, 전체 Compose down·볼륨 삭제를 사용하지 않는다.
Checking의 기존 이미지·checkout·timer와 평가 상태는 복귀 검증이 끝날 때까지 보존한다.
