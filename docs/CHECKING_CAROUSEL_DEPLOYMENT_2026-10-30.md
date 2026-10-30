# Checking 캐러셀 배포 기록 — 2026-10-30

**진행 상태:** Checking 백엔드와 전용 DB를 갱신하고, 로컬에서 실제 에이전트가 생성한 캐러셀 5편·25장을 적재했다. 기존 테스트 콘텐츠 3편·18장을 보존하여 Checking에는 총 8편·43장이 있다. 서버의 실제 피드·상세·이미지 조회와 공개 HTTPS 응답을 확인했다. 사용자가 로그인한 Edge에서도 신규 5편·테스트 표시, 던전밥 상세 화면과 나츠메 캐러셀의 마지막 페이지 이동을 확인했다.

이번 반영은 사용자가 승인한 Checking 환경에 한정한다. main 및 생년월일 정책은 변경 대상이 아니다. 로컬 생성 과정은 [반복 생성·적재 기록](LOCAL_CAROUSEL_BATCH.md), 이전 도메인·OAuth 설정은 [Checking 배포 기록](CHECKING_DEPLOYMENT_2026-10-28.md)을 따른다.

## 반영 환경과 DB 변경

- 프론트: `https://checking.me-nu.kr`
- API: `https://api.checking.me-nu.kr`, 호스트 프록시 대상 `127.0.0.1:8002`
- Compose 프로젝트 / DB: `me-nu-checking` / `me_nu_checking`
- 실제 백엔드 런타임 커밋: `9881ceee0085534b7702fcc995b33dc3cf767369`
- DB revision: `f4b8c1d9a620` → `150a601074af`
- 서버 백업 위치: `/home/ubuntu/me-nu-checking-update-20261030-9881cee`

이번 마이그레이션은 추천 기능에 필요한 `content_entity_link`, `recommendation_round`, `recommendation_item`, `user_event` 4개 테이블을 추가한다. 생년월일 삭제 revision `d2f6a9c4e731`을 적용하지 않았다. 적용 후 `birth_date` 컬럼이 남아 있고, 값이 있는 행의 수가 적용 전과 같음을 확인했다. 실제 생년월일 값은 이 문서에 기록하지 않는다.

추천 링크·추천 결과·활동 기록이 저장된 뒤에는 migration downgrade가 데이터 삭제를 거부한다. 장애 시 자동 downgrade나 볼륨 삭제를 실행하지 말고, 이전 Checking 이미지와 서버 백업을 기준으로 복구 범위를 판단한다.

## 콘텐츠 적재와 테스트 표시

로컬에서 검증한 원본 bundle과 PNG를 사용했다. 서버에서는 에이전트를 다시 실행하거나 LLM을 추가 호출하지 않았다. 전송 묶음 `checking-carousel-five.tar.gz`는 콘텐츠 5편, PNG 25개, manifest를 포함한 31파일이며 압축 크기는 31,615,543바이트다. 인증 파일은 포함하지 않았으며 SHA-256은 다음과 같다.

```text
f8e419f286b7db03b5c8425e6dc5a7770b634ff575467faa813f5ebefc0308a2
```

Checking에서 부여된 콘텐츠 ID는 로컬 ID와 다르다.

- ID 4 / 로컬 ID 2: 냥코 선생님, 생활력까지 챙겼다!
- ID 5 / 로컬 ID 3: 원피스 흰수염, 2차 추첨 다시 열린다!
- ID 6 / 로컬 ID 4: 던전밥 일행이 노래방에 떴다!
- ID 7 / 로컬 ID 5: 인형 모티프 굿즈까지 귀여움이 마법처럼!
- ID 8 / 로컬 ID 6: 나츠메 우인장 나토리·마토바 후야제 페어!

5편 모두 1~5페이지의 캐러셀이며, `needs_review` 상태와 원본 QC 기록을 유지한다. 사람의 승인이나 정식 검수 통과로 바꾸지 않았다. 피드·상세에는 `DEV_PREVIEW`의 **개발용 미리보기**, `REVIEW_REQUIRED`의 **검토 필요**, `AI_GENERATED`의 **AI가 생성한 콘텐츠입니다.** 안내가 제공된다. 이 자료는 실제 생성 산출물의 팀 검증용 공개이며, 합성 테스트 픽스처나 정식 발행 승인과 구분한다.

5개 bundle 각각 dry-run → apply → 재실행 순으로 검증했다. apply는 각 `created=1`, 재실행은 각 `unchanged=1`로 중복 생성 없이 종료했고, 25개 PNG의 DB 저장 해시가 전송 전 원본과 일치했다. 원본 로컬 ID를 사용한 `import-plan-{2..6}.json`, `import-apply-{2..6}.json`, `import-repeat-{2..6}.json`은 서버 백업 폴더에 보관했다.

## Checking에 한정된 미리보기 허용

`CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED`는 기본값이 `false`이며, 이번 Checking 런타임에서 명시적으로 활성화했다. DB 이름만으로 미리보기 권한을 부여하지 않는다. Checking 환경 식별자, 전용 DB·사용자·컨테이너 호스트, 정확한 Checking 프론트/API 출처, Secure 세션, 개발 인증 우회 비활성 조건을 함께 검사한다.

적재에는 `--checking-test-preview`를 사용하며 기존 로컬 전용 `--local-test-preview`와 구분한다. API 조회 시에도 같은 범위를 다시 검사한다. 플래그를 끄거나 운영 환경으로 바꾸면 원본 QC 기록을 수정하지 않고 미리보기 콘텐츠가 공개 대상에서 제외된다. 실제 Checking 데이터에 운영 설정을 적용한 읽기 전용 검사에서도 신규 5편이 제외됨을 확인했다.

## 서버와 공개 응답 검증

`checking-content-verification.json`의 읽기 전용 검사 결과는 다음과 같다.

- 연결 DB `me_nu_checking`, revision `150a601074af` 확인
- 신규 5편·25장, 전체 8편·43장 확인
- 실제 피드 조회 결과 ID `8, 7, 6, 5, 4, 3, 2, 1` 확인
- 신규 5편의 상세와 각 1~5페이지 이미지 조회, 미리보기·검토 필요·AI 생성 안내 확인
- 운영 설정에서 신규 미리보기 비노출, 생년월일 컬럼·기존 비어 있지 않은 행 수 보존 확인

`public-api-verification.json`에서는 HTTPS 인증서 검증을 유지한 상태로 Checking·Test·Labs의 `/health`, `/health/db`, `/health/redis` 총 9개 응답이 모두 200이었다. Checking 피드의 비로그인 요청은 401이며, Checking 출처 CORS 응답은 200과 `https://checking.me-nu.kr` 허용 값이었다. 이 결과는 다른 환경에 새 코드를 배포했다는 의미가 아니다.

Checking 외 기존 환경의 6개 컨테이너는 전후 ID와 `StartedAt`이 같았다. 비교 증거는 서버 백업에 보관했다. 배포 전 관련 로컬 테스트는 **288개 통과·1개 건너뜀**이며, 실제 서버 반영 결과는 위의 별도 조회·HTTP·적재 검사로 확인했다.

`public-frontend-verification.json`에서는 Checking 로그인 문서와 홈 문서의 HTTP 200을 확인했다. 로그인 제목과 카카오·네이버 버튼, 공개 배포 자산의 Checking API 주소, 미리보기 표시·CSS 및 2초 알림 처리 코드가 존재했다. **이 HTTP·자산 검사만으로 프론트의 정확한 Git 커밋이나 로그인 후 화면 동작을 확정하지 않는다.** 이 검사 시점의 기록에서 `exactCommitVerified`와 `browserVisualVerification`은 `false`이며, 이후의 실제 브라우저 확인은 다음 절에 별도로 기록한다.

## 브라우저 최종 확인

사용자가 Edge에서 Checking에 로그인한 뒤 기존 탭을 새로고침하여 확인했다. 개발 인증 우회로 만든 화면 확인이 아니다.

- 홈에서 신규 콘텐츠 ID 8·7·6·5·4 모두 노출됐고, **개발용 미리보기** 표시 5개를 확인했다.
- 던전밥 콘텐츠 ID 6의 상세에 이미지·본문·출처와 **검토 필요 / 개발용 미리보기 / AI가 생성한 콘텐츠입니다.** 안내가 표시됐다.
- 상세 확인 후 홈으로 복귀했다. 홈·상세 증거 화면을 `checking-home.png`, `checking-detail.png`로 저장했다.
- 나츠메 콘텐츠 ID 8을 5번 슬라이드로 이동하여 현재 페이지 표시(`aria-current=true`)와 이미지 로딩을 확인한 뒤 1번으로 복원했다. Edge는 Checking 홈 화면을 유지했다.

결과는 `browser-verification.json`에 별도로 보관했다. 이번 브라우저 확인을 신규 가입·온보딩·저장·로그아웃까지의 전체 사용자 흐름 재검증으로 해석하지 않는다.

검증 원본은 로컬 `C:/Users/USER/me_nu/out/checking-shared-five-2026-10-30/`의 `checking-content-verification.json`, `public-api-verification.json`, `public-frontend-verification.json`, `browser-verification.json`, `package-verification.json`에 보관했다. 생성 이미지·인증 정보·DB 백업은 Git 문서에 포함하지 않는다.
