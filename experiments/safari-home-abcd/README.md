# Safari HOME A/B/C/D — 합성 데이터 전용 인계 패키지

이 디렉터리는 운영 FE에 반영할 코드가 아니다. Git의 정확한 두 커밋에서 FE를 추출하고, **이 디렉터리의 `.runtime/` 사본에만** Mock 연결과 실험 패치를 적용한다. 기존 Worktree, 원본 의존성, 기존 실험 결과를 수정하지 않는다.

| 조건 | 원본 커밋 | 모든 피드 요청 limit | HOME에 실제 마운트하는 카드 | 이미지 |
|---|---|---:|---|---|
| A | `95c2c205f4ae82b881933fd7def9404c3ff9bd60` | 100 | 받은 카드 전체 | 정상 |
| B | 위와 같음 | 100 | 받은 카드 전체 | 요청·Blob·object URL·decode·Image·preload 차단 |
| C | 위와 같음 | 100 | 첫 카드 1개 | 첫 카드 이미지 정상 |
| D | `6b0ad9361b65a15c7d4b153b7b55e561f3e73e4a` | 20 | 받은 카드 전체 | 정상 |

123건 ID 순서 `1000 … 878`, 10장의 1308×1632 PNG, 합성 세션을 공통 사용한다. A/B/C 페이지는 `100+23`, D는 `20×6+3`이다. C도 모든 응답 데이터를 보관하고 다음 페이지를 요청한다. `slice(0,1)`은 실험 C에만 적용된다. 운영 D에는 콘텐츠 숨김이 없다.

B는 콘텐츠 이미지뿐 아니라 SVG/아이콘/CSS 이미지도 제거하고 첫 이미지 대기를 즉시 완료시킨다. 따라서 B가 안정적이어도 특정 이미지 함수가 단독 원인이라고 단정할 수 없다. `browser-runtime.js`가 네 조건 모두에서 계측하며 B에서만 네이티브 이미지 경로를 차단한다. C는 HOME 마운트만 제한한다. DETAIL/SAVE/MY 동작은 별도이다.

## 1. 새 PC 준비

검증한 환경은 Windows, Node **24.20.0** / zlib **1.3.2.1-motley-42c2f19**, Git for Windows, OpenSSL(Git 설치본), Chrome, Playwright **1.62.1**이다. FE 의존성은 각 원본 커밋의 `frontend/package-lock.json`을 사용한다. macOS/Linux 전체 실행은 아직 검증하지 않았다.

두 브랜치가 승인 후 원격에 올라간 다음, 기존 작업 폴더와 별개의 새 폴더에서 아래를 실행한다. 아래 명령은 인계 절차이며 이번 준비 작업에서 원격 fetch/설치를 실행했다는 뜻이 아니다.

```powershell
# HOME과 무관한 대용량 Git LFS 자료는 받지 않는다. 이 PowerShell 프로세스에만 적용.
$env:GIT_LFS_SKIP_SMUDGE='1'
git clone --no-checkout https://github.com/Por0ri/me-and-nu.git safari-lab
Set-Location safari-lab
git fetch origin codex/safari-home-abcd-handoff-20261125 codex/safari-home-page20-20261125
git switch --track origin/codex/safari-home-abcd-handoff-20261125
git cat-file -e 95c2c205f4ae82b881933fd7def9404c3ff9bd60
git cat-file -e 6b0ad9361b65a15c7d4b153b7b55e561f3e73e4a
Set-Location experiments/safari-home-abcd
node prepare.cjs
```

`prepare.cjs`는 필요한 commit이 없으면 실패하고 자동 fetch하지 않는다. Git archive는 `frontend/`만 추출한다. `.env`나 인증정보를 원본 작업 폴더에서 가져오지 않는다. 이미 `.runtime/`이 있으면 덮어쓰지 않는다.

의존성 설치는 새 PC 담당자가 승인한 환경에서 별도로 수행한다. 아래 npm 명령은 패키지 레지스트리 접속/설치 스크립트를 실행할 수 있으며, 운영 API 테스트 단계와 분리한다. 기존 프로젝트의 `npm install`을 실행하지 않는다.

```powershell
foreach ($variant in @('A','B','C','D')) {
  npm ci --prefix ".runtime/$variant/frontend" --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed: $variant" }
}
npm install --prefix .runtime/tools --save-exact playwright@1.62.1 --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Playwright installation failed' }
```

Chrome 설치본을 사용하므로 Playwright의 브라우저 다운로드 명령은 필요하지 않다. PATH에 OpenSSL이 다른 위치로 설치되어 있다면 `$env:LAB_OPENSSL='OpenSSL 실행파일의 절대 경로'`로 지정한다. 기존에 설치된 Playwright를 읽어서 사용할 때만 `$env:LAB_PLAYWRIGHT_MODULE='playwright 모듈의 절대 경로'`를 지정할 수 있다. 개인 PC 경로는 Git에 저장하지 않는다.

이미 동일 lockfile을 설치한 **물리적** 의존성 폴더가 있다면 처음 준비할 때 `node prepare.cjs --deps-from '절대경로/frontend/node_modules'`를 대신 사용할 수 있다. Junction을 만들지 않고 네 사본으로 복사하며 파일 해시를 비교한다. 이번 재검증은 이 방식으로 진행했다. 새 PC의 npm 레지스트리 설치 자체는 검증하지 않았다.

## 2. fixture·실험 패치·인증서·빌드

```powershell
node patch-snapshots.cjs
node fixtures.cjs
node certificates.cjs
node check.cjs
node build.cjs
node check.cjs
```

각 명령이 성공한 것을 확인한 뒤 다음 명령을 실행한다. fixture 생성 결과는 `fixture-manifest.json`의 콘텐츠 및 모든 PNG SHA-256과 일치해야 한다. 압축 라이브러리 차이로 실패하면 비교를 중단하고 검증된 Node/zlib 버전을 맞춘다. 네 조건만 서로 같게 만드는 것으로 과거 실험과 동일하다고 판단하지 않는다.

인증서는 PC마다 새로 발급한다. CA 7일/서버 3일, SAN은 **127.0.0.1만** 포함한다. 개인키는 `.runtime/private/`에 두고 Windows에서는 새 private 폴더의 상속 권한을 제거해 실행 계정·소유자·SYSTEM에 제한한다. 관리자 실행, OS 인증서 저장소 등록, iPhone 설치는 하지 않는다. OpenSSL 실행과 해당 새 폴더 ACL 설정만 수행한다.

빌드는 환경변수를 허용 목록으로 제한한다. `NEXT_PUBLIC_API_BASE_URL=https://127.0.0.1:8443`, `NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API=true`를 **빌드 시점**에 고정한다. 원본의 원격 API 허용 목록과 fallback도 실험 사본에서 제거한다. build/run Node에는 loopback 전용 network guard를 주입한다. 임의의 `.env*` 파일을 추가하지 않는다.

## 3. 실행과 검증

첫 PowerShell 창에서 실행한다. 다른 프로그램이 포트를 사용 중이면 종료시키지 말고 그 작업 담당자와 조정한다.

```powershell
node serve.cjs
```

- HTTPS 진입점: `https://127.0.0.1:8443/__lab`
- Next A/B/C/D: `127.0.0.1:3577`, `:3578`, `:3579`, `:3580`
- Mock은 HTTPS 진입 서버 내부의 메모리 구현이다. 별도 공개 API/DB는 없다.
- `serve.cjs`는 추가 실행 인자를 거부하며 Node 서버를 LAN 또는 wildcard로 바인딩하지 않는다.

두 번째 PowerShell 창의 같은 디렉터리에서:

```powershell
node probe-egress.cjs
node verify-https.cjs
node verify.cjs
node --require ./net-guard.cjs --test .runtime/D/frontend/tests/pivot-home-pagination.test.cjs .runtime/D/frontend/tests/pivot-feed-warmup.test.cjs
node export-results.cjs
# 필요할 때만 창을 띄워 수동 확인 (A 대신 B/C/D 가능)
node open-browser.cjs A
# 테스트 종료
node stop.cjs
```

자동 검증은 375px·390px × A/B/C/D 8개 조건에서 최초 limit/카드 수, 123건 순서·누락·중복, 가로 카드 이동, DETAIL 뒤 HOME 포커스, 저장과 SAVE/MY 상태, Secure 쿠키/CSP, B 이미지 네이티브 호출 0건을 확인한다. 가로 이동 자동 검사는 키보드 이벤트이므로 실제 터치/세로 스크롤 품질은 iPhone에서 별도로 확인한다. HTTPS 회귀 테스트는 다음 페이지 실패/재시도·첫 이미지 실패 주입·온보딩 전체 흐름을 이번 패키지에서 다시 수행하지 않는다. 운영 수정 커밋의 기존 단위/브라우저 테스트는 해당 소스에 보존되어 있다.

`open-browser.cjs`와 자동 검증은 해당 서버 인증서 SPKI만 예외 처리한 격리 Chrome을 사용한다. 시스템 CA 신뢰나 전역 HTTPS 검증을 해제하지 않는다. 일반 브라우저에서 인증서 경고를 무시하는 절차를 권장하지 않는다.

실험 전환 주소는 `/__lab/start/A?slides=10` … `/__lab/start/D?slides=10`. 전환 때 새 합성 세션이 생성되며 저장 상태도 초기화된다. **한 브라우저에서 한 조건씩** 실행한다. `slides=2`, `5`, `10`을 사용할 수 있지만 한 비교 묶음의 슬라이드 수는 같게 유지한다.

## 4. 외부 요청 및 비밀정보 경계

- 미정의 Mock은 `404 MOCK_ROUTE_NOT_DEFINED`, OAuth는 403이다. 운영 서버 fallback이 없다.
- CSP는 통신을 same-origin에 한정하며 B는 `img-src 'none'`이다. 세션 쿠키는 Secure/HttpOnly/SameSite=Lax, CSRF를 검증한다.
- 브라우저 route 차단 및 loopback 프록시 설정, Node fetch/DNS/socket/listen 가드로 검증 프로세스를 제한한다. host firewall 또는 OS 전체 패킷 캡처와 동일하지 않으며 다른 앱의 외부 통신까지 차단했다고 주장하지 않는다.
- `.runtime/results/browser-requests.jsonl`, `server-requests.jsonl`, `node-egress.jsonl`에 요청 주소/건수와 차단 내역을 남긴다. 인증 쿠키/CSRF 값은 요청 로그에 기록하지 않는다.
- `.runtime/private/runtime.json`의 중지 제어 토큰, 개인키, 세션, 스크린샷/원시 진단 로그는 공유하지 않는다. 결과는 `export-results.cjs`의 allowlist 출력 `shareable-results.json`을 검토 후 별도로 전달한다.
- `.runtime/`, node_modules, .next, 인증서/키, env 파일은 Git 제외다. `git add -f`를 사용하지 않는다. `scan-share.cjs`가 실제 Git 변경 후보 및 민감 패턴을 검사한다. 패턴 검사는 비밀 검토를 완전히 대체하지 않는다.

## 5. 해석과 다음 단계

`prior-windows-results.json`은 기존 로컬 HTTPS 결과를 경로·세션 값 없이 요약한 **과거 증거**다. 재구성 검증 결과와 혼동하지 않는다. D는 최초 DOM을 크게 줄이지만 스크롤해 123건을 모두 로드한 뒤 DOM은 A와 유사해진다. 최초 이미지 요청 수는 A와 D가 같았으며 메모리 감소를 이미지 요청 수만으로 추정할 수 없다. 동일 PNG를 여러 콘텐츠 URL에 재사용하므로 운영 이미지의 다양성·압축·캐시 상황을 완전히 재현하지 않는다.

BE가 전달한 09:56 WebContent `highwater` 약 2.20GB 증거는 메모리 종료의 단서다. 이 패키지는 원인 분리 도구이며 Safari 오류 수정 완료를 증명하지 않는다. Windows Chrome 결과를 실제 Safari 메모리 측정으로 표현하지 않는다.

실기기 연결과 테스트 순서는 [BE-IPHONE-TEST.md](BE-IPHONE-TEST.md), 공유 승인 경계는 [SHARING.md](SHARING.md)를 따른다. 현재 코드에는 LAN/배포 실행 기능이 없으며 기존 광범위 Node 방화벽 규칙을 변경하지 않는다.
