# 기존 등록 출처의 일일 자동 수집 연결 검토 — 2026-11-26

기준 커밋: `bfe64aab259373251bb9aacaf745b5ecd6f592bc` (`main`). 이 문서는 출처 확장 검토이며, 출처 설정·Worker·Agent·스케줄을 변경하지 않았다. 외부 수집, LLM 호출, 서버 작업, 서비스 DB 쓰기는 수행하지 않았다. 같은 작업 브랜치의 사용량 기록 수정과는 별도 범위다.

**PR TIMES·EEO·Inside·AnimeAnime는 기존 Agent 수집 기능을 재사용해 연결할 수 있다. 다만 현재 일일 큐는 작품별 공식 홈페이지만 받도록 좁게 구현되어 있으므로, 설정 한 줄로 켤 수 있는 상태는 아니다.** 여러 작품을 다루는 매체의 기사별 작품 확인, 상세 기사 URL 허용 범위, 일일 배분 계약을 함께 확장해야 한다. ANN 리뷰와 Filmarks 큐레이션은 현재 뉴스·행사·굿즈 범위 밖이다.

## 1. 확인 범위와 근거의 한계

- 코드와 등록 JSON/CSV를 읽고, 네트워크를 사용하지 않는 Worker 검증 함수로 현재 라우트와 예시 기사 URL의 거절 사유를 확인했다. 실행 시 바이트코드 저장도 비활성화했다.
- 앞선 별도 서버 읽기 전용 집계는 전체 콘텐츠 6,321건 중 `dict` 6,074건, `crawl` 230건, 기타 17건이었다. `crawl` 출처별 집계에는 Filmarks 45건, PR TIMES 26건, EEO 20건, ANN 17건, Inside 16건, AnimeAnime 15건이 포함됐다. 이 수치는 앞선 집계 결과를 인용한 것이며 이번 문서 작업에서 DB를 다시 조회하지 않았다. 집계의 매체 단위와 설정의 개별 source ID는 구분해야 한다.
- 과거 적재 실적은 해당 매체를 활용했던 증거다. 오늘도 접근 가능하거나 현재 큐의 입력 계약을 만족한다는 증거는 아니다. 기존 6천여 건 대부분이 `dict` 계열이므로, 현재 공식 기사 16개 라우트만으로 생산한 결과라고 설명하면 안 된다.
- 앞선 11월 26일 진단에서 주술회전·히로아카·하이큐·스파이패밀리·프리렌의 `/news/` 응답은 Cloudflare challenge를 동반한 403이었다. 해당 진단은 목록 파싱 이전 실패를 보여 주며 robots.txt 금지와는 구분된다. 이번 검토에서는 사이트를 다시 요청하지 않았다. 접근 차단 우회는 확장안에 포함하지 않는다.

## 2. 현재 연결이 좁아지는 지점

| 단계 | 현재 계약 | 매체 출처를 추가할 때의 영향 |
| --- | --- | --- |
| Agent 등록 | `status=active`, 지원 유형, collection/role, `discovery_enabled`를 따로 검사한다. `discovery_enabled` 생략 시 모델 기본값은 `true`다. | 등록됨·발견 활성화·사실 근거 허용은 서로 다른 조건이다. `source_type_map`도 수집 허가 자체가 아니다. |
| 일일 라우트 | `primary` + `html` + `official_news` + 출처에 연결된 기존 작품 정확히 1개. 현재 7작품/16조합이다. | RSS·editorial·작품 공통 매체는 제외된다. 일반 발견이 꺼진 공식 출처도 명시적 작품별 일일 경로에서는 사용할 수 있다. |
| 발견→기사 변환 | 발견용 출처는 등록된 원문 출처로 해석한 뒤, 원문 출처의 작품을 하나로 결정한다. | 원문 매체 자체가 여러 작품을 다루면 `work_title_*`가 없어서 멈춘다. |
| Worker URL 검증 | 정확한 등록 호스트, 목록 URL의 디렉터리 하위 상세 기사, 자산/목록 제외. | `/category/...` 목록에서 `/article/...`로 가는 정상 기사도 현재는 거절된다. |
| Worker 작품 검증 | 요청한 작품이 기존 CSV에 있고, 출처의 작품 제목과 정확히 한 개로 일치해야 한다. | PR TIMES 등 공통 매체에 임의 작품 제목을 넣는 방식은 부적절하다. |
| Agent 실제 생성 | `--expected-work-id`에서 출처 작품을 먼저 검사하고, 수집한 기사 제목·구조화 근거·등록 공식 링크로 기사 작품을 다시 확인한다. | 백엔드만 완화하면 Agent에서 다시 차단된다. 기사별 작품 증거를 받는 계약을 양쪽에서 맞춰야 한다. |
| 일일 배분 | `(source_id, content_type)`는 작품 하나에만 대응하며, 발견 결과의 source/type/work가 라우트와 정확히 같아야 한다. | 한 매체에서 여러 작품이 발견되는 구조나 RSS ID와 실제 원문 ID가 다른 구조를 표현할 수 없다. |

관련 코드: `worker/menu_worker/daily_routes.py:17,91`, `anime_catalog.py:127,156,196,220`, `discovery_agent.py:54,118,184`, `daily_allocation.py:67`; Agent `sources/models.py:18`, `discovery.py:503`, `cli.py:1858,2032`.

일반 Beat의 `ANIME_DISCOVERY_ROUTES`와 현재 횟수 제한 일일 실행은 별도 경로다. 매체 확장을 위해 일반 Beat를 같이 켜면 안 된다. 승인된 일일 실행 안에서만 한 건씩 발행해야 하루 40회 시도·20건 검수 통과 목표를 하나의 이력으로 관리할 수 있다.

## 3. 출처별 연결 가능성

모든 판단은 **정적 등록/코드 기준**이다. 아래의 가능성은 실시간 접속 성공을 의미하지 않는다.

| 출처 / source ID | 현재 등록·유형 | 일일 큐에 바로 연결 가능한가 | 최소 수정 범위 |
| --- | --- | --- | --- |
| PR TIMES `prtimes_rss` → `prtimes_release_pages` | RSS는 active/discovery/발견 on, 원문은 active/primary/html/발견 off. 공통 지원은 news/event/goods, 두 출처 모두 고정 작품 없음. | 불가. RSS 발견기는 이미 있지만 현재 일일 라우트에서 제외되고, PR TIMES 원문은 작품 미확정으로 거절된다. | RSS를 발견 경로, 등록된 보도자료 상세 페이지를 근거 경로로 분리한다. 같은 PR TIMES 기사 URL을 `prtimes_release_pages`로 명확히 연결하고 기사별 기존 작품을 확인한다. 발표 기업/주최자 자신의 상품·행사 발표라는 기존 근거 범위를 유지한다. RSS 전체를 사실 출처로 승격하거나 홈페이지 직접 폴링을 켜지 않는다. |
| EEO `eeo_events` | active/editorial/html/발견 on, event/goods, 고정 작품 없음. | 불가. Agent의 행사 기사 파서와 편집 기사 근거 정책은 존재한다. Worker의 경로·작품 검사와 일일 라우트가 막는다. | 등록 목록 `/media/category/event/`와 상세 기사 `/media/YYYY/MM/DD/숫자/`를 구분한 명시적 경로 규칙, 기사별 작품 근거, 행사 기간 검증을 연결한다. 주최자·행사 내용·출처 링크를 보존하고 editorial 표시를 유지한다. |
| Inside `inside_goods` | active/editorial/html/발견 on, goods, 고정 작품 없음. | 불가. Agent는 편집 굿즈 기사를 지원한다. 목록 `/category/figureandgoods/goods/latest/`와 상세 `/article/...`가 달라 Worker에서 거절된다. | 상세 기사 패턴 허용과 기사별 작품 확인. 제품·판매자/제조사·판매/출시 조건을 원문 근거로 확인한다. 기존 이미지 권리·기간 검증을 유지한다. |
| AnimeAnime `anime_anime_goods` | active/editorial/html/발견 on, goods, 고정 작품 없음. | 불가. Inside와 같은 공통 장벽이다. | `/category/news/goods/latest/`와 `/article/...`를 분리하고 기사별 작품 증거를 연결한다. Inside와 공통 어댑터 계약을 쓰되 호스트와 허용 경로는 각각 등록한다. |
| Inside `inside_figures` | active/discovery/html/발견 on, goods. | 불가. `inside_goods`와 달리 발견용 역할이다. | 제조사/판매자 등 허용된 원문으로 해석하는 경로가 필요하다. 공통 호스트라는 이유로 `inside_goods` 또는 primary로 바꾸지 않는다. 최초 확장에서는 제외하는 편이 단순하다. |
| ANN `anime_news_network` | active/editorial/html, 발견 기본값 on, **review만 지원**. URL도 `/review/`이다. | 현재 범위 밖. 매체 이름 때문에 뉴스 라우트로 취급하면 안 된다. | 리뷰 유형의 입력·검수·출력·배분 정책을 별도로 확장할 때 다룬다. 현재 news/event/goods 어댑터에 review를 강제로 넣지 않는다. |
| Filmarks `filmarks_anime` | active/editorial/html, 발견 기본값 on, **curation만 지원**. | 현재 범위 밖. 기존 45건을 뉴스·행사·굿즈의 수집 성공 근거로 사용할 수 없다. | 향후 큐레이션 경로에서 출처가 명시된 집계 평점만 다룬다. 기존 정책대로 개인 리뷰 본문·작성자 데이터는 수집 대상이 아니다. |
| 약사의 혼잣말 `src_kusuriyanohitorigoto_jp` | active/primary/html/발견 on, category=event, event만 지원. `/news/` 목록이지만 고정 작품 필드는 없음. | 현 상태는 불가. 기존 CSV에 작품 ID `90`이 있어 공통 매체보다 작은 확장 후보이다. | 등록 출처와 작품 90의 명시적 연결 및 실제 상세 경로 fixture를 검증한 뒤, 허용된 단일 작품 공식 경로로 추가한다. 통과만을 위해 category를 바꾸거나 지원 유형을 임의로 넓히지 않는다. 실제 접근 가능성은 별도 확인 대상이다. |

등록 근거: `agents/ani_me;nu/config/sources.json:129,141,309,351,778,924,936,960,1084`; 지원 유형은 같은 디렉터리 `source_type_map.json:59,66,130,136,302,348,351,357`.

추가로 MegaHobby는 현재 discovery/goods, FuRyu는 primary지만 `source_discovery`이며 노트에 발견용으로만 사용한다고 되어 있다. 표면적인 `active`/`primary` 값만 보고 자동 생성 근거로 추가할 수 없다. 각 등록의 역할·노트·상세 URL 범위를 먼저 정합하게 해야 한다.

## 4. 네트워크 없는 현재 동작 확인

실제 등록 JSON과 `catalog/menu_works.csv`를 메모리 경로 어댑터로 읽어 현재 함수만 호출했다. JSON/CSV·서비스 설정은 수정하지 않았다. 기사 URL은 저장된 테스트의 예시를 사용했고 HTTP 요청은 없었다.

| source ID | 예시 상세 경로 | `_is_registered_article` | `_validate_source_work` | `catalog_routes` 제외 사유 |
| --- | --- | --- | --- | --- |
| `prtimes_release_pages` | `/main/html/rd/p/000000001.000000002.html` | 통과 | `source_work_unresolved` | `unsupported_daily_source` |
| `eeo_events` | `/media/2026/10/28/407578/` | `source_path_mismatch` | `source_work_unresolved` | `unsupported_daily_source` |
| `inside_goods` | `/article/2026/10/29/190017.html` | `source_path_mismatch` | `source_work_unresolved` | `unsupported_daily_source` |
| `anime_anime_goods` | `/article/2026/10/29/104305.html` | `source_path_mismatch` | `source_work_unresolved` | `unsupported_daily_source` |

이는 기존 보호 검사가 의도대로 거절하는 결과다. 해당 검사를 삭제하는 대신 명시적으로 검토된 매체용 계약을 추가해야 한다. 이번에는 fixture 확장 테스트나 Agent 전체 회귀 테스트를 새로 실행하지 않았다.

## 5. 권장 구현 범위

우선 PR TIMES 원문 1개와 EEO 또는 굿즈 매체 1개를 동일한 기사별 작품 검증 경로에 연결하는 구현을 제안한다. 현재 접근이 확인되지 않은 후보 간 우선순위이며, 성공률을 보장하는 순위는 아니다. 이후 Inside·AnimeAnime는 같은 계약으로 넓힐 수 있다.

1. **발견 경로와 생성 원문을 분리한다.** 승인된 발견 출처·유형과 허용 원문 출처를 명시한다. RSS ID, 원래 발견 URL, 최종 원문 source ID/URL을 모두 남긴다. 기존 공식 작품 라우트는 그대로 읽을 수 있어야 한다.
2. **기사별 기존 작품 증거를 계약에 넣는다.** 기존 `resolve_work_matches`의 제목·구조화 `about`·명시적 등록 공식 링크 판정을 재사용한다. 단일 기사/단일 작업 정책에서는 정확히 하나의 기존 작품만 허용한다. 여러 작품, 작품 없음, 다른 작품, 카탈로그 불일치는 유료 생성 전에 보류/제외한다. 요청자의 작품 ID나 매체 홈페이지 이름만 믿지 않는다. Agent도 수집한 기사에서 같은 작품을 다시 확인한다.
3. **목록과 상세 기사 URL을 구분한다.** 출처별 검토된 상세 경로만 허용한다. 호스트만 맞으면 모든 경로를 허용하는 방식, 목록·이미지·검색 결과·로그인 페이지의 기사 취급은 금지한다. HTTPS·공개 DNS·리디렉션·canonical·쿼리 검사를 유지한다.
4. **기사의 실제 원문 기준으로 중복을 막는다.** 발견 경로가 둘이어도 동일한 원문 `source_id + canonical URL`이면 하나로 합친다. 기존 생성 시작 이력·실패 이력·내보내기 기록은 그대로 사용한다. 서로 다른 매체의 비슷한 보도는 URL 중복만으로 동일 콘텐츠라고 판정할 수 없으므로, 매체 확장 시 의미 중복까지 해결됐다고 보고하면 안 된다.
5. **배분과 정책 버전을 함께 확장한다.** 매체별 후보를 한 번 수집한 뒤 검증된 실제 작품·유형으로 배분한다. 같은 목록을 작품 수만큼 반복 요청하지 않는다. 기존 `(source,type)→작품 1개` 제한을 완화하는 변경은 일일 정책·체크포인트·import policy·host supervisor 검증까지 일관되게 버전 관리한다. 과거 라우트/해시를 덮어쓰거나 source ID를 작품별 복제해 중복 키를 바꾸지 않는다.
6. **운영 범위는 그대로 둔다.** KST 하루 40회 시도·검수 통과 20건 목표, 단건 순차 실행, 실패/생략의 사용량 기록, 비용 불명확 시 중단, 검수 대기 적재와 운영자 승인 후 공개를 유지한다. 현재 미완료 일일 이력을 정리하지 않고 새 정책으로 새 실행을 열지 않는다. 기존 release binding 마이그레이션은 라우트·정책 범위 변경을 허용하는 도구가 아니다.

작품 증거 로직: Agent `work_catalog.py:593`; 사실 근거 역할 정책: `source_policy.py:55`; 기간 보류/만료 판정: `cli.py`의 `expiry_evidence`, `news_expiry_evidence`, `write_early_result` 호출부. Agent는 이미 일부 editorial 기사와 출처가 명시된 reference 경로를 지원하지만, 이번 확장에서 discovery의 reference fallback까지 한꺼번에 넓힐 필요는 없다.

## 6. 유지할 원문·기간 검증

- 목록 날짜나 URL의 연도만으로 행사·판매 기간을 확정하지 않는다. 원문 일정·주최자/판매자·제품 식별 정보와 해당 정보의 출처 URL을 남긴다. 기간이 끝난 후보는 생략하고, 불명확한 후보는 보류한다. 진행 중/발매 예정/상시 판매 등 구분은 기존 `expiry_evidence` 규칙을 따른다.
- 일반 뉴스와 즉시 행동이 필요한 시의성 뉴스를 구분한다. 시의성 뉴스의 날짜가 없거나 지난 경우 기존 차단을 유지한다. 현재 없는 일률적인 기사 나이 제한을 이번 문서에서 새 정책으로 정하지 않는다.
- 원문 수집 실패를 다른 매체의 글로 몰래 대체하지 않는다. 편집 매체를 독립 근거로 쓰려면 그 매체의 등록된 역할과 출처 표기를 유지한다. RSS로 후보를 찾았더라도 실제 필수 원문이 403이면 그 기사의 원문 확인 문제는 남는다.
- robots.txt, 공개 주소 검증, 요청 간격, 응답 크기·시간 제한을 유지한다. CAPTCHA/Cloudflare 우회, IP 회전, 로그인/쿠키 차용, 다른 User-Agent 위장은 제안하지 않는다. 허용된 RSS/API·정상 공개 기사 또는 출처 측 허용 경로를 사용한다.
- 사실 검수 blocker, 이미지 배정·권리·표지 중복, 2장 뉴스/행사/굿즈 규칙, 적재 전 승인 상태를 완화하지 않는다. 수집 가능한 후보 수와 실제 검수 통과 수는 다르다.

### 발견 단계의 비용 경계: 확장 전에 고정할 조건

추가 읽기 전용 검토에서 다음을 구분했다.

- 일반 Agent `crawl-generate --candidate-id`의 발견 매체 fallback은 `matching_sources`가 없을 때 `discovery_capture = collect_official_page(...)`를 호출한다. **이 직후 `discovery_capture.link_review_usage`를 `work_match_usage_records`에 더하는 코드는 없다.** 원문을 확정하지 못한 HTML 발견 기사에서는 `original_source_unresolved` 보고서를 별도로 저장하고 반환하며, 이 보고서는 공통 `write_early_result`를 거치지 않는다. 원문을 찾은 경우에도 발견 페이지의 사용량과 이후 원문 수집 사용량을 각각 보존하는 연결을 확인해야 한다.
- 그렇다고 현재 해당 발견 fallback에서 유료 링크 검토가 실행된다고 볼 수는 없다. 그 호출은 `review_ambiguous_links`를 생략하고, `collect_official_page`의 기본값은 **False**다. `classify_link_candidates`도 `allow_llm=False`이면 모델 호출 전에 반환한다. 확인된 것은 현재 비용 발생이 아니라 **향후 이 옵션을 켜거나 수집기를 교체할 때 비용 기록이 빠질 수 있는 연결 지점**이다.
- 별도의 일반 candidate 작품 사전 확인에서는 `review_ambiguous_links=not prepare_only`를 사용하므로, 준비 전용 실행이 아니면 모델 검토를 허용할 수 있다. 해당 capture의 사용량은 누적하고 있지만, 일반 Agent CLI 자체가 일일 Celery 횟수 예약을 수행하는 것은 아니다. 이 CLI를 그대로 등록 전 후보 발견 단계로 옮기면 하루 40회 제한 밖에서 비용이 생길 수 있다.
- 현재 일일 Celery 경로는 `--candidate-id`를 사용하지 않는다. `--expected-work-id`와 candidate 입력은 함께 사용할 수 없고, 발견 subprocess는 키가 없는 `_environment()`와 저수준 HTML/정적 링크 파싱을 사용한다. 실제 작업은 `daily_allocation.run_allocated`에서 `counts_slot=True` 기록을 먼저 저장한 뒤 순차 실행한다. 따라서 위 fallback 문제를 현재 일일 큐의 실제 과금 누락으로 보고하지 않는다.

확장 시 발견은 **무과금 고정**이어야 한다. RSS/HTML 후보 읽기·정적 원문 링크 확인·기존 카탈로그 작품 매핑까지만 허용하고, `review_ambiguous_links=False`를 명시하며 키 제거와 외부 모델 호출 차단을 유지한다. `prepare_only`라는 이름이나 “크롤링”이라는 단계명만으로 무과금이라고 판단하지 않는다.

모델을 통한 링크 검토·OCR·작품 추론이 꼭 필요하다면 일일 시도 슬롯과 작업을 먼저 영속적으로 예약한 뒤 그 작업 안에서만 수행한다. 현재 입력 계약으로 안전하게 접수할 수 없는 모호한 후보는 보류한다. 제한 밖의 임시 LLM 호출로 작품 ID를 만들어 접수하지 않는다. 발견 원문·연결 원문 각각의 사용량을 한 번씩 누적하고, 정상 생성뿐 아니라 원문 미확정·작품 불명·기간 보류·예외 종료에서도 기록한다. 호출 시작 후 사용량을 확인하지 못한 경우에는 0건/0달러로 바꾸지 않고 비용 미확정으로 남겨 중단해야 한다.

근거: Agent `cli.py`의 `discovery_capture` 생성 및 `original_source_unresolved` 분기, `identity_preflight_capture` 수집 옵션; `sources/page_collector.py:1658,1817`, `sources/link_review.py`의 `allow_llm` 조기 반환; Worker `real_generation.py:80`, `discovery_agent.py:44,189`, `daily_allocation.py:128`.

## 7. 후속 무과금 fixture 테스트 계획

외부 소켓을 차단하고 RSS/HTML/robots/리디렉션 응답·생성 결과를 fixture로 주입한다. LLM 클라이언트 호출은 즉시 실패하도록 막고, Redis/Celery/DB 통합은 별도 격리 환경에서만 실행한다.

| 검증 영역 | 필요한 사례 | 합격 기준 |
| --- | --- | --- |
| 등록·역할 | active/disabled/terms_review/permission_required, 지원하지 않는 유형, editorial·discovery 구분 | 미승인 출처는 요청 전 차단. RSS 단독을 공식 사실로 승인하지 않음. 기존 공식 16개 라우트 유지. |
| 목록→상세 | PR TIMES RSS 원문, EEO 날짜 상세 경로, Inside/AnimeAnime `/article/`, 목록·이미지·다른 카테고리 | 승인 상세 기사만 통과. 일반 홈페이지/자산/타 출처/비공개 주소와 잘못된 canonical은 거절. |
| 작품 | 기존 작품 1개, 별칭, 제목과 `about` 충돌, 여러 작품, 미등록 작품, 다른 작품으로 리디렉션 | 단일한 기존 ID와 원문 증거가 맞을 때만 접수. 신규 작품 생성이나 LLM 추정 없음. |
| 유형·기간 | news/event/goods 혼합, 만료 행사, 예약 종료, 발매 예정, 연도 없는 날짜, 상품명만 있는 기사, 시의성 뉴스 | 원문 기준 판정 유지. 만료 생략·불명확 보류. held/skipped도 실제 관측 사용량과 누락 여부가 기록됨. |
| 기사 중복 | RSS와 HTML이 같은 원문 발견, 끝 `/`, 추적 쿼리, 식별 쿼리 차이, 동시 접수, 재시작, 과거 생성 시작 이력 | 같은 원문은 작업/생성/내보내기 각각 한 번. 서로 다른 식별 쿼리는 보존. 과거 실패·비용 이력 불변. |
| 배분·한도 | 매체 한 곳에 한 작품 후보 과다, 다른 작품 후보 부족, 전체 40번째 시도·20번째 통과, 자정/재시작 | 가능한 후보 안에서 작품·유형을 배분. 한 번에 생성 작업 1개. 전체 일일 한도 유지. 새 정책으로 한도 리셋 불가. |
| 장애·범위 | 403 challenge, robots 거절, timeout, 잘못된 RSS/HTML, 빈 목록, 미확인 원문 링크 | 실패 단계 구분. 차단 우회/외부 fallback 호출 없음. 사용할 수 없는 매체가 다른 정상 후보의 이력을 훼손하지 않음. |
| 등록 전 비용 경계 | 모호한 관련 링크가 있는 발견 페이지, 원문 미확정, 일반 candidate 경로의 오사용, 환경에 키가 있는 경우 | 발견 중 모델 mock 호출 0. 키를 상속하지 않고 링크 검토 옵션은 False. 새 매체 adapter도 슬롯 예약 전 OCR·작품/링크 LLM을 호출하지 않음. |
| 작업 내부 사용량 | 예약된 fake 작업에서 발견/원문 링크 검토 영수증 각각 반환, 이후 보류·생략·예외, 사용량 없는 응답 | 원문 단계별 영수증이 누락·중복 없이 합산됨. 유료 가능 시도는 한도에 포함. 미확정 비용은 0으로 오인하지 않고 중단. 실제 모델 호출 없이 fixture로 검증. |
| 적재·피드 | 동일 검수 통과 번들 2회, 승인 전/승인 후/반려 | 격리 DB 콘텐츠 1개, 승인 전 비노출, 승인 후 1개 노출. 기존 추천 피드 회귀 유지. |

재사용할 기존 테스트: `worker/tests/check_discovery_agent.py`, `check_daily_discovery_agent.py`, `test_anime_catalog.py`, `test_daily_routes.py`, `test_daily_count_runtime.py`, `test_jobs_postgres.py`; Agent `tests/test_source_policy.py`, `test_period_article_images.py`, `test_work_catalog.py`, `test_returned_goods_work_identity.py`, `test_expired_generation_jobs.py`. 이 목록은 후속 검증 계획이며 새 연결이 구현되거나 통과했다는 보고가 아니다.

출처 연결 코드·새 정책·실제 접속 점검·재배포·유료 재개는 이 문서 범위에 포함되지 않는다. 이번 변경은 검토 문서 추가뿐이다.
