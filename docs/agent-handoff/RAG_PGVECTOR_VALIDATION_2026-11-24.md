# RAG 벡터 저장소 이전 · 2026-11-24

기준은 PR #110이 반영된 Checking `6bccf07591ad94c65bf61a43ea0ddfdfb32d5df2`다. 이 작업은 별도 브랜치 `codex/rag-pgvector-20261124`에서 수행한다. 운영 반영·자동 수집·유료 생성·기존 작업 재개는 승인 범위가 아니다.

## 보존 범위

- 인계된 원문 SQLite, 초안·콘텐츠·작업·검수 이력, 이미지 원본은 그대로 보관한다. 벡터 이전과 비교 과정에서 읽기 전용으로 연다.
- 벡터 sidecar의 `index_metadata`, `embedding_vector`, `evidence_chunk`를 별도 PostgreSQL DB의 `rag_vector` 스키마로 옮긴다. 서비스의 `content.embedding`이나 추천 표를 사용하지 않는다.
- `text-embedding-3-small`, 1,024차원, 기존 float32 바이트와 `text_hash`, 문서·근거 조각 ID, 원문 URL, 출처, 인용 위치를 보존한다. 재임베딩하지 않는다.
- PostgreSQL 점수는 `1 - (embedding <=> query)`이고 기존 0~1 범위 처리, 작품·출처·근거 유형 제한, 후보 URL 제외, 인용 근거 우선순위, 동점 정렬과 lexical fallback을 유지한다. 기존 후보 범위 내 정확 검색이며 이번 범위에 근사 검색 인덱스 변경은 없다.

## 구현

`ani_rag.storage.postgres_vector`는 전용 DB 이름을 명시적으로 확인하고, 원본 벡터와 pgvector 값을 함께 저장한다. 읽기 어댑터는 읽기 전용 트랜잭션으로만 검색한다. DB URL은 지정된 환경변수에서 읽으며 출력하지 않는다.

초기 적재는 원본 두 파일의 SHA-256에 연결되고, 배치 저장과 재시작 위치가 한 트랜잭션에서 기록된다. 같은 인계본을 다시 실행하면 중복으로 넣지 않는다. 다른 인계본으로 덮어쓰거나 같은 ID의 내용이 달라지면 중단한다. 적재가 완료되기 전에는 검색·갱신을 허용하지 않는다.

PR #111 최종 리뷰에서 기본 읽기 옵션이 WAL에만 저장된 변경을 본체 SHA로 식별하지 못해 `already_imported`로 건너뛰는 문제를 재현했다. 초기 적재는 이제 **두 읽기 옵션 모두** 비어 있지 않은 WAL·journal을 거부한다. 실행 중 DB를 그대로 전달하지 말고 일관된 백업 사본을 준비해야 하며, 복구 파일을 임의로 삭제해서는 안 된다. 원본을 체크포인트하거나 수정하는 동작은 추가하지 않았다.

초기 적재 동안 일반 읽기 트랜잭션을 고정하고, 파일 식별자·크기·수정 시각 및 복구 파일 유무를 배치 전후·커밋 전에 확인한다. 완료 및 `already_imported` 반환 전에는 전체 SHA를 다시 대조한다. 도중 입력이 변한 부분 적재는 `input_changed`로 남겨 검색·갱신·재개를 차단한다. 원본을 복구해도 이 부분 목적지는 자동 재사용하지 않으며, 이력을 보존하고 새 전용 목적지로 다시 검증해야 한다. 정상 완료된 목적지에 잘못된 새 입력을 제출하면 기존 데이터와 완료 상태는 유지하고 새 요청만 거부한다. 입력 불변 상태에서 일반 중단된 작업은 기존 커서로 재개할 수 있다.

증분 갱신은 기존 `last_source_raw_document_id` 뒤부터 읽는다. 같은 원문이 여러 배치에 걸쳐도 완료되지 않은 문서를 건너뛰지 않는다. 완료한 원문 ID·URL·content_hash의 지문도 함께 전진시키고, 재시작 때 과거 원문이 바뀌었는지 확인한다. 동시에 실행한 적재·갱신은 advisory lock으로 직렬화한다.

이번 증분 CLI는 **이미 저장된 벡터만 재사용**한다. 새로운 본문의 벡터가 없으면 유료 호출 없이 중단하고 그 문서를 완료 처리하지 않는다. 라이브러리의 명시적 임베딩 공급자 주입 지점은 테스트와 향후 승인된 연결을 위한 것이며, 실제 모델 클라이언트는 구성하지 않았다.

## 명시적 실행 도구

PostgreSQL 선택 의존성은 `ani-rag[postgres]`다. `agents/ani_rag`에서 `uv sync --frozen --extra postgres --python 3.12`로 설치한다. `uv.lock`에는 검증에 사용한 `psycopg`와 `psycopg-binary` 3.3.6을 추가했고 기존 패키지 버전은 유지했다. 아래 도구는 자동으로 실행되지 않는다. 실제 환경에서는 전용 DB·읽기/쓰기 계정과 백업·용량을 별도 준비한 뒤 검토한 설정으로 사용한다.

```sh
python scripts/import_pgvector_sidecar.py \
  --source-db /verified/agents/ani_rag/data/ani_rag_live_20261111.sqlite3 \
  --index-db /verified/agents/ani_rag/output/ani_rag_evidence_vectors_20261119.sqlite3 \
  --database-url-env ANI_RAG_VECTOR_DATABASE_URL \
  --expected-database menu_rag_vector_test --immutable-snapshot

python scripts/update_pgvector_evidence_index.py \
  --source-db /verified/agents/ani_rag/data/ani_rag_live_20261111.sqlite3 \
  --database-url-env ANI_RAG_VECTOR_DATABASE_URL \
  --expected-database menu_rag_vector_test --immutable-snapshot
```

`--immutable-snapshot`은 해시를 확인한 고정 인계본에만 사용한다. 실행 중인 DB에는 사용하지 않는다. 인계된 벡터 DB는 WAL 모드 헤더가 남아 있어, 일반 읽기 전용 연결은 읽기 전용 볼륨에서 보조 파일을 만들지 못해 실패했다. 원본 수정 없이 SQLite의 고정 스냅샷 읽기를 명시적으로 선택하도록 보완했다. 초기 적재에서는 이 플래그 유무와 관계없이 복구 파일·입력 변경을 검사한다. 일반 증분 실행은 기존처럼 실시간 WAL의 커밋된 자료를 읽으며 이번 변경으로 제한하지 않는다.

실제 대용량 인계본의 사전 대조에서는 Windows 바인드 볼륨의 반복 임의 읽기가 병목이었다. 원문 ID·출처·URL은 한 번 순서대로 읽어 대조하고, 초기 복사도 SHA로 고정된 SQLite 파일의 행 위치를 이용해 순서대로 진행하도록 보완했다. 이는 초기 복사의 재시작 위치일 뿐 문서·근거 ID나 기존 증분 문서 체크포인트를 바꾸지 않는다.

기존 `run_worker.py`에는 SQLite `--vector-db` 대신 `--vector-database-url-env`와 `--expected-vector-database`를 선택하는 연결부를 추가했다. **이 명령은 실제 워커 실행기이므로 이번 검증에서는 호출하지 않는다.** 원문·작업 SQLite와 이미지는 계속 별도이며, 이번 벡터 이전만으로 RAG 전체가 PostgreSQL이나 Celery로 이전되는 것은 아니다.

## 검증 환경과 결과

데이터·검색 검증은 외부 포트가 없고 외부 통신을 차단한 전용 Docker 네트워크와 PostgreSQL에서 수행했다. 모델 키는 없고 원본 파일은 읽기 전용으로 연결했다. 별도의 설치 검증은 임시 컨테이너에서 패키지 다운로드만 허용하고, 키·원본 DB·서비스 설정을 연결하지 않은 상태로 수행했다.

| 확인 | 현재 결과 |
| --- | --- |
| RAG 회귀(PostgreSQL 저장소·초기 입력 보호 테스트 제외) | WAL 수정 후 149 passed, 62 subtests passed, 29.86초. 네트워크 없음 |
| PostgreSQL 저장소 단위·실제 DB 통합 | 최초 24 passed, 6.09초. 일회성 테스트 DB별 생성·정리. 아래 WAL 수정 회귀에서도 재통과 |
| 초기 이전 WAL 누락 수정 회귀 | 기존 저장소 24건 + 신규 입력 보호 20건, 총 44 passed, 8.76초. 별도 임시 PostgreSQL 사용 |
| SQLite/pgvector 검색 연결부 회귀 | 위 RAG 회귀에 포함. 별도 선행 실행은 19 passed, 17 subtests |
| 잠금 파일·재현 설치 | `uv lock --check`, `uv sync --frozen --extra postgres --no-dev` 및 PostgreSQL 어댑터 import 통과. 기존 패키지 버전 변경 0개, 모델 호출·DB 연결 0건 |
| 실제 인계본 복원 | 2,800개 파일의 SHA-256 전부 일치 |
| 원본 두 SQLite DB | integrity_check=ok, 외래키 위반 0건 |
| 전수 벡터 대조 | 706,246개 원래 float32 바이트 및 pgvector 값·메타데이터 일치 |
| 전수 근거 대조 | 736,622개 모든 필드·ID·원문 URL·출처·인용 위치·text_hash 일치 |
| 실제 검색 비교 | 9개 출처·7개 작품, 115개 사례의 상위 결과 ID·순서와 근거 필드 일치 |
| 검색 점수 | SQL `1 - 거리`와 기존 cosine의 최대 차이 0.00000020255402899405084 (허용 오차 0.000002 이내) |
| 같은 증분 갱신 두 번 | 모두 벡터·근거 추가 0건, 모델 호출 0건, 체크포인트 642229 유지 |
| 원본 보존 | 이전 후 원본 두 파일의 SHA-256 재확인 일치 |

저장소 통합 테스트는 반복 적재·증분 재실행·배치 실패 후 재개·부분 문서 재개·기존 벡터 재사용·새 벡터 필요 시 무과금 중단·과거 원문 변경·동시 갱신 차단·읽기 전용·양수/음수/영벡터의 실제 cosine 점수를 포함한다. 검색 테스트는 작품·출처·후보 URL 제외·인용 우선순위·동점·fallback·잘못된 벡터를 포함한다.

원문 642,226건, 초안 172건, 작업 1,926건, 검수 이력 188건은 같은 원본 파일·ID로 보존했다. 문서/벡터 고아, 출처/URL 불일치, 없는 출처, 벡터 규격 불일치 검사 모두 0건이었다. 수집 대기 5건과 생성 실행 중 1건도 기존 상태 그대로이며 재개하지 않았다.

실제 검색은 저장된 벡터를 질의 벡터로 사용했다. 출처는 `anilist`, `ann_encyclopedia`, `enwiki`, `kitsu_anime_catalog`, `kmrb`, `kowiki`, `madb`, `wikidata`, `wikidata_character_catalog`이고 작품 ID는 1, 2, 3, 5, 8, 9, 11이다. 작품 범위 검색·출처 제한·후보 원문 URL 제외·없는 작품·없는 출처를 비교했다. 기본 검색과 출처 제한은 비어 있지 않은 실제 vector 결과여야 통과하도록 확인했다. 이 표본의 검색 동등성을 확인한 것이며 모든 가능한 질의의 동등성이나 답변 품질을 보장하는 테스트는 아니다.

실제 인계본은 체크포인트까지 이미 색인되어 있어 두 번의 증분 실행은 정상적인 무변경 갱신이다. 새로운 문서의 기존 벡터 재사용·배치 중간 실패·부분 문서 재개·동시 갱신 차단은 별도 합성 데이터 PostgreSQL 회귀에서 검증했다. 신규 벡터를 만드는 유료 갱신 경로는 활성화하지 않았다.

WAL 수정 회귀는 원문/벡터 입력 각각의 WAL·journal 거부, 같은 본체 SHA의 2건 입력을 1건으로 오인하지 않음, 기존 완료 데이터·메타데이터 보존, 새 고정 사본의 2건 무누락·반복 중복 방지, 진행 중 WAL·본체 변경, 변경 후 콜백 예외, 원본 복구 후 무효 부분 적재 재개 차단을 포함한다. 일반 실시간 증분은 WAL의 새 문서를 한 번만 반영하고 모델 호출 없이 기존 벡터를 재사용한다. 최초 시험에서 기존 테스트 준비 연결을 닫지 않아 새 입력 규칙에 걸린 6건은 테스트 연결을 정상 종료하도록 수정했다. 본문·연결 비교와 실제 cosine 검사 자체는 유지했다.

환경은 PostgreSQL 17.11, pgvector 0.8.6이다. 검증 DB 실측 크기는 8,746,055,347바이트(약 8.15 GiB)였다. 원래 벡터 바이트와 PostgreSQL 벡터 값을 함께 보존한 크기이며, 운영 서버의 여유 용량을 점검한 결과는 아니다.

Windows 바인드 파일의 I/O 병목 때문에 임시 Docker 볼륨으로 두 DB를 복사한 뒤 SHA-256 일치를 다시 확인하고 읽기 전용으로 연결했다. 느린 격리 이전 실행 두 번은 I/O 병목을 해소하기 위해 담당자가 종료했고, 최종 실행은 같은 스냅샷으로 완료했다. 최종 내부 사본 이전은 종료 코드 0, 전체 실제 데이터 대조는 종료 코드 0(235.57초)이었다. 데이터 손상이나 운영 실패로 기록하지 않는다.

검증 원본 해시:

- 원문: `336d3b0683e01e3d9ecd53381673f85aa578036e57d10beaf4c035712ced7133`
- 벡터: `6e6932c6172c5a785e0dcf3cebbed22d4ed091632b4d2e8113e3d3f6a6c8c3dd`

로컬 증거는 `C:/Users/USER/me_nu/out/rag-pgvector-verification-20261124/`에 보관한다. 주요 파일은 `restore.log`, `snapshot-before.json`, `snapshot-after-hashes.json`, `import-native.log`, `full-transfer-results.json`, `full-transfer.log`, `rag-regression.log`, `store-tests-sequential-transfer.log`, `locked-install-result.json`, `locked-install.log`다. 원본 복원본은 `C:/Users/USER/me_nu/out/rag-handoff-snapshot-20261124/`에 보존한다.

WAL 최종 리뷰·수정의 별도 증거는 `C:/Users/USER/me_nu/out/rag-pr111-final-review-20261124/`에 있다. `review-tests.log`는 수정 전 실제 누락 재현이고, `wal-fix-store-tests-final.log`는 수정 후 44건 회귀, `wal-fix-rag-regression-final.log`는 네트워크를 차단한 RAG 149건 회귀다. 합계 193 tests와 62 subtests가 통과했다. RAG 최초 실행은 검증 컨테이너에 하위 폴더만 연결해 테스트 수집 중 경로 오류가 났고, 저장소 구조를 그대로 읽기 전용 연결한 재실행으로 확인했다. 전체 인계본의 전수 이전·115개 검색 대조는 앞선 실행 결과이며 이 보완에서 대용량 이전을 다시 실행하지는 않았다.

검증용 PostgreSQL과 사본 보관 컨테이너는 확인 후 중지하고 전용 볼륨은 후속 검토를 위해 보존한다. 서비스 DB·추천 데이터·자동 수집·유료 생성 설정은 수정하지 않았으며 워커를 재개하거나 배포하지 않았다. 검증된 코드와 이 보고서만 Checking 대상 리뷰 PR로 전달하며, 병합과 운영 반영은 별도다.

## 운영 전 남는 범위

기존 인계본의 담당자 PC 이미지 경로와 원본 provenance 경로는 변경하지 않았다. 운영 이미지 배치, 원문·큐·검수 저장소의 PostgreSQL 전환, Celery 예산·시도 제한 및 서비스 적재 연결은 별도 작업이다. 인계본의 대기·실행 중 작업을 임의로 재개하지 않는다. 특히 실행 중 기록은 기존 산출물·시도 이력과 대조한 뒤 복구 여부를 결정해야 한다.
