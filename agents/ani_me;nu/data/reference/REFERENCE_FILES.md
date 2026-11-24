# 레퍼런스 폴더 파일 안내

기간형과 RAG형은 저장소 루트의 `catalog/`에서 정식 작품·시즌 목록을 읽고, 같은 폴더의 두 CSV에 임시 작품과 발견 후보를 함께 기록합니다. 기간형의 외부 ID, 별칭, 캐릭터·주제가 연결 정보는 기본적으로 이 레퍼런스 폴더에 보관합니다.

서버에서는 두 프로세스가 같은 `catalog/`를 보도록 마운트합니다. 정식 CSV 두 개는 읽기 전용으로 관리하고, 임시 등록 CSV의 교체와 잠금 파일 생성을 위해 폴더 쓰기를 허용합니다. 정식 목록 전체를 읽기 전용으로 마운트하는 구성에서는 `ME_NU_WORK_REGISTRY_DIR`로 별도의 공유 쓰기 폴더를 지정할 수 있습니다.

## 파일 목록

| 파일 | 용도 | 주요 연결 키 |
|---|---|---|
| 루트 `catalog/menu_works.csv` | 상위 작품의 한국어·로마자·영어·원어 제목과 팀이 부여한 정식 ID입니다. | `menu_work_id` |
| 루트 `catalog/menu_work_entries.csv` | 시즌·극장판 등 개별 항목과 상위 작품을 연결합니다. | `menu_work_id`, `anilist_id`, `mal_id` |
| 루트 `catalog/agent_works.csv` | 정식 목록에 없는 작품의 제목과 두 에이전트가 공유하는 임시 ID입니다. | `menu_work_id` |
| `menu_work_external_entries.csv` | Kitsu 같은 외부 카탈로그 항목의 ID·제목을 이미 등록된 상위 작품에 연결합니다. | `menu_work_id`, `source_id`, `external_id` |
| `menu_work_franchises.csv` | 여러 작품이 공유하는 프랜차이즈 표기입니다. 프랜차이즈가 같아도 작품 ID는 별개로 유지할 수 있습니다. | `franchise_id`, `menu_work_id` |
| `menu_work_parent_overrides.csv` | 작품명 매칭에서 특정 하위작·스핀오프를 상위 작품으로 연결하는 예외 규칙입니다. | `menu_work_id`, `parent_menu_work_id` |
| 루트 `catalog/discovered_works.csv` | 작품을 특정하기 어렵거나 임시 번호가 충돌한 후보의 작품명, 원문 URL, 확인 시각을 보존합니다. | `kitsu_id`, `status` |
| `work_characters.csv` | 작품과 캐릭터·작품 속 고유명사의 연결 및 언어별 이름·별칭입니다. 자세한 출처와 매칭 규칙은 `WORK_MATCH_DATA.md`를 참고합니다. | `menu_work_id`, `anilist_id`, `wikidata_id` |
| `work_theme_songs.csv` | 작품별 OP·ED 곡명과 가수 정보입니다. 곡명·가수명으로 작품을 찾는 보조 사전입니다. | `menu_work_id`, `anilist_id`, `animethemes_anime` |
| `WORK_MATCH_DATA.md` | 캐릭터·곡명 매칭 사전의 출처, 열 설명, 사용 규칙, 한계와 재생성 방법을 설명합니다. | 문서 |

## 작품 ID 연결 방식

1. 원문 제목과 구조화된 작품 정보를 루트 `catalog/`의 정식 목록과 `agent_works.csv`의 임시 목록에서 찾습니다. 같은 작품에 정식 번호가 생기면 새 매칭에는 정식 번호를 우선합니다.
2. 시즌·극장판 제목은 루트 `catalog/menu_work_entries.csv`의 해당 항목을 통해 상위 `menu_work_id`로 연결합니다.
3. `menu_work_external_entries.csv`는 기존 작품에 대한 Kitsu 등의 외부 ID를 보완합니다. `external_id` 자체가 서비스의 작품 ID는 아닙니다.
4. 캐릭터·곡명 사전은 제목만으로 작품을 찾기 어려울 때 보조 신호로 사용합니다. 이름이 여러 작품에 걸리거나 애매하면 자동으로 연결하지 않습니다.
5. 프랜차이즈가 같다는 이유만으로 작품을 합치지 않습니다. 예를 들어 유희왕 원작과 유희왕 GX는 각각 다른 `menu_work_id`를 유지합니다. 상위 작품 연결 예외는 `menu_work_parent_overrides.csv`에 명시된 경우에만 적용합니다.

## 새 작품 처리 원칙

중복 대조 후 원문의 주된 작품이 신규로 확인되면 기간형 에이전트는 다음 방식으로 임시 번호를 계산하고 작품 제목과 함께 `agent_works.csv`에 기록합니다.

- Kitsu ID가 확인되면 `1_000_000 + int(kitsu_id)`를 사용합니다. 예를 들어 Kitsu 50362가 미등록이면 계산값은 1050362입니다. 현재 팀 목록에는 이미 정식 번호 2424가 있어 이를 사용합니다.
- Kitsu ID 없이 작품 제목이 확인되면 `2_000_000 + zlib.crc32(normalized_title.encode("utf-8")) % 1_000_000_000`을 사용합니다. 제목은 유니코드 NFKC와 대소문자 정규화를 적용한 뒤 문자와 숫자만 남깁니다.
- 모호한 후보와 다른 작품의 임시 번호가 겹친 후보는 `discovered_works.csv`에 기록하고 검토를 기다립니다.

정식 번호는 팀이 루트 `catalog/`에서 관리합니다. 임시 번호로도 생성·적재할 수 있습니다. 기존 SQLite 콘텐츠에 기록된 작품 번호와 연결은 보존합니다.

## 수정 시 주의

- `menu_work_id`를 바꾸거나 재사용하지 않습니다. 작품의 최종 연결 ID를 참조하는 다른 CSV들도 함께 영향을 받습니다.
- 상위 작품의 한국어 제목을 모든 시즌·극장판 항목에 일괄 복사하지 않습니다. 하위 항목의 정식 제목이 확인된 경우 그 제목을 유지합니다.
- 프랜차이즈 표기는 작품 통합이나 작품 ID 변경을 뜻하지 않습니다.
- `.menu_work_catalog.lock`은 카탈로그 작업 중 생성되는 실행 잠금 파일이며 작품 데이터가 아닙니다. 직접 편집하지 않습니다.
