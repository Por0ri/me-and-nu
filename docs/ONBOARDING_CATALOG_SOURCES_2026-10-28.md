# 온보딩 시작 카탈로그: 선정 기준과 출처

기준일: **2026-10-28 (Asia/Seoul)**. 사용자가 승인한 대표작 30~50개 범위에서 **실제 작품 30개**를 준비했다. 입력 파일은 [onboarding_catalog_starter.json](../backend/app/seeds/onboarding_catalog_starter.json)이며 `topicCode:"anime"`, `demo:false`다. 이 문서와 JSON 생성은 DB 적재·배포·main 병합을 뜻하지 않는다.

## 1. 작품 선정과 식별 기준

- 기존 에이전트 카탈로그의 잘 알려진 작품을 출발점으로 사용하되, 스포츠·음악·로맨스·SF와 독립 애니메이션 영화를 포함하도록 편집했다. **전체 작품 목록이나 한국 이용자 인기 순위가 아니다.** 액션·판타지와 일본 작품에 편중되어 있으며 한국 제작 애니메이션·가족 TV물·여성향 작품 등의 확장이 필요하다.
- `provider:"menu_work"`는 외부 API 공급자라는 뜻이 아니라 **이 저장소 에이전트의 canonical `menu_work_id` 네임스페이스**다. 숫자는 문자열 `externalId`로 보존하고 서비스 `entity_id`는 DB가 별도로 발급한다. 예를 들어 `menu_work:2`와 DB `entity_id=2`는 같다는 보장이 없다.
- 내부 근거 파일은 [menu_works.csv](../agents/ani_me%3Bnu/data/reference/menu_works.csv), [menu_work_entries.csv](../agents/ani_me%3Bnu/data/reference/menu_work_entries.csv), [parent overrides](../agents/ani_me%3Bnu/data/reference/menu_work_parent_overrides.csv)다. 각각 2,322개 canonical 작품, 3,887개 시즌·영화 엔트리, 1개 부모 보정이 있다. 기존 파일은 수정하지 않았다.
- 온보딩 검색 단위는 **기존 canonical 작품 묶음 1개**다. 27개는 `SERIES`, 독립 영화 3개는 `MOVIE`다. TV 시즌·OVA·같은 시리즈의 극장판을 Q1에서 중복 선택하도록 새 작품으로 만들지 않는다. 동시에 실사화·독립 스핀오프·다른 주인공의 후속작을 이름이 비슷하다는 이유로 새로 합치지 않는다.
- 기존 내부 묶음은 영구적으로 정확하다고 판정한 것이 아니다. 특히 강철의 연금술사와 에반게리온 등의 다른 영상화 판본은 현재 canonical 범위를 그대로 참조한다. 판본별 취향이 필요하면 기존 ID의 의미를 바꾸기 전에 entry 단위 모델과 이전 방법을 별도로 설계한다.
- AniList/MAL 시즌 ID는 이 JSON에 복사하지 않았다. 추천 연결에는 `entity_external_id(provider=menu_work)` → canonical ID → 기존 entry 매핑을 사용하며 외부 번호를 새 내부 PK로 사용하지 않는다. 전체 CSV·인기도 숫자·원문 설명·포스터를 서비스 카탈로그에 복제하지 않았다.

## 2. 한국어명, 연도와 이미지

각 작품의 일본 공식 사이트·방송사·제작사 페이지로 애니메이션 작품임을 확인했다. 아래의 한국어 근거는 배급·방송·공식 상점에서 **표기가 실제로 사용됨을 확인한 수준**이다. 한국어 페이지를 찾지 못한 항목은 기존 내부 한국어명과 원제를 대조한 편집 표기로 표시했으며, 국내 정식 배급명 확인 완료라고 주장하지 않는다. Netflix 장르 목록을 근거로 둔 항목은 개별 작품 페이지까지 확인했다는 뜻도 아니다.

검색용 별칭은 원제, 공백·기호 차이와 널리 쓰이는 짧은 표기를 소수만 편집했다. 원시 공급자의 전체 별칭 목록은 사용하지 않는다. `Re:제로...`·`카구야 님...` 등은 시즌/부제 제거 후 시리즈 표시명으로 쓰며 원제와 대표 전체 표기를 별칭으로 유지한다.

`releaseYear`는 **이 JSON이 가리키는 애니메이션 묶음의 최초 공개 연도**이며 국내 배급 연도나 최신 시즌 연도가 아니다. 공식 자료로 범위를 확인한 10개만 값을 채우고 나머지 20개는 null로 둔다. 기존 CSV의 `first_year`를 일괄 복사하지 않았다. 특히 원피스의 1998 같은 선행 영상 연도를 TV 첫 방영 연도로 오해할 수 있어 이번 시작 목록에서 제외했다. 작품별 연도 검증이 끝나면 같은 외부 ID를 유지한 채 갱신한다.

**30개 모두 `imageUrl:null`**이다. 포스터 다운로드·복제·핫링크를 하지 않았다. 프론트는 제목·형식과 기본 썸네일로 표시한다. 이미지 사용 근거와 안정적인 전달 경로를 확보한 뒤 추가한다. 연도 null과 이미지 null은 검색·선택을 막지 않는다.

## 3. 장르·소재 편집 기준

장르 10개, 소재 28개를 정의했다. 각 칩은 이 서비스의 편집 분류이며 공급자가 공인한 장르값이나 객관적인 점수가 아니다. 작품의 공식 소개·도입 설정에서 확인할 수 있는 중심 요소를 사용하고, 결말·사망·정체 반전·후반 배신 등을 드러내는 태그는 넣지 않았다. 등장인물 성별·연령·제작 방식·원작 형식도 이번 선호 칩으로 넣지 않는다.

- **장르**: 이야기를 소비하는 큰 형식·분위기(액션, 로맨스, 미스터리 등).
- **소재**: 이야기에 등장하는 배경·활동·관계(학교생활, 배구, 편지, 첩보 등).
- 2026-10-29 Checking의 Q2 후보는 Topic 전체 활성 칩이며, **장르와 소재 각각 최소 1개·최대 7개**다. `sourceEntityIds`는 선택 작품과의 연결 힌트이고 빈 배열도 허용한다. 이 문서의 30개 시작 카탈로그와 현재 전체 칩 목록의 범위를 구분한다. 이번 최소 조건 변경의 서비스 배포는 별도다.
- `code`는 안정적인 내부 키다. 표시명 변경은 가능하지만 같은 code를 다른 의미로 재사용하지 않는다. '타임루프'는 같은 시간의 반복, '시간 여행'은 시점 이동으로 구분한다.
- 공식 소개에 같은 요소가 있어도 다른 작품에 태그가 빠질 수 있다. 이 시작 목록은 대표 요소의 최소 편집본이며, 반전 없이 공통 기준을 적용하는 사람 검토를 거쳐 확장한다.
- 이 태그는 연령 등급이나 시청 가능 국가를 나타내지 않는다. 생년월일·연령 확인 정책은 별도 보류 상태이며 이 목록으로 새 정책을 도입하지 않는다.

| 종류 | code | 표시명 |
| --- | --- | --- |
| genre | `action` | 액션 |
| genre | `adventure` | 모험 |
| genre | `fantasy` | 판타지 |
| genre | `comedy` | 코미디 |
| genre | `drama` | 드라마 |
| genre | `romance` | 로맨스 |
| genre | `mystery` | 미스터리 |
| genre | `thriller` | 스릴러 |
| genre | `science_fiction` | SF·공상과학 |
| genre | `sports` | 스포츠 |
| theme | `school` | 학교생활 |
| theme | `friendship` | 우정 |
| theme | `family` | 가족 |
| theme | `growth` | 성장 |
| theme | `supernatural` | 초자연 |
| theme | `superpowers` | 초능력 |
| theme | `heroes` | 히어로 |
| theme | `journey` | 여행 |
| theme | `survival` | 생존 |
| theme | `military` | 군사 |
| theme | `mind_games` | 두뇌전 |
| theme | `ninja` | 닌자 |
| theme | `body_swap` | 몸 바뀜 |
| theme | `other_world` | 이세계 |
| theme | `time_loop` | 타임루프 |
| theme | `music` | 음악 |
| theme | `volleyball` | 배구 |
| theme | `espionage` | 첩보 |
| theme | `science` | 과학 |
| theme | `civilization` | 문명 재건 |
| theme | `historical` | 역사적 배경 |
| theme | `letters` | 편지 |
| theme | `work` | 직업생활 |
| theme | `mecha` | 로봇 |
| theme | `space` | 우주 |
| theme | `bounty_hunters` | 현상금 사냥 |
| theme | `dungeons` | 던전 |
| theme | `time_travel` | 시간 여행 |

## 4. 작품별 확인 기록

모든 아래 URL의 확인일은 **2026-10-28**이다. 일본 원문과 한국어 배급 설명은 그대로 저장하지 않고 분류 근거만 짧게 기록했다. 공식 사이트 확인은 콘텐츠·이미지 재사용 허가 확인과 구분한다. 페이지에 표시된 현재 배급 가능 여부·가격·연령 등급·최신 방송 일정은 이 카탈로그에 가져오지 않았다.

표의 ID는 `menu_work_id`이며 JSON의 `externalId`와 일치한다. 한국어 근거가 없는 5개(4, 10, 11, 32, 39)는 **편집 표기 / 국내 정식명 추가 확인 대상**이다.

| ID | 표시명 / 단위 / 연도 | 작품 확인·분류 참고 | 한국어 표기 근거 | 자체 분류 이유 |
| --- | --- | --- | --- | --- |
| 1 | 진격의 거인 / SERIES / 미확인 | [공식 작품·방송 페이지](https://shingeki.tv/final/) | [한국어 표기](https://www.netflix.com/kr/title/70299043) | 거인에 맞서는 전투와 인류 생존이라는 도입 설정에 따라 분류. |
| 2 | 귀멸의 칼날 / SERIES / 2019 | [공식 작품·방송 페이지](https://kimetsu.com/anime/about/) | [한국어 표기](https://www.netflix.com/kr/browse/genre/2952508) | 귀살대의 전투, 남매 관계와 인간·귀신 설정을 반영. |
| 3 | 주술회전 / SERIES / 2020 | [공식 작품·방송 페이지](https://jujutsukaisen.jp/story/) | [한국어 표기](https://www.netflix.com/kr/title/81278456) | 저주를 상대하는 주술사들과 학교라는 기본 배경을 반영. |
| 4 | 데스노트 / SERIES / 2006 | [공식 작품·방송 페이지](https://www.ntv.co.jp/deathnote/static/story.html) | 기존 내부명·원제 대조; 정식명 추가 확인 | 1화 소개의 탐정과 노트 소유자의 두뇌전, 초자연적 노트를 기준으로 분류. |
| 5 | 나의 히어로 아카데미아 / SERIES / 미확인 | [공식 작품·방송 페이지](https://heroaca.com/introduction/) | [한국어 표기](https://www.netflix.com/kr/browse/genre/2952508) | 히어로 양성 학교와 초능력, 동급생들의 성장을 반영. |
| 6 | 헌터×헌터 / SERIES / 미확인 | [공식 작품·방송 페이지](https://www.ntv.co.jp/hunterhunter/intro/index.html) | [한국어 표기](https://www.netflix.com/kr/title/70300472) | 헌터 시험과 동료들의 모험이라는 도입 설정을 반영. |
| 7 | 원펀맨 / SERIES / 미확인 | [공식 작품·방송 페이지](https://onepunchman-anime.net/) | [한국어 표기](https://www.netflix.com/kr/browse/genre/2952508) | 영웅 활동과 압도적인 힘을 코미디로 다루는 시리즈의 기본 설정을 자체 분류. |
| 10 | 강철의 연금술사 / SERIES / 미확인 | [공식 작품·방송 페이지](https://www.hagaren.jp/introduction/) | 기존 내부명·원제 대조; 정식명 추가 확인 | 형제가 잃어버린 것을 되찾으려 떠나는 여정을 기준으로 분류. |
| 11 | 나루토 / SERIES / 미확인 | [공식 작품·방송 페이지](https://naruto-official.com/) | 기존 내부명·원제 대조; 정식명 추가 확인 | 닌자 소년과 동료들의 성장이라는 대표 설정을 자체 분류. 후속 별도 주인공 작품은 자동 병합하지 않음. |
| 13 | 목소리의 형태 / MOVIE / 2016 | [공식 작품·방송 페이지](https://koenokatachi-movie.com/) | [한국어 표기](https://www.netflix.com/kr/title/80223226) | 학창 시절의 관계를 돌아보고 다시 소통하는 이야기라는 소개를 반영. |
| 14 | 너의 이름은. / MOVIE / 2016 | [공식 작품·방송 페이지](https://www.kiminona.com/) | [한국어 표기](https://www.netflix.com/kr/title/80161371) | 두 학생의 몸이 바뀌는 도입 설정과 관계를 기준으로 분류. 후반 반전 태그 제외. |
| 15 | 체인소 맨 / SERIES / 2022 | [공식 작품·방송 페이지](https://chainsawman.dog/tvseries/) | [한국어 표기](https://www.netflix.com/kr/browse/genre/6721) | 악마와 맞서는 액션이라는 기본 설정을 자체 분류. 잔혹 장면이나 정체 반전은 칩에 사용하지 않음. |
| 18 | 모브사이코 100 / SERIES / 미확인 | [공식 작품·방송 페이지](https://mobpsycho100.com/) | [한국어 표기](https://www.netflix.com/kr/browse/genre/6721) | 초능력 소년의 학교생활과 인간관계, 성장을 반영. |
| 19 | Re:제로부터 시작하는 이세계 생활 / SERIES / 미확인 | [공식 작품·방송 페이지](https://re-zero-anime.jp/tv/story/) | [한국어 표기](https://www.netflix.com/kr/browse/genre/6721) | 이세계에서 반복되는 시간을 겪는 도입 설정을 반영. |
| 20 | 4월은 너의 거짓말 / SERIES / 미확인 | [공식 작품·방송 페이지](https://www.kimiuso.jp/) | [한국어 표기](https://www.aniplustv.com/share.asp?c=1545) | 음악을 계기로 연결되는 학생들의 관계라는 기본 설정을 자체 분류. |
| 24 | 하이큐!! / SERIES / 2014 | [공식 작품·방송 페이지](https://haikyu.jp/) | [한국어 표기](https://www.netflix.com/kr/browse/genre/2952508) | 공식 소개의 고교 배구와 팀의 성장·동료 관계를 반영. |
| 26 | 스파이 패밀리 / SERIES / 미확인 | [공식 작품·방송 페이지](https://spy-family.net/) | [한국어 표기](https://www.netflix.com/kr/title/81511410) | 서로 정체를 숨긴 가족과 첩보 임무라는 도입 설정을 반영. |
| 27 | 카구야 님은 고백받고 싶어 / SERIES / 미확인 | [공식 작품·방송 페이지](https://kaguya.love/) | [한국어 표기](https://shop.aniplustv.com/item/10052) | 학생회의 연애 두뇌전이라는 기본 설정을 반영. 후속 시즌의 관계 결과는 태그에서 제외. |
| 28 | 닥터 스톤 / SERIES / 미확인 | [공식 작품·방송 페이지](https://dr-stone.jp/introduction/) | [한국어 표기](https://www.netflix.com/kr/browse/genre/2952508) | 과학과 동료들의 협력으로 문명을 다시 세운다는 도입 설정을 반영. |
| 29 | 빈란드 사가 / SERIES / 미확인 | [공식 작품·방송 페이지](https://vinlandsaga.jp/) | [한국어 표기](https://www.netflix.com/kr/title/81249833) | 중세 전사들의 여정을 다룬다는 소개를 반영. 후속 전개의 결과는 제외. |
| 30 | 바이올렛 에버가든 / SERIES / 2018 | [공식 작품·방송 페이지](https://tv.violet-evergarden.jp/introduction/) | [한국어 표기](https://www.netflix.com/kr/title/80182123) | 전쟁 이후 편지 대필 일을 통해 감정을 알아가는 과정을 반영. |
| 32 | 토라도라! / SERIES / 미확인 | [공식 작품·방송 페이지](https://king-cr.jp/special/toradora/introduction.html) | 기존 내부명·원제 대조; 정식명 추가 확인 | 공식 소개의 학원 러브코미디와 학생들의 관계를 반영. |
| 38 | 이 멋진 세계에 축복을! / SERIES / 미확인 | [공식 작품·방송 페이지](https://konosuba.com/1st/) | [한국어 표기](https://shop.aniplustv.com/item/17047) | 이세계에서 모인 동료들의 모험을 코미디로 다루는 기본 설정을 반영. |
| 39 | 코드 기아스 반역의 를르슈 / SERIES / 미확인 | [공식 작품·방송 페이지](https://geass.jp/) | 기존 내부명·원제 대조; 정식명 추가 확인 | 로봇 병기와 전략적 대결이라는 대표 설정을 자체 분류. 결말·정체 태그 제외. |
| 42 | 신세기 에반게리온 / SERIES / 미확인 | [공식 작품·방송 페이지](https://www.evangelion.jp/) | [한국어 표기](https://qr.netflix.com/kr/title/81033445) | 전투 병기와 인류 생존이라는 기본 소개를 반영. 후반 세계관의 비밀은 제외. |
| 43 | 장송의 프리렌 / SERIES / 2023 | [공식 작품·방송 페이지](https://frieren-anime.jp/intro/) | [한국어 표기](https://www.netflix.com/kr/browse/genre/2952508) | 용사 일행의 모험 이후 이어지는 마법사의 여정과 인연이라는 공식 소개를 반영. |
| 45 | 센과 치히로의 행방불명 / MOVIE / 2001 | [공식 작품·방송 페이지](https://www.ghibli.jp/works/chihiro/) | [한국어 표기](https://www.netflix.com/kr/title/60023642) | 낯선 세계에 들어간 소녀의 모험과 가족이라는 도입 설정을 반영. |
| 47 | 카우보이 비밥 / SERIES / 미확인 | [공식 작품·방송 페이지](https://www.cowboy-bebop.net/world/) | [한국어 표기](https://www.netflix.com/kr/TITLE/80001305) | 우주에서 현상범을 좇는 동료들의 기본 설정을 반영. |
| 54 | 나 혼자만 레벨업 / SERIES / 미확인 | [공식 작품·방송 페이지](https://sololeveling-anime.net/) | [한국어 표기](https://www.netflix.com/kr/title/81749761) | 헌터·던전과 주인공의 성장을 다룬다는 도입 소개를 반영. |
| 56 | 도쿄 리벤저스 / SERIES / 미확인 | [공식 작품·방송 페이지](https://tokyo-revengers-anime.com/) | [한국어 표기](https://www.netflix.com/kr/browse/genre/6721) | 공식 소개의 타임리프 서스펜스와 청소년 동료 관계를 반영. |

연도에 값을 넣은 근거:

| ID | 연도 | 근거 |
| --- | --- | --- |
| 2 | 2019 | [공식 애니메이션 소개](https://kimetsu.com/anime/about/)의 TV 시작 연도 |
| 3 | 2020 | [공식 소개](https://jujutsukaisen.jp/story/)의 TV 시작 연도 |
| 4 | 2006 | [방송사 1화 기록](https://www.ntv.co.jp/deathnote/static/story.html)의 2006-10-03 방송 |
| 13 | 2016 | [한국어 배급 작품 페이지](https://www.netflix.com/kr/title/80223226)의 독립 영화 연도 |
| 14 | 2016 | [영화 공식 사이트](https://www.kiminona.com/)와 [한국어 배급 페이지](https://www.netflix.com/kr/title/80161371) |
| 15 | 2022 | [공식 TV 페이지](https://chainsawman.dog/tvseries/)의 2022-10-11 방송 |
| 24 | 2014 | [공식 포털 소개](https://haikyu.jp/)의 TV 시리즈 시작 연도 |
| 30 | 2018 | [TV 공식 페이지](https://tv.violet-evergarden.jp/)의 최초 방송기 기록과 [배급 페이지](https://www.netflix.com/kr/title/80182123) |
| 43 | 2023 | [공식 홈페이지](https://frieren-anime.jp/)의 2023-09-29 최초 방송 표기 |
| 45 | 2001 | [제작사 작품 페이지](https://www.ghibli.jp/works/chihiro/)와 [배급 페이지](https://www.netflix.com/kr/title/60023642) |

## 5. 기존 자료 검토 결과와 운영 범위

기존 canonical CSV의 `title_ko_source`에는 Wikidata·AniList·manual 등이 섞여 있고 `title_ko_check`가 비어 있는 행도 많다. 빈 값은 사람 검수 완료 증거가 아니다. 예를 들어 ID 46은 한국어명이 '스틸 볼 런'인 반면 원제와 묶음 범위는 다른 죠죠 작품까지 포함되어 있어 이번 카탈로그에 넣지 않았다. 원본 CSV를 수정하거나 새 데이터로 전체 대체하지 않았다.

[AniList 공식 API 이용 조건](https://docs.anilist.co/guide/terms-of-use)은 데이터 대량 수집·저장 서비스로의 이용 및 일부 경쟁 서비스 이용을 제한한다. 이번에는 AniList API를 호출하지 않고 원시 태그·인기도·이미지의 대량 동기화도 만들지 않았다. 향후 해당 공급자를 자동 적재 원천으로 쓰려면 실제 서비스 용도와 이용 조건을 먼저 검토한다. 이는 현재 수작업 시작 카탈로그를 중단시키는 승인 요청이 아니다.

이 JSON은 현행 [카탈로그 적재 도구](../backend/app/seeds/onboarding_catalog.py)로 미리보기 후 저장할 수 있다. 활성 `anime` Topic과 온보딩 V2 스키마가 필요하다. e3(`e3a7b9c2d451`)는 온보딩 도입 revision이며 후속 서비스 전체의 명시적 target은 e3·캐러셀 f4를 포함하는 **`150a601074af`**이다. [캐러셀 migration 안내](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md#2-db-변경과-명시적-migration-대상)를 참고하고 생년월일 삭제 d2를 함께 적용하지 않는다. 실작품 목록이므로 `--demo`를 붙이지 않는다. DB명은 실제 검증 대상과 정확히 일치시킨다.

```powershell
# backend 디렉터리. 아래 DB명은 대상 환경에 맞게 지정한다.
python -m app.seeds.onboarding_catalog --file app/seeds/onboarding_catalog_starter.json --expected-database <검증할_DB명>
# 미리보기 확인 후에만 같은 명령에 --apply 추가
```

적재는 기존 사용자 선택·동의·생년월일을 바꾸지 않는다. 이 파일에 든 작품의 태그 매핑은 파일 내용으로 대체되므로 사람 편집 후 다시 적재할 때 변경 요약을 확인한다. 실서비스 검색으로 확장할 때에는 검색 실패 로그에 개인 입력을 무작정 쌓는 대신 미지원 작품 제안 경로와 카탈로그 수정 이력을 먼저 정한다.

## 6. 검증과 남은 일

JSON 파싱과 현행 importer 모델 검증을 통과했다. 30개 ID가 기존 canonical CSV에 존재하고 표시명 또는 별칭이 기존 한국어명과 일치함을 대조했다. 작품 30개·태그 38개·작품별 연결 140개이며, 중복 외부 ID·미선언/중복 태그와 사용하지 않는 태그가 없다. 27개 SERIES·3개 MOVIE, 값이 있는 연도 10개, 모든 imageUrl null도 확인했다. `git diff --check`를 통과했다. 별도 PostgreSQL 적재와 실제 화면 검증 결과는 [온보딩 구현 명세](ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md) 및 통합 작업 기록에서 관리한다.

국내 정식명 추가 확인 5개, 미확인 연도 20개, 포스터 30개가 남아 있다. 이 결측값은 화면 연결 테스트를 막지 않으며 실작품 30개를 synthetic fixture로 바꾸지 않는다. 추천 서비스와의 연동, 기존 수천 행의 전체 품질 검수, 대규모 공급자 동기화는 이번 시작 카탈로그 완료 범위 밖이다.
