# 공유 범위와 승인 경계

운영 수정 브랜치 `codex/safari-home-page20-20261125`의 기존 `6b0ad9361b65a15c7d4b153b7b55e561f3e73e4a`는 변경하지 않는다. 공유용 실험 브랜치 `codex/safari-home-abcd-handoff-20261125`는 `95c2c205f4ae82b881933fd7def9404c3ff9bd60`를 기준으로 **experiments/safari-home-abcd/**만 추가한다. D는 별도 FE 수정 커밋을 추출하므로 팀원이 두 ref를 모두 확보해야 한다. Main/Checking으로 병합하지 않는다.

기존 FE 수정 6개 파일:

- `frontend/lib/pivot/home-feed.ts` — HOME 전용 20건 상수
- `frontend/lib/pivot/home-service.ts` — 최초/후속 요청의 공통 크기
- `frontend/lib/pivot/feed-warmup.ts` — warm-up 요청 크기 일치
- `frontend/tests/pivot-home-pagination.test.cjs`
- `frontend/tests/pivot-feed-warmup.test.cjs`
- `frontend/tests/pivot-home-page-size-browser-check.cjs`

실험 브랜치에 새로 추가할 파일 목록:

| 용도 | `experiments/safari-home-abcd/` 아래 파일 |
|---|---|
| 문서 | `README.md`, `BE-IPHONE-TEST.md`, `SHARING.md` |
| Git 경계 | `.gitignore`, `.gitattributes`, `scan-share.cjs` |
| 재구성 | `config.cjs`, `utils.cjs`, `prepare.cjs`, `patch-snapshots.cjs`, `check.cjs` |
| 합성 자료 | `fixtures.cjs`, `fixture-manifest.json`, `mock.cjs` |
| 빌드·실행 | `build.cjs`, `certificates.cjs`, `serve.cjs`, `stop.cjs`, `local-client.cjs`, `open-browser.cjs` |
| 차단·계측 | `net-guard.cjs`, `browser-runtime.js`, `probe-egress.cjs` |
| 검증·결과 | `verify.cjs`, `verify-https.cjs`, `export-results.cjs`, `prior-windows-results.json`, `current-windows-results.json` |

`current-windows-results.json`은 이번 재구성에서 성공한 검증의 비밀정보 제거 요약이다. 정확한 파일별 바이트 수/SHA-256은 `scan-share.cjs`의 출력에서 확인한다. 기존 44MB PNG는 생성기+해시로 대체하고, 기존 원시 결과는 원래 로컬 위치에 보존한다.

현재 준비 단계에는 새 Commit/Push/PR/배포가 없다. 아래는 **승인 후** 사용할 명령 예시이며 자동 실행 스크립트가 아니다. 작업자는 각각 올바른 저장소/브랜치인지 먼저 확인한다. `git add .` 또는 `git add -f`는 사용하지 않는다.

```powershell
# 기존 FE 수정 저장소에서, HEAD가 아래 정확한 SHA인지 먼저 확인
git rev-parse HEAD
git status --porcelain=v1
# 원격 같은 이름의 ref가 없거나 의도한 선조인지 재확인; force push 금지
git ls-remote --heads origin codex/safari-home-page20-20261125 codex/safari-home-abcd-handoff-20261125
# 승인된 경우 기존 커밋만 전송 (FE에 새 커밋을 만들지 않음)
git push origin 6b0ad9361b65a15c7d4b153b7b55e561f3e73e4a:refs/heads/codex/safari-home-page20-20261125

# 별도 실험 저장소에서
git branch --show-current
node experiments/safari-home-abcd/scan-share.cjs
git add -- experiments/safari-home-abcd
git diff --cached --stat
git diff --cached --check
# 최종 내용·비밀정보 재검토 및 승인 후에만
git commit -m "test: add reproducible synthetic Safari HOME ABCD harness"
git push origin HEAD:refs/heads/codex/safari-home-abcd-handoff-20261125
```

주의: 대상 GitHub 저장소는 준비 시점에 **공개**였다. 인증서·개인키·실제 세션·계정·원시 Jetsam·개인 PC 경로를 올리지 않는다. `.runtime/`에는 합성 데이터만 있어도 중지 토큰/개인키가 있으므로 디렉터리 전체를 공유하지 않는다. 기존 Git LFS 자료는 이 변경의 추가 파일이 아니며 HOME 실험에 필요하지 않다. pre-push 훅이 대용량 LFS 전송을 요구하면 중단하고 원격 보유 상태를 확인한다. 훅을 무조건 우회하지 않는다.

GitHub 연결 계정의 Push 권한과 실제 CLI 인증은 별개다. 또한 feature branch Push가 Vercel 등의 자동 Preview 배포를 유발할 수 있다. 연결된 배포 설정 조회가 권한 오류로 차단되어 **자동 배포 비활성 여부는 미확인**이다. 배포 금지 조건을 지키려면 배포 담당자가 이 두 브랜치의 트리거 제외를 확인하거나, 배포 연동이 없는 별도 공유 저장소를 승인해야 한다. 승인 전 Push하지 않는다. 기존 프로젝트 설정을 이 패키지가 변경하지 않는다.
