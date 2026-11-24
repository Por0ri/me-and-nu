# 메인 서비스 전환 실행 (2026-11-17)

## 현재 상태

- **완료:** 전체 백업의 로컬 격리 복원·삭제, 기존 공개 3,530건의 일반 공개 전환,
  메인 FE/API 배포와 apex/www DNS 연결, 외부 점검 27건, 20:45 KST 최종 런타임 점검,
  실제 카카오 로그인·가입 절차·피드 이미지·로그아웃 E2E.
  메인과 Checking에서 같은 3,530건이 공개되며 전환 당시 기존 회원·추천·검수 이력의
  보호 검증을 통과했다. 이후 실제 로그인·가입·피드 사용으로 발생한 정상 서비스 활동은
  전환 당시 보존 검증과 구분한다.
- **최종 확인 완료:** 21:07 KST에 백업·복구·공개 전환·외부 접속·런타임 증거를 다시
  대조하고 readiness 7개 값을 `true`로 저장했다. 나머지 환경 설정은 바이트 단위로 보존했다.
  네이버는 기존 개발 중 앱의 등록 멤버 범위를 유지한다.
- **계속 비활성:** 자동 수집·유료 생성. 앱 소스는 백엔드 `f08549d`, main merge
  `ea22d98`이며 이번 운영 검증 때문에 추천 코드나 생성 정책을 바꾸지 않았다.

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
  실제 격리 복원 결과를 확인한 다음 전달한다. 원문을 출력하지 않으며, 로컬 전체 복원은
  아래 기록된 원본 데이터·목적지에 대한 별도 명시 승인 후에만 실행한다.
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

백업/전환/배포의 실제 결과는 아래 시간순 운영 기록에 추가했다. 각 이력의
'진행 중'·'아직 미완료'는 해당 시점의 상태이며, 현재 상태는 위 요약을 따른다.

## 실행 중 확인된 사실

- 준비 및 운영 가드 커밋 `f08549d`를 Checking에 push했다.
- Vercel Production 환경에만 `NEXT_PUBLIC_API_BASE_URL=https://api.me-nu.kr`를
  등록했다. 기존 Checking Preview 설정은 보존했다.
- 가비아 로그인 복구 후 A `api → 52.78.2.187`, TTL 600을 추가했고,
  외부 DNS 조회에서도 같은 주소를 확인했다. 기존 6개 레코드는 변경하지 않았다.
- 원본 Checking 소스와 API는 그대로 두고 `/home/ubuntu/apps/me-nu-main-20261117`에
  위 커밋을 별도 checkout했다. 메인 API 이미지 SHA는
  `9b187ce250d59f08b6f0e313298269bc328fe269063181c7448ec779fad82faa`이다.
  네트워크 없는 검사에서 UID 10001, Pillow import, Python 소스 권한 검증을 통과했다.
- 메인 환경 파일은 서버 내부에서만 기존 DB/Redis/OAuth 앱 정보를 복사해 0600으로
  준비했다. LLM 키는 넣지 않았고 생성은 비활성 상태다.
- 환경 준비의 첫 시도는 Checking의 복수 프록시 IP 목록과 메인 단일 IP 가드의
  형식 차이로 파일 생성 전에 중단됐다. 운영 하니스에서 해당 목록 복사를 제거하고,
  실제 메인 프록시 IP를 별도로 확인해 지정하도록 수정했다. 서비스 오류는 아니다.
- 읽기 전용 capture 결과는 고정 ID 3,530개/미리보기 3,526개/기존 승인 4개이며,
  manifest와 ID 해시 모두 기존 승인값과 일치했다. 원본 전환 계획은 서버 0600으로만
  저장했고 서비스 DB 쓰기와 LLM 호출은 0회다.
- 별도 로컬 배포 worktree에서 일반 merge를 준비했다(`ea22d98`). 충돌 없고 최종
  tree `10fb010aa969ed68753aeef1f17e2c4665b0d699`가 Checking과 정확히 동일하다.
  이 단계에서는 아직 main으로 push하지 않았다.
- PowerShell에서 Git의 `^{tree}` 인수를 따옴표 없이 읽은 조회가 해석 오류를 냈다.
  인수를 따옴표로 감싼 명령으로 다시 확인했으며 병합 충돌이나 파일 손상은 없었다.
- DB 약 29.0GB 중 약 27.9GB가 슬라이드 이미지다. 서버 내 전체 snapshot 백업을
  진행 중이며, 동일 서버에 백업과 전체 복원본을 동시에 보관할 공간은 부족할 수 있어
  복원 위치를 별도로 검토한다. 실제 전환은 복구 검증 전에는 실행하지 않는다.
- 첫 백업의 압축 속도는 약 2.34MB/s였다. 기존 DB의 CPU 제한 안에서 `pg_dump`의
  압축까지 수행해 한 시간 제한을 넘을 것으로 확인했다. 해당 하니스의 정확한 명령과
  COPY PID/앱/대상 테이블/시작 시각을 검증해 이 작업만 취소했다. 부분 archive는
  보존하고 복구 완료 증거를 발행하지 않았다. 이후 COPY 작업 0건, Checking `/health`
  정상 응답을 확인했다. DB/추천의 자원 설정은 변경하지 않았다.
- 별도 PG17 클라이언트(내부망만, 1 CPU/512MiB, gzip level 1)에서 압축하도록
  백업을 재시작했다. 동일 snapshot/전체 행·이미지 해시/sequence 검증 절차를 유지한다.
- 메인 API의 실제 프록시 peer `172.28.0.1`을 임시 probe와 실제 API TCP 연결에서
  교차 확인해 한 개 IP만 신뢰하도록 설정했다. loopback API/DB/Redis health 통과,
  DB pool 2/overflow 0, 자동 수집·유료 생성 false를 확인했다.
- 하니스 업로드가 비동기로 진행되는 중 실행을 먼저 시도한 명령은 파일 없음으로
  종료됐다. 업로드 완료를 확인한 뒤 실행했고, 실패한 시도의 서비스 변경은 없었다.
- Caddy 첫 시도는 서버의 자기 공인 IP HTTPS 접속 시간 초과로 적용 전에 멈췄다.
  외부 PC의 Checking HTTPS와 서버 localhost의 원래 SNI 인증서 검증은 정상이다.
  인증서 검증을 끄지 않고 두 검사를 분리했다.
- Caddy 원본 SHA `1ba122a4b129ec36eacb9745e7a246ff569a0ab83517626c7e135b5ea745fcc5`
  전체를 보존한 채 `api.me-nu.kr → 127.0.0.1:8003` 블록만 추가했다. 변경 후 SHA는
  `552308bda5e911275cd07cd3f8eb8599432c8b6192a60ee4950ae7f55a3e3bf4`다.
  서버 TLS 및 외부 PC의 메인 health/DB/Redis·Checking health가 모두 통과했다.
  복구 자료는 서버 `.../caddy/caddy-main-20261117T030441Z-58f9f4f7.metadata.json`이다.
- `ea22d98`을 정상 push로 main에 반영해 Vercel Production 빌드를 시작했다.
  apex/www DNS 전환 및 콘텐츠 상태 전환은 아직 하지 않았다.
- 전환 하니스는 실제 저장소의 CAS UPDATE 두 종류만 쓰기로 허용한다. 네트워크 없는
  새 이미지에서 두 정상 SQL과 금지 SQL 8개 검증을 통과했다. 외부 검사 스크립트의
  첫 import 경로 오류는 `PYTHONPATH=/app` 지정으로 바로잡았고 DB 연결은 0회였다.
  이미지 전체 바이트 복구 검증은 백업 단계에서 하고, 이미지 쓰기가 금지된 전환 단계에서는
  저장 SHA·바이트 길이·모든 메타데이터를 검사해 불필요한 대용량 재해시를 피한다.
- 분리 클라이언트의 첫 실행에서는 Docker 기본 로그가 바이너리 stdout을 중복 저장해
  예상보다 디스크 사용량이 증가했다. 이번 작업의 정확한 프로세스만 중단했고,
  임시 클라이언트와 그 로그 정리가 끝난 것을 확인해 여유 약 40GiB를 회복했다.
  두 부분 archive는 보존했다. `--log-driver none`을 필수 지정해 새 snapshot으로
  다시 시작했으며 기존 PostgreSQL의 CPU/메모리 설정은 바꾸지 않았다.
- Vercel Production 배포 `3edUKa78XtupLDNp5neTKpbacgrc`가 `ea22d98`로 Ready/Current임을
  확인했다(2026-11-17 12:06 KST). 이 사실만으로 DNS/로그인/데이터 전환 완료를 의미하지 않는다.
- Production 빌드의 두 경고는 같은 npm `cache-dir` 환경 설정 경고였다. 빌드는 통과했다.
- 외부 PC API 전용 점검 20건 통과: health 6건, 정확 CORS와 교차 출처 차단,
  공개 약관, 비로그인 콘텐츠 401, 개발용 로그인 404, 무state OAuth 콜백의 안전한 303.
  이 점검은 익명 세션 생성·실제 OAuth·LLM 호출 0회이며, 실제 회원 로그인 검증을
  대신하지 않는다. 결과는 `main-api-only-public-smoke.json`에 기록했다.
- 메인 설정으로 카카오·네이버 authorize endpoint에 무쿠키 GET을 각 1회 보냈다.
  카카오는 로그인 페이지 302, 네이버는 200이며 응답에서 알려진 명시적 오류는 없었다.
  redirect 추적·로그인·동의·토큰 교환은 하지 않았으므로 콜백 등록 완료 증거로 취급하지 않는다.
  기존 앱 관리 계정으로 개발자 콘솔에 로그인하는 단계는 사용자 입력 대기 중이다.
- 12:42 KST 런타임 점검의 상태 검사 모두 통과했다. 메인/Checking API healthy,
  기존 PostgreSQL·Redis·추천 컨테이너 실행, 메인 pool 2/overflow 0, 로컬 포트만 노출,
  생성 Worker 미시작·키 미장착을 확인했다. PostgreSQL의 일반 연결 여유는 19개였다.
  증거는 서버 `evidence/runtime/runtime-20261117T034242-63663b35.json`에 보관했다.
  이 검사는 컨테이너 및 DB 연결 수 범위이며, 호스트 전체 스케줄러를 감사한 결과는 아니다.
- 전체 archive가 커지면서 여유가 10GiB 아래로 내려가, 중단된 두 부분 dump의
  정확한 경로·크기·0600·소유자·metadata 부재·열린 파일 없음부터 확인했다.
  두 불완전 파일만 정리해 8,324,960,256 bytes를 확보했다. 현재 백업은 유지했고,
  이후 여유 약 16.9GiB를 확인했다. 원본 DB 쓰기는 없었다.
  정리 목록은 서버 `backups/interrupted-partials-cleanup-20261117.json`에 남겼다.
- 전체 snapshot 백업 완료: `checking-20261117T031232Z-1fdb4c9c.dump`,
  32,410,984,954 bytes, SHA-256
  `6069256d43027c034e96007ff47be3b08fc5b5b78868f16f47fbc10ba4be3218`.
  서버 metadata에 동결된 manifest/ID SHA가 일치하며, 복구 검증 값은 아직 false다.
  서버에는 약 16.89GiB가 남아 전체 복원본을 함께 둘 수 없다.
- 로컬 격리 PG17로 SSH 스트리밍 복원하는 실행은 자동 승인 검토가 거절했다.
  전체 회원·콘텐츠·이미지의 서버→사용자 PC 전송에 대한 구체적인 동의가 기존 승인에
  포함되지 않았다는 사유다. 명시적 승인을 요청했으며 이 시점까지 전송·복원은 0회다.
  승인 전에는 다른 경로로 반출하지 않고, 전환 apply도 복구 검증 완료까지 보류한다.
- 원격 백업이 완료되고 임시 클라이언트·해당 하니스 프로세스가 사라진 뒤에도 로컬 SSH
  연결이 종료되지 않았다. 완성 metadata를 별도 읽기 전용 연결로 확인한 후, 정확한
  원격 명령·manifest·ID·부모 PID가 일치하는 로컬 SSH 연결만 정리했다.
  이는 완료된 전송 세션 정리이며, 원격 백업·metadata·서비스에는 변경이 없다.

## 19시 이후 승인된 후속 검증

- 사용자가 전체 백업(회원·콘텐츠·이미지 포함)을 이 PC의 외부 접속이 차단된 임시
  Docker DB로 복원하고 검증 후 삭제하는 작업을 명시 승인했다. 복원 하니스
  `local_restore_verify.py` SHA `e4f6b5737141729c9f46aa270182f4aa6c4532dd413bd2bcfd90088995bee3a2`로
  실행을 시작했다. 실제 검증 완료 전에는 recovery receipt를 발행하지 않는다.
- 19:06 KST 읽기 전용 재확인에서 현재 공개 ID 3,530개, 미리보기 3,526개,
  기존 승인 4개와 동결 manifest/ID SHA가 모두 동일했다. PNG 원문이나 보호 테이블
  전체를 읽지 않고 기존 공개 대상의 메타데이터만 조회했으며 DB 쓰기는 없었다.
- 사용자가 두 OAuth 앱의 메인 로그인 설정 저장을 별도 승인했다. 카카오 `me;nu`
  앱의 기존 로그인 설정과 네이버 `me-nu` 앱에 메인 callback을 각각 한 개 추가했고,
  기존 localhost/Test/Labs/Checking 4개 callback을 보존했다. 네이버 서비스 URL은
  `https://www.me-nu.kr`로 바꿨다. 새로 열어 저장된 5개 주소를 확인했다.
- 키 대조를 위한 조회 시도는 자동 승인 검토에서 거절되어 실행하지 않았다. 대신 기존
  콜백 편집 메뉴에서 승인된 주소만 수정했다. 키 재발급·삭제 및 개인정보 동의항목 변경은 없다.
- 네이버 앱은 기존부터 `개발 중` 상태다. 설정 화면은 등록된 멤버만 로그인할 수 있고
  일반 계정 이용에는 네이버 검수가 필요하다고 안내한다. callback 등록 완료와 전체
  사용자에게 네이버 로그인이 개방됐다는 주장은 구분한다. 검수 제출은 하지 않았다.

## 20:30 이후 복구·공개·메인 연결 확인

- **20:30 KST 전체 격리 복원 성공.** 서버 백업 32,410,984,954 bytes를 승인된 로컬
  임시 PG17로 복원해 46개 테이블의 행수·전체 행/이미지 해시와 34개 sequence 구조를
  대조했다. 원본 snapshot과 일치하며 sequence 증가도 0개였다. 임시 DB 컨테이너와
  volume을 삭제한 뒤에만 recovery receipt를 발행했다. LLM 호출은 0회다.
  로컬 증거는 `out/main-beta-release-2026-11-17/local-restore-evidence/`의
  `checking-20261117T031232Z-1fdb4c9c.metadata.recovery-evidence.json` 및
  같은 이름의 `.recovery-receipt.json`이다. 백업 원본과 SHA는 위 완료 기록을 유지한다.
- **고정 3,530건의 운영 전환 성공.** `transitioned=3526`, `already_approved=4`,
  Content/Draft CAS UPDATE는 각각 3,526회이며 `protected_unchanged=true`다.
  메인과 Checking의 공개 대상은 각각 같은 3,530건이다. 새 콘텐츠를 생성하거나
  대상 밖 비공개·검수 대기 글을 공개하지 않았고, 기존 회원·추천·이미지·출처·검수 이력은
  보호 검증을 통과했다. 실제 증거는 서버
  `/home/ubuntu/me-nu-main-release-20261117/publication/applied.json`이다.
- 전환 후 별도 검증도 `read_only=true`, DB 쓰기 0회로 통과했다. 양쪽 공개 대상은
  3,530건으로 동일하며 보호 상태 SHA는
  `77b5a5e9523a3467b7e827e843d346f904242a9ca95785324f14ce6bdb4facc4`다.
  서버 `publication/verified.json`에 보관했다. 전환 중 PNG 원문을 다시 해시한 것은
  아니며, 완료된 전체 바이트 복구 검증과 전환의 저장 SHA·길이·메타데이터 보호를 구분한다.
- **20:39 KST 메인 FE DNS 저장 완료.** 가비아에 A `@ → 216.150.1.1`,
  CNAME `www → 1b4d5e1d5141e60c.vercel-dns-017.com`을 TTL 600으로 추가했다.
  기존 7개 레코드를 보존해 총 9개가 됐고, DNS 응답과 Vercel의 Valid 상태를 확인했다.
  기존 `me-nu.kr → www.me-nu.kr` canonical 전환과 메인 API 연결을 유지한다.
- 메인 공개 경로 점검 `main-public-final-smoke.json`의 **27개 요청이 모두 통과**했다.
  이 점검 자체의 세션 생성·외부 OAuth·LLM 호출은 모두 0회다. 실제 배포된 메인
  프론트엔드의 API base URL과 fetch 대상이 `https://api.me-nu.kr`임을
  `compiled-main-api-origin-www/verified-api-origin.json`으로 확인했다.
  두 증거는 로컬 `out/main-beta-release-2026-11-17/` 아래에 있다.
- **20:45:29 KST 최종 런타임 검사 전항목 통과.** 기존 Checking API·PostgreSQL·Redis·
  추천 컨테이너 ID와 이미지가 기준선 그대로이며, 메인/Checking API는 healthy다.
  PostgreSQL `max_connections=30`, 일반 연결 여유 21개, idle transaction 0개를
  확인했다. 메인 pool 2/overflow 0, 미리보기·개발 기능 비활성, 세션 namespace 분리도
  유지된다. 생성 Worker는 `created` 상태로 키가 없고 미시작이며, 실행 중 컨테이너
  이름 검사에서 자동 수집/Beat/유료 생성 작업은 발견되지 않았다.
  이 범위는 호스트 전체 스케줄러 감사가 아니다. 로컬 `main-runtime-final-audit.json`과
  서버 `evidence/runtime/runtime-20261117T114529-21efa0ef.json`이 증거다.
- **실제 카카오 로그인·가입 절차·피드 이미지·로그아웃 E2E 완료.** 사용자가 카카오
  로그인 방식임을 명시했고, `https://www.me-nu.kr/pivot/consent`의 남은 가입 절차를
  직접 완료했다. Windows의 실제 Edge 화면에서 `https://www.me-nu.kr/pivot/home`과
  콘텐츠 이미지 2개가 정상 렌더링된 것을 직접 확인해 `main-feed-verified.png`를 남겼다.
  사용자가 MY에서 직접 로그아웃한 뒤에도 실제 화면에서
  `https://www.me-nu.kr/pivot/login`으로 돌아와 카카오·네이버 로그인 버튼이 표시된 것을
  직접 확인했다. 사용자 응답만으로 성공을 기록한 것이 아니며, 쿠키·토큰 추출이나
  추가 LLM 호출은 없었다. 이번 검증은 기존 계정과 동일한 회원 ID가 유지됐다는 실증은 아니다.
- 화면 검증 중 브라우저 연결 끊김과 새 탭 혼동이 있어 사용자 요청에 따라 기존 탭만
  사용했다. 브라우저 연결을 재시작한 뒤 디버거가 연결되지 않은 상태에서도 Windows
  실제 화면 읽기로 확인했다. 자동 로그아웃 클릭은 전경 창 문제로 실행되지 않아 사용자가
  직접 완료했다. 이들은 검증 도구의 운영상 문제이며 앱 코드 결함으로 기록하지 않는다.
- 로그인·가입·피드 이용에 따른 정상 서비스 활동은 위 전환·읽기 전용 확인의 보호 SHA
  검증 이후에 발생했다. 그 활동까지 과거 snapshot과 불변이라고 주장하지 않는다.
  자동 수집·유료 생성은 계속 꺼둔다.

## 21:07 최종 배포 확인 완료

- 전체 백업 SHA와 실제 복구 receipt, 공개 전환 apply/읽기 전용 검증 receipt,
  DNS·TLS·콜백·배포 프론트엔드·실제 카카오 E2E 증거를 대조해 preflight가 통과했다.
  이어진 런타임 점검에서 일반 DB 연결 여유는 20개였으며 기존 컨테이너와 생성 비활성
  상태를 유지했다. 컨테이너 상태 확인 범위는 위에 명시한 것과 같다.
- `MAIN_BACKUP_VERIFIED`, `MAIN_DATA_PARITY_VERIFIED`, `MAIN_DB_CAPACITY_VERIFIED`,
  `MAIN_DNS_TLS_VERIFIED`, `MAIN_FRONTEND_BUILD_VERIFIED`, `MAIN_OAUTH_CALLBACKS_VERIFIED`,
  `MAIN_RELEASE_APPROVED`만 `false`에서 `true`로 전환했다. 원본을 root 전용으로 백업한 뒤
  SHA·inode 비교와 원자적 교체, 변경 전후 Compose 검증을 수행했다.
- 원본 환경 파일 SHA는
  `f530906d05820b33661059c736d63e3c9d0547854ede1192b8decb2fa31421f9`,
  완료 후 SHA는
  `f319da2d338494ba40681a15683f00c051810e808dad3ebfbd3f9fdff6a59378`다.
  나머지 바이트와 OAuth provider 설정은 보존했다. 최종 확인 단계의 서비스 DB 쓰기와
  LLM 호출은 모두 0회이며, 컨테이너 재시작·자동 수집·유료 생성 활성화도 없었다.
- 완료 시각은 `2026-11-17T12:07:32.403077+00:00`이다. 서버 증거는
  `/home/ubuntu/me-nu-main-release-20261117/evidence/attestations/main-attestations-20261117T120732-ede6a4bc.json`,
  원본 환경 백업은 같은 디렉터리의 `main-env-before-20261117T120732-ede6a4bc.env`다.
  환경 파일 원문이나 비밀 값은 출력하지 않았다.
- root 전용 증거 경로의 receipt를 일반 SCP로 읽지 못해 기존 승인된 sudo 읽기로
  JSON만 로컬 `out/main-beta-release-2026-11-17/main-attestations-final.json`에 보관했다.
  서버 파일 권한이나 환경 파일을 변경하지 않은 증거 보관 절차상의 보정이다.
- 메인 접속 주소는 `https://www.me-nu.kr`이다. 카카오 서비스 이용 흐름은 실제 화면에서
  확인했고, 네이버는 기존 등록 멤버 제한을 유지한다. 하루 검수 통과 20건 목표의 자동
  생성은 예산·시도 상한 결정과 일일 집계·스케줄 검증을 거칠 후속 작업이다.
