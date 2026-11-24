# 관리자 콘텐츠 승인·반려 POST UI (11/19, 실행 비활성)

> 이 문서는 11월 19일 구현 당시 기록이다. 11월 20일의 계정·권한 고정, 미확정 요청 이탈 경고 보완과 후속 검증은 [안전성 보완 문서](ADMIN_REVIEW_SAFETY_VALIDATION_2026-11-20.md)를 따른다. 아래 환경 제약과 검증 수치를 현재 상태로 해석하지 않는다.

기준은 fetch로 확인한 `origin/Checking` `ab7aa22946663f48fd73e4252c9162d7d7345d01`이다. PR #88·#90·#91은 아직 병합되지 않았다.

이 작업은 #91(review 메타데이터 UI)과 #88(격리 HTTP 리허설)을 재구현하지 않고 그 위에 쌓았다.
- 독립 브랜치 `claude/admin-review-post-ui-1119`를 #91 브랜치(`9edc274`)에서 만들고 #88(`e969310`)을 병합했다. 원래 두 브랜치는 그대로다.
- #88·#91이 Checking에 병합되면 이 브랜치의 차이는 이번 작업만 남는다.

## 11/19 BE 확인 결과 (환경 제약)

- Checking DB에 Migration `47c1a8d6b302`가 적용되지 않았다.
- PR #89가 Checking BE 서버에 배포되지 않았다.
- 역할별 관리자 계정과 전용 검수 데이터가 준비되지 않았다.
- Main과 Checking이 DB를 공유한다. 따라서 기존 서비스 콘텐츠에 POST를 보내면 안 된다.

## 구현

| 파일 | 내용 |
| --- | --- |
| `frontend/lib/admin-review-write-gate.ts` | 코드에만 있는 쓰기 스위치. 기본 꺼짐, 활성화 시 지정한 API 주소 하나로만 전송 |
| `frontend/lib/admin-review-flow.ts` | 승인·반려 요청 흐름: 요청 객체 고정, 중복 방지, 수동 재확인, 결과·재조회 |
| `frontend/components/admin/content-review-panel.tsx` | 흐름 연결: 실행 버튼, 진행·결과·불확실·중단 표시, 이탈 경고 |
| `frontend/components/admin/content-detail-screen.tsx` | 성공·충돌 후 상세 재조회 연결(`onReload={retry}`) |
| `frontend/tests/helpers/admin-review-api-fixture.cjs` | (#88) 상세 GET에 역할·상태별 `review` 추가 (BE 판정 서비스와 같은 순서) |
| `frontend/tests/admin-review-e2e.test.cjs` | (#88) 흐름 시나리오 9개 추가, 기존 테스트의 처리되지 않은 rejection 타이밍 수정 |
| `frontend/tests/admin-content.test.cjs` | 운영 패널 비활성 검증과 상태별 화면 테스트 추가 |

기존 `prepareReviewRequest`, `submitAdminContentReview`, `reviewFailure`, `reviewAvailability`, `ContentReviewPanel`, `ContentDetailScreen`을 그대로 재사용했다. 요청·응답 계약과 오류 분류는 바꾸지 않았다.

### 흐름 규칙

1. **실행 가능 여부**: 서버 `review` 메타데이터의 `allowedActions`와 쓰기 스위치가 모두 허용할 때만 실행할 수 있다. 최종 권한·버전·상태 검사는 BE가 한다.
2. **사유와 최종 확인**: 사유를 입력하고 "입력 확인"을 누른 뒤 "승인 실행"/"반려 실행"을 눌러야 보낸다. 실행하면 모달을 닫고 진행 상황은 패널에 표시한다. 모달을 닫아도 요청은 취소되지 않는다.
3. **중복 방지**: 요청 중이거나 결과가 불확실한 동안에는 새 요청을 만들지 않는다. 동시에 여러 번 눌러도 POST는 1번이다.
4. **요청 고정**: 확인한 조치마다 UUID를 1번 만들고, 6개 필드를 `Object.freeze`한 한 객체로 보존한다.
5. **성공 표시**: HTTP 200과 응답 검증을 통과한 뒤에만 "완료"를 표시한다. `auditLogId`, `replayed`, 결과 상태(콘텐츠·검수·초안)를 보여 주고, 상세 GET을 다시 조회한다.
6. **409**: 이 요청을 멈추고 상세(상태·권한 메타데이터)를 다시 조회한다. 자동으로 다시 실행하지 않는다.
7. **결과 불확실**: 503·5xx·408·429·응답 유실·형식이 잘못된 200이 해당한다.
   - "처리 결과를 확인하지 못했습니다"와 요청 번호를 표시한다. 실패로 단정하지 않는다.
   - 사용자가 누를 때만 같은 객체로 최대 3회 재확인하고, 자동 재전송은 하지 않는다.
   - 한도에 도달하면 요청 번호로 BE 감사 기록을 확인하라고 안내한다. 상세 상태만으로 이 요청의 성공을 확정하지 않는다.
8. **401·403·CSRF**:
   - 401 → 로그인 필요 + 재조회.
   - 403 → 권한 없음 + 재조회. 회수·강등 후 재확인도 403이며 성공으로 보지 않는다.
   - CSRF 만료 → 같은 요청으로 수동 재확인. 새 토큰으로 1회만 커밋된다.
9. **화면 이탈·재마운트**:
   - 흐름은 콘텐츠별 모듈 저장소에 있다. 상세 재조회, 앱 안 이동, 모달 닫기로 패널이 다시 그려져도 요청과 결과가 유지된다.
   - 패널이 없는 동안 필요해진 재조회는 다시 붙을 때 1번 실행한다.
   - 요청 중 탭을 닫으려 하면 `beforeunload` 경고를 띄운다. 브라우저 저장소에는 기록하지 않는다.

## 실제 POST 비활성 근거

- **코드 스위치만 있음**: `admin-review-write-gate.ts`의 `ACTIVATION`이 `null`이다.
  - 환경 변수, NEXT_PUBLIC 값, URL query, 저장소, cookie, header 어느 것도 이 값을 바꿀 수 없다. 테스트가 소스에 이런 참조가 없음을 확인한다.
  - 활성화는 검토를 거친 코드 변경으로만 가능하다. 활성화돼도 지정한 API 주소 하나로만 보낸다. 예: Checking 주소로 승인되면 Main API로는 보내지 않는다.
- **운영 패널은 항상 꺼진 스위치를 씀**: 운영 패널은 항상 저장소 흐름(`reviewFlowFor`)과 기본 스위치를 쓴다.
  - 상세 화면은 흐름이나 스위치를 주입하지 않는다(테스트 고정).
  - 스위치가 꺼져 있으면 실행 버튼은 "승인 실행 (미연결)"으로 비활성이다.
  - `execute`·`recheck`를 직접 호출해도 `blocked`가 되고 POST는 0건이다(테스트 고정).
- **POST 경로는 흐름 하나뿐**: 화면에서 `submitAdminContentReview`를 직접 부르는 곳은 없다. 기존 테스트가 고정한다.
- **브라우저 검사 차단**: `admin-review-actions-browser-check.cjs`는 기존대로 동작한다.
  - 시작 주소와 모든 요청을 localhost·127.0.0.1로 제한하고, GET이 아닌 요청은 abort한다.
  - 운영 빌드에서 실행 버튼이 비활성인지 확인한다.
- **POST 시나리오 위치**: POST는 테스트 안에서만, 127.0.0.1의 가짜 API에 그 주소에 묶인 테스트 전용 활성화로 보낸다.

## 활성화 절차 (BE 승인 후, 별도 PR)

1. 아래 BE 선행 조건이 모두 실제로 완료됐는지 BE가 확인한다.
2. 대상 API 주소 하나, 승인자, 날짜, 근거(이슈나 문서)를 정한다.
3. `ACTIVATION`을 채우는 PR을 만든다. 리뷰 후 병합하고 해당 FE를 배포한다.
4. 전용 검수 데이터로만 브라우저 E2E를 실행한다.
5. 브라우저 검사는 "POST 없음" 조건을 실제 흐름 검사로 바꾼 별도 스크립트로 수행한다.

## 검증

| 검사 | 결과 |
| --- | --- |
| 관리자 FE 단위 테스트 (`admin-content`, `admin-content-input`) | 41개 통과 (#91 기준 39 + 신규 2) |
| 격리 HTTP 리허설 (`admin-review-e2e`) | 16개 통과 (#88 기준 7 + 신규 9) |
| 리허설 반복 실행 | 12회 연속 통과 |
| `npm run typecheck`, `npm run build -- --webpack` | 통과 |
| 브라우저 검사 | 이 PC에 Playwright가 없어 실행하지 않음 (문법 확인만) |

반복 실행 중 #88 기존 테스트 1건이 간헐적으로 실패했다. 원인은 읽기 Promise 두 개를 미리 만들어 두면서, 두 번째 rejection이 await 전에 처리되지 않는 타이밍 문제였다. 각 Promise를 await 직전에 만들도록 바꿨다. 검증 기준은 바꾸지 않았다.

## 실제 E2E에 필요한 BE 선행 조건

코드 병합만으로 완료로 보지 않는다.

- [ ] 전용 검수 데이터 또는 분리된 DB. Main과 Checking이 DB를 공유하므로 기존 서비스 콘텐츠는 쓰지 않는다.
- [ ] 대상 DB에 Migration `47c1a8d6b302` 적용 (백업과 복구 절차 준비 후)
- [ ] 대상 API 서버에 PR #89 배포 (Router 등록 포함)
- [ ] `manage_admin_grants.py`로 viewer·operator·admin 실제 권한 부여
- [ ] 쿠키·CORS·CSRF Origin이 정상이고, 상세 GET에 `review`가 포함되며, 역할별 GET·POST 응답이 맞는지 확인
- [ ] 실제 DB에서 감사 기록·replay·적재기 상태 보호 검증
- [ ] 위 확인 후 `ACTIVATION` PR 승인
