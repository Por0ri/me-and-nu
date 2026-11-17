# 레퍼런스 폴더 파일 안내

정식 작품 목록은 저장소 루트 `catalog/`의 `menu_works.csv`와 `menu_work_entries.csv`입니다. 이 폴더에는 외부 ID, 별칭, 임시 작품과 작품별 캐릭터·주제가 매칭에 사용하는 보조 파일을 둡니다.

## 파일 목록

| 파일 | 용도 | 주요 연결 키 |
|---|---|---|
| `menu_work_external_entries.csv` | Kitsu 같은 외부 카탈로그 항목의 ID·제목을 이미 등록된 상위 작품에 연결합니다. | `menu_work_id`, `source_id`, `external_id` |
| `agent_works.csv` | 정식 목록에 없는 작품에 에이전트가 부여한 안정적인 임시 ID와 제목을 기록합니다. 정식 `catalog/`에는 쓰지 않습니다. | `menu_work_id` |
| `menu_work_franchises.csv` | 여러 작품이 공유하는 프랜차이즈 표기입니다. 프랜차이즈가 같아도 작품 ID는 별개로 유지할 수 있습니다. | `franchise_id`, `menu_work_id` |
| `menu_work_parent_overrides.csv` | 작품명 매칭에서 특정 하위작·스핀오프를 상위 작품으로 연결하는 예외 규칙입니다. | `menu_work_id`, `parent_menu_work_id` |
| `discovered_works.csv` | 작품 ID 연결이 모호하거나 임시 ID 충돌 등으로 등록할 수 없는 후보의 검토 대기 목록입니다. | `kitsu_id`, `status` |
| `work_characters.csv` | 작품과 캐릭터·작품 속 고유명사의 연결 및 언어별 이름·별칭입니다. 자세한 출처와 매칭 규칙은 `WORK_MATCH_DATA.md`를 참고합니다. | `menu_work_id`, `anilist_id`, `wikidata_id` |
| `work_theme_songs.csv` | 작품별 OP·ED 곡명과 가수 정보입니다. 곡명·가수명으로 작품을 찾는 보조 사전입니다. | `menu_work_id`, `anilist_id`, `animethemes_anime` |
| `WORK_MATCH_DATA.md` | 캐릭터·곡명 매칭 사전의 출처, 열 설명, 사용 규칙, 한계와 재생성 방법을 설명합니다. | 문서 |

## 작품 ID 연결 방식

1. 원문 제목과 구조화된 작품 정보를 루트 `catalog/menu_works.csv` 및 `catalog/menu_work_entries.csv`에서 찾습니다.
2. 임시 작품도 매칭에는 함께 사용할 수 있으며, 시즌·극장판 제목은 정식 목록의 `menu_work_entries.csv`에서 상위 `menu_work_id`로 연결합니다.
3. `menu_work_external_entries.csv`는 기존 작품에 대한 Kitsu 등의 외부 ID를 보완합니다. `external_id` 자체가 서비스의 작품 ID는 아닙니다.
4. 캐릭터·곡명 사전은 제목만으로 작품을 찾기 어려울 때 보조 신호로 사용합니다. 이름이 여러 작품에 걸리거나 애매하면 자동으로 연결하지 않습니다.
5. 프랜차이즈가 같다는 이유만으로 작품을 합치지 않습니다. 예를 들어 유희왕 원작과 유희왕 GX는 각각 다른 `menu_work_id`를 유지합니다. 상위 작품 연결 예외는 `menu_work_parent_overrides.csv`에 명시된 경우에만 적용합니다.

## 새 작품 처리 원칙

에이전트는 루트 `catalog/`를 읽기만 합니다. 신규 작품은 Kitsu ID가 있으면 `1,000,000 + int(kitsu_id)`, 없으면 정규화한 제목의 CRC32를 이용한 `2,000,000 + crc32(title) % 1,000,000,000` 임시 ID를 부여해 `agent_works.csv`에만 기록합니다. Kitsu 외부 ID는 `menu_work_external_entries.csv`에서 이 임시 ID와 연결합니다. 정식 ID는 팀이 `catalog/`에 등록합니다. 임시 ID 충돌이나 모호한 작품은 `discovered_works.csv`에 기록합니다.

## 수정 시 주의

- `menu_work_id`를 바꾸거나 재사용하지 않습니다. 작품의 최종 연결 ID를 참조하는 다른 CSV들도 함께 영향을 받습니다.
- 상위 작품의 한국어 제목을 모든 시즌·극장판 항목에 일괄 복사하지 않습니다. 하위 항목의 정식 제목이 확인된 경우 그 제목을 유지합니다.
- 프랜차이즈 표기는 작품 통합이나 작품 ID 변경을 뜻하지 않습니다.
- `.menu_work_catalog.lock`은 카탈로그 작업 중 생성되는 실행 잠금 파일이며 작품 데이터가 아닙니다. 직접 편집하지 않습니다.
