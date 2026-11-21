# 관리자 대시보드·분석 조회 API

기준: Checking `6c143d0`, FE [PR #90](https://github.com/Por0ri/me-and-nu/pull/90).
프론트 작업은 11/18 `b24a426`에 작성되어 11/19 13:47 KST에 Checking으로 병합됐다.
해당 화면은 통계·추천 평가 API 계약 없이 `—`를 표시한다. 이 변경은 BE 조회 계약과
평가 결과 생성 경로를 추가한다. FE 연결, 배포, 도메인 분리는 포함하지 않는다.

## 경로와 권한

| GET 경로 | 쿼리 | 응답 |
| --- | --- | --- |
| `/api/v1/admin/dashboard` | `period=7d` | 이용 현황, TOP5, 취향, 기록된 생성 작업, 미처리 항목, 공지 가용성, 추천 성능 |
| `/api/v1/admin/dashboard/attention` | `kind=all`, `page=1`, `pageSize=5` | 미처리 신고·문의 목록과 분류별 건수 |
| `/api/v1/admin/analytics/overview` | `period=7d` | 이용 현황, 가입 코호트 퍼널, 취향, 검색·콘텐츠 TOP5, 재방문 관측률 |
| `/api/v1/admin/analytics/recommendation-performance` | `period=7d`, `audience=all` | 모델별 시리즈 NDCG@10, Hit Rate@10, 표본과 평가 조건 |

viewer/operator/admin의 실제 활성 세션과 현재 DB 권한을 요청마다 확인한다.
일반 회원·권한 회수는 403, 미인증·만료·정지 계정은 401이다. 클라이언트 role과
개발 인증 우회는 관리자 권한을 부여하지 않는다. 읽기 요청에는 CSRF 헤더가 필요 없다.
모든 응답은 `Cache-Control: private, no-store`이며 쓰기나 감사 기록 생성은 없다.
권한 저장소 장애와 통계 DB 오류는 503이다. DB 장애를 빈 목록이나 0으로 대체하지 않는다.
쿼리 검증 실패는 기존 공통 오류 계약의 422다.

`period`는 `7d/30d/3m/6m`이고 각각 현재 시각부터 과거 **7/30/90/180일**이다.
달력 월이나 자정 기준 완결 일자가 아니다. 시각은 UTC offset을 포함하며 `[startAt,endAt)`이다.
FE는 반환된 경계를 KST로 표시한다. 추천 성능의 기간은 **평가 완료 시각**을 필터링한다.
평가 정답 기간은 별도의 `conditions.targetPeriod`다.

`kind`는 `all/report/inquiry/notice`, page는 1~100000, pageSize는 1~100이다.
미처리 목록은 기간 필터와 무관한 현재 잔량이다. 오래된 항목부터 `createdAt, kind, recordId`
순으로 정렬한다. `knownTotal`은 선택 분류에서 구현된 저장소의 항목 수이며 예약 공지를
포함하는 전체 합계가 아니다. 응답에는 문의·신고 원문, 이메일, 회원 ID가 없다.

## 0과 미집계 구분

각 Metric은 `availability`, `reason`, `value`, `unit`, `definition`을 제공한다.
비율에는 `numerator/denominator`도 있다.

| 상태 | FE 표시 규칙 |
| --- | --- |
| `available` | 정의된 저장 기록의 집계. 실제 0은 숫자 0 표시 |
| `partial` | 관측 범위 안의 수치. 정의/제약을 함께 표시하며 완전한 서비스 KPI로 이름을 바꾸지 않음 |
| `unavailable` | `value=null`, `—`와 사유 표시. 분모 0도 0%로 표시하지 않음 |

순위·분포 역시 상태와 사유를 확인한다. 순위의 빈 `items`는 해당 기록이 없다는 뜻이며,
미집계 분포는 `sampleSize=null` 또는 표본 0과 사유로 구분한다.
장애인 503은 별도의 재시도 상태다.

## PR #90 화면 연결과 정의

| 화면 | 응답 필드 / 필요한 표시 |
| --- | --- |
| 이용 회원 | `usage.activeUsers`: 홈 피드 노출 또는 상세 open이 관측된 고유 회원. 전체 방문자와 다름 |
| 가입 후 피드 진입률 | `usage.signupFeedObservationRate`: **가입 후 피드 노출 관측률**로 표시. 기간 내 온보딩 완료 회원 중 완료 후 홈 피드 노출이 관측된 비율 |
| 콘텐츠 조회 수 | `usage.contentReadSegments`: **콘텐츠 열람 구간 수**로 표시. open은 가시성 구간마다 생성되므로 페이지뷰가 아님 |
| 콘텐츠 TOP5 | `topContents`: 같은 open 구간 기준, 동률은 contentId 오름차순 |
| 가입 전환 퍼널 | `funnel`: 가입 시작(미수집), 계정 생성, 같은 생성 코호트의 온보딩 완료, 같은 코호트의 완료 후 피드 노출. 원래 FE의 가입 완료/취향 완료를 별도 수집한 것처럼 표시하지 않음 |
| 온보딩 취향 | `preferenceDistribution`: 선택 기간에 온보딩 완료한 active/warned 회원의 유효 Tap별 **현재** 답변. 수정 이력과 최초 답변은 복원 불가 |
| 인기/무결과 검색 | `popularSearches/zeroResultSearches`: 실행된 검색 문자열별 횟수. 자동완성 제외, 로그 저장은 best effort |
| 재방문율 | `returningUserRate`: 직전 동일 길이 기간 관측 회원 중 현재 기간에도 관측된 비율. D1/D7 가입 코호트 리텐션과 다름 |
| 이용 기기/평균 세션 | `deviceDistribution/averageSessionDuration`: 미수집. 임의 비중/세션 길이로 대체하지 않음 |
| 생성 상태 | `generation`: 최근 24시간 생성된 서비스 DB AgentRun의 현재 상태별 수. 적재기가 성공 기록만 만들 수 있으므로 전체 Worker 성공/실패 현황이 아님 |
| 확인 필요 항목 | `attention`: received/reviewing 신고, received 문의. all은 예약 공지 미구현으로 partial |
| 오늘 공지·예약 | `todayNotices`, `attention.scheduledNotices`: 예약 공지 저장 모델 미구현. 사용자별 Notification 행수로 대체하지 않음 |
| 작품 추천 품질 | `recommendationPerformance`에서 `currentModelId`의 기존/신규 장면 점수를 나란히 표시. 평가 기준 모델이며 현재 배포 모델과 일치한다는 보증은 없음. 클릭은 `/admin/analytics?view=performance` |

사용자 활동/검색은 서버의 `EVENT_DATA_SOURCE`만 집계한다. 클라이언트가 출처를 선택할 수 없다.
응답 `meta.eventDataSource`와 `scope=connected_database`를 표시한다.
**현재 Main과 Checking은 DB와 local_demo 출처를 공유하므로 환경별 트래픽이 아니다.**
회원·취향·신고·문의·AgentRun에는 같은 출처 필드가 없으므로 DB 전체 기록 범위다.
삭제 계정·콘텐츠의 CASCADE로 과거 집계가 달라질 수 있고 늦게 도착하는 이벤트가 있어
응답 시각은 고정된 통계 스냅샷이나 수집 완전성을 보증하지 않는다.

## 추천 평가 데이터

온라인 추천 라운드·클릭 기록만으로 시리즈 정답과 모델 간 동일 후보 조건을 복원하지 않는다.
`ADMIN_RECOMMENDATION_EVALUATION_PATH`에 설정한 서버 소유 JSON 파일을 읽어 검증하고 계산한다.
경로는 요청으로 받지 않고 응답에 노출하지 않는다. 미설정·파일 없음·잘못된 평가 자료는
`unavailable`과 `not_configured/artifact_missing/artifact_invalid` 등으로 반환한다.
파일 최대 크기는 16 MiB이며 원본 사용자 ID·케이스별 순위 배열은 API로 반환하지 않는다.

실제 평가 산출 명령(저장소 루트, backend+model 의존성과 MAL/AniList 원본 데이터 필요):

```powershell
python -m model.eval.admin_performance_export --evaluation-id mal-20261121 --output out/evaluations/admin-performance.json
```

기존 `next_anime_test`의 정답을 가린 표본에서 튜닝 표본을 제외하고 v0.5/EASE/태그/인기순을
같은 후보·필터·K=10 조건으로 새로 실행한다. EXPERIMENTS.md의 반올림된 수치를 복사하지 않는다.
파일은 스키마 검증 후 임시 파일에서 원자적으로 교체한다. 서버 배포 때 해당 파일을 읽기 전용으로
마운트하고 컨테이너 내부 경로를 환경 변수에 설정한다. 요청 시 모델을 학습하거나 평가하지 않는다.
이 변경에서는 원본 데이터 전체 평가와 서버 파일 배치를 수행하지 않았다.

평가 파일에는 버전, 평가 ID/완료 시각, 데이터·케이스 지문, 코드 버전, 단위, 정답 기간,
정답 기준, 후보 정책, 모델 목록, 동일 케이스 순서의 모델별 정답 순위를 기록한다.
다른 표본 크기·중복 모델·K 변경·정답 다중화·시간대 없는 시각·잘못된 순위는 거절한다.
단일 정답의 순위가 r≤10이면 NDCG=`1/log2(r+1)`, Hit=`1`, 실패는 둘 다 `0`이며
각 시나리오의 전체 표본으로 평균낸다. 점수와 Hit Rate의 단위는 **0~1 비율**이다.
`*DeltaVsBaseline`은 같은 시나리오 인기순과의 절대 점수 차이이며 상대 개선율이 아니다.

`audience=all`은 모델마다 `scores`의 existing/new를 함께 반환한다. 하나의 막대 차트에서
모델별 두 계열을 표시하고 필터로 하나만 선택할 수 있다. 두 장면을 합산해 전체 평균을 만들지 않는다.
현재 exporter의 existing/new는 **동일 보류 표본에 전체 기록/온보딩 입력을 준 시뮬레이션 장면**이다.
실서비스 가입 일수로 나눈 사용자 집단과 다르며 `conditions.cohortInterpretation=input_scenarios`와
각 `cohorts[].definition`을 화면에서 확인할 수 있어야 한다. 점수가 없는 장면을 0으로 채우지 않는다.
신규 입력 장면에서도 공유 CF 통계에는 그 사람의 정답 이전 시청 기록이 포함될 수 있으므로
학습에 전혀 등장하지 않은 신규 회원의 cold-start 검증과 다르다. `currentModelId`는 이 평가에서
선택한 비교 기준 모델이다. 실서비스 모델 산출물·서비스 확장 데이터까지 평가했다고 해석하지 않는다.

## 검증

2026-11-21 로컬 검증 결과:

| 범위 | 결과 |
| --- | --- |
| 신규 집계 SQL·HTTP 권한/계약·추천 평가 | 75 passed |
| 기존 관리자 콘텐츠·권한·검수 scope/FE 계약·OAuth 설정·CORS 회귀 | 202 passed |
| 격리 PostgreSQL 17.11 집계 시나리오 + 실제 Migration 스키마/AsyncSession | 11 passed |
| diff 공백 검사 | 통과 |

분자·분모와 퍼널 단계는 각각 단일 SQL 문장의 같은 스냅샷에서 집계한다.
응답의 모든 패널을 하나의 영구 스냅샷으로 고정하지는 않는다.

새 SQL 집계·HTTP 권한·평가 파일/계산 테스트와 기존 관리자 회귀 테스트를 실행한다.
PostgreSQL 검증은 운영 DB 대신 로컬 임시 pgvector 컨테이너에서 실행한다:

```powershell
cd backend
python -m pytest tests/test_admin_analytics.py tests/test_admin_analytics_api.py tests/test_admin_recommendation_analytics.py -q
python scripts/run_admin_postgres_checks.py --test-file tests/test_admin_analytics_postgres.py
```

신규 Migration은 없다. FE 연결 전에 지표 명칭 조정과 null/partial 표시 규칙을 적용하고,
배포 후 viewer/operator/admin의 실제 세션으로 네 경로를 확인한다. 도메인 분리는 그 이후 판단한다.
