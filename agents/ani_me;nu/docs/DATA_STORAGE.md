# me;nu 에이전트 적재 규칙

원칙은 **메타데이터와 이미지 바이너리는 PostgreSQL, 교환/백업은 JSON, 읽기용 원고는 Markdown**으로 관리하는 것입니다. 이미지 바이너리는 `image_blobs` 테이블의 `BYTEA`로 저장하고, 메타데이터는 에이전트 레코드에 저장해 같은 DB에서 일관되게 추적합니다. Markdown은 화면 배치·사실 관계·권리 검수를 위한 원본 데이터가 아니므로 DB를 대체하지 않습니다.

PostgreSQL URL이 아직 없는 개발 환경에서는 `.env`의 `ANI_MENU_DATABASE_PATH=data/ani_menu_local.sqlite3` 로컬 임시 저장소를 사용합니다. 현재 실행환경도 이 모드이며, 생성 텍스트와 PNG BLOB 모두 SQLite에 실제 적재됩니다. 나중에 `ANI_MENU_DATABASE_URL`을 설정하면 같은 저장소 계층이 PostgreSQL로 전환됩니다.

## 작품 식별자와 애니메이션 데이터셋

`anime_id_list.csv`는 AniList에서 받은 작품 목록이며 `anilist_id`가 작품의 **외부 식별자**입니다. 파일은 3,887행이고 현재 ID는 중복이 없지만 연속 번호가 아닙니다. 따라서 마지막 ID에 1을 더해 새 AniList ID로 쓰면 안 됩니다. 새 작품은 AniList에서 제공하는 ID를 받아 `anilist_id`로 보존합니다.

서비스 내부에서 순번형 키가 필요하면 별도 `anime_id`를 DB identity/sequence로 발급하고 `anilist_id`에는 UNIQUE 제약을 둡니다. 가져온 CSV의 `anilist_id`는 외부 ID 컬럼에 그대로 넣습니다. 동시 적재가 가능한 PostgreSQL에서는 `MAX(id)+1` 대신 identity/sequence를 사용해야 충돌을 피할 수 있습니다. 이 방식이면 외부 데이터가 빠지거나 AniList가 높은 ID를 발급해도 내부 순번에 영향을 주지 않습니다.

작품, 출처 원문, 이미지, 생성 콘텐츠는 각각 별도 레코드 ID를 가집니다. 원문/이미지/콘텐츠가 작품 ID를 대신하지 않으며, 작품과는 외래 키 또는 `content_anime` 같은 연결 테이블로 연결합니다. 캐러셀이 여러 작품을 다루면 연결 테이블에 작품 여러 개를 저장합니다. 사실·이미지는 출처 ID와도 연결해 출처 추적을 유지합니다.

CSV는 초기 작품 카탈로그 가져오기용 입력 데이터로만 취급합니다. 원본 파일은 수정하지 않고, UTF-8로 읽어 ID 유효성·중복·제목 누락을 검사한 뒤 가져온 시각과 데이터 출처를 기록합니다. 새 작품을 수동으로 `MAX+1` 등록하는 대신 외부 ID를 확인해 catalog에 추가합니다. 현재 에이전트 스키마는 `anime_id` 카탈로그와 원문/이미지/콘텐츠 간 FK 연결을 아직 구현하지 않았으므로, 이 저장소의 현 기능이 해당 ID 연결까지 완료됐다고 간주하면 안 됩니다.

## 에이전트별 저장

| 담당 | 저장 레코드 | 중요 필드 |
|---|---|---|
| 정보 에이전트 | `source`, `fact`, `discovery_candidate` | 출처 URL·발행자·공식 여부·확인 시각, fact와 source 연결, 행사 시작/종료일, 지역, 검증 상태 |
| 유머 에이전트 | `trend_source`, `humor_idea` | 탐색 출처, 원문 복제 대신 요약된 아이디어, 관련 작품/사실 연결, 편집 검토 상태 |
| 이미지 에이전트 | `image_asset` + `image_blobs` | SHA-256 저장 키·크기·MIME 타입·원본 포맷, BYTEA 바이너리, 출처 URL, 권리 상태/근거, 연결 fact, OCR 언어·문구·한국어 설명 |

각 자료는 `request_id`로 한 건의 수집 묶음에 연결합니다. `crawl-ingest`는 출처·팩트·이미지만
적재합니다. 생성 에이전트는 렌더링 직후 결과를 `generated_pending_review` 상태로
`content_drafts`와 `content_cards`에 저장하고,
통과한 콘텐츠는 `feed_queue`에도 적재합니다. `generate-db`는 같은 `request_id`의 `source`, 검증된
`fact`/`humor_idea`, `image_asset`만 읽습니다. `discovery_candidate`와 `official_page_capture`는
검증되지 않은 원문/발견 후보이므로 생성 입력에서 제외됩니다. 정보성 패키지는 공식 1차 출처,
`verified=true` 팩트, 팩트별 출처 연결을 모두 만족해야 생성 조회가 가능합니다.

품질 검사에서 이미지나 근거 문제가 발견되면 관련 수집 레코드 상태를 `needs_crawler_update`로
바꿉니다. 카피 에이전트가 사실을 임의로 보정하지 않도록 하기 위한 상태입니다.

## 카드 원고 형식

카드 원고의 기준 데이터는 Markdown 문자열이 아니라 `ContentPackage`와 카드별 구조 필드입니다. `content_cards`에는 `page`, `role`, `eyebrow`, `title_lines`, `body_sentences`, `fact_ids`, `image_id`, `image_explanation`, `layout`, `rendered_image_key`를 저장하고, `feed_queue.package_json`에는 전체 패키지와 출처·사실·이미지 메타데이터를 함께 보존합니다. 한 행이 카드 한 장이며 `page`로 캐러셀 순서를 보장합니다.

각 캐러셀에는 추천용 대표 `content_type`을 하나 저장합니다. 현재 활성 타입은 소식(`news`), 행사(`event`), 작품 가이드(`guide`), 인물(`people`), 굿즈(`goods`), 비하인드(`behind`), 리뷰 분석(`review_analysis`), 유머(`humor`), 캐릭터(`character`), 큐레이션(`curation`), 음악(`music`) 11개입니다. `content_kind`의 `information`/`humor`는 수집·생성 경로이고, 추천용 `content_type`과 별도 필드입니다. 하위 카테고리는 아직 저장하지 않습니다. `content_types` 레지스트리와 `content_drafts`/`feed_queue`의 인덱스 컬럼을 사용해 타입별 추천 조회를 지원하며, 미분류 콘텐츠는 피드에 넣지 않습니다.

- 제목은 `title_lines` 배열로 보관해 한국어 단어가 렌더 단계에서 어색하게 쪼개지지 않도록 합니다.
- 본문은 `body_sentences` 배열로 문장 단위 보관합니다. 마크다운 줄바꿈이나 HTML 태그에 문장 의미를 맡기지 않습니다.
- 카드에서 언급한 검증 사실은 `fact_ids`로 연결합니다. 날짜·장소·가격·참여 단계 같은 변경 가능한 값은 `Fact.details`에 타입이 있는 필드로 보관합니다.
- 외국어 포스터의 OCR 원문은 이미지 레코드에 보존하고, 사용자에게 설명할 한국어는 카드의 `image_explanation`에 둡니다.
- 사람이 읽고 공유할 필요가 있으면 `ani-menu export-markdown <manifest.json> --output <file.md>`로 내보냅니다. 이 Markdown은 내보내기 산출물이며, 수정 후 다시 입력하려면 구조화 manifest에 반영해야 합니다.

생성 에이전트는 각 페이지를 1308×1632px PNG(327:408 비율, 4배)로 개별 렌더링해 `image_blobs`에 저장하고 SHA-256 키를 `content_cards.rendered_image_key`에 연결합니다. 텍스트와 근거는 같은 카드 행에 구조화 저장되므로 이미지를 합쳐 다시 자르지 않고 페이지별 좌우 스와이프에 바로 사용할 수 있습니다.

## 이미지 형식

이미지는 PNG로 일괄 변환하지 않습니다. 수집한 원본 포맷을 그대로 `image_blobs.binary_data`에 보관하고, `image_blobs.sha256`을 기본 키로 사용해 같은 바이너리의 중복 저장을 방지합니다. 메타데이터에는 storage key와 크기, MIME 타입을 둡니다.

- PNG: 투명 배경, 마스코트/캐릭터 컷아웃, 선명한 그래픽
- JPEG: 사진이나 일반 포스터처럼 사진 계열이며 투명도가 필요 없는 이미지
- WebP: 이미 WebP로 제공되고 파이프라인이 해당 포맷을 지원할 때 원본 유지
- GIF/애니메이션: 움직임이 콘텐츠 의미에 필수일 때만 별도 처리 대상으로 삼고, 카드 정지 이미지로 임의 변환하지 않음

PostgreSQL은 큰 `BYTEA` 값을 TOAST로 저장하므로 애플리케이션이 파일시스템 경로를 관리할 필요가 없습니다. 대신 DB 백업·복구에는 이미지도 포함되므로 DB 크기와 백업 시간을 함께 모니터링합니다. 동일 SHA-256 바이너리는 한 번만 저장합니다.

이미지 노드는 manifest URL 또는 승인된 공식 페이지에서 찾은 URL을 내려받아 파일 크기·형식·픽셀 수를 검증한 뒤 PostgreSQL에 바이너리로 적재합니다. 다운로드에는 HTTPS, 공식 1차 출처 도메인 또는 명시된 CDN 허용 목록, 공개 IP 검사가 적용됩니다. 다운로드 성공은 이용 허가를 뜻하지 않으며 권리 상태는 별도로 심사합니다. OCR 추출/번역은 아직 자동화되지 않았으므로 해당 필드는 실제로 채워질 때까지 미확인으로 취급합니다.

## 보존·상태

- `valid_until`은 피드 노출 종료 시점, `purge_at`은 DB 완전 삭제 시점입니다. 둘을 분리해 만료 콘텐츠는 피드에서 먼저 숨기고 보관 유예기간 후 정리합니다.
- 행사·팝업·응모는 행사 종료일/마감일 다음 날부터 피드에서 제외하고, 종료 후 30일이 지나면 관련 초안·카드·수집 레코드·이미지를 정리합니다.
- 방영일이 있으면 방영일 후 30일까지, 그 외 날짜가 특정되지 않은 새 정보는 기본 90일 동안 피드에 노출합니다. 정보는 유효기간 종료 후 30일 뒤 삭제합니다.
- `ani-menu db-cleanup`은 만료된 피드 항목을 숨김 상태로 바꾸고, `purge_at`이 지난 생성 콘텐츠·카드·수집 자료와 어디에서도 참조되지 않는 이미지 바이너리를 삭제합니다.
- 세 번 검수에서 탈락한 콘텐츠는 `rejected_archive`에도 저장하며, 해당 콘텐츠의 보존 만료 시 함께 삭제합니다.
- 통과 결과는 내부 `feed_queue`에 적재합니다. 이는 Instagram 자동 게시와는 별개이며, 게시 연동이 구현되기 전까지 실제 SNS 게시를 뜻하지 않습니다.
