# 콘텐츠 유형별 자동수집 출처 후보 확장

조사 기준일: 2026-10-21
목적: 당시 11개 분류로 조사한 후보를 현재 활성 10개 유형에 맞춰 통합해 보존한다. 비하인드 후보는 유머 유형에 포함된다.
상태: **조사 문서 전용**. 이 목록은 `config/sources.json`이나 실행 코드에 반영하지 않았다.

## 먼저 답: 전부 연결할 수 있나?

기술적으로는 대부분 연결할 수 있다. 단, “한 번에 모든 사이트를 연결”하는 형태는 아니다. 사이트마다 RSS, 공개 API, 공식 목록 페이지(HTML), 공식 채널 메타데이터 등 수집 방식이 달라서 **출처별 어댑터**가 필요하다. 더 중요한 점은 기술적으로 읽을 수 있는 것과 자동수집·LLM 입력·DB 저장을 허용하는 것이 서로 다르다는 것이다.

따라서 각 출처는 다음처럼 단계적으로 취급한다.

- **우선 검증**: 공식 API/RSS 또는 구조가 안정적인 공개 목록이 있고, 이번 조사에서 쓰임새를 확인한 후보. 그래도 약관·빈도·저장 조건 확인 후에만 연결.
- **조건 확인**: 수집은 기술적으로 가능해 보여도 약관, AI 입력, 재배포/보존, 인증 또는 지역 접근을 더 확인해야 하는 후보.
- **보류/제외**: 플랫폼 정책 신호가 부정적이거나 접근 차단·불안정이 있어 범용 자동수집기로 연결하지 않을 후보.

여기서 “후보”는 콘텐츠 원문이나 이미지를 복사해 저장해도 된다는 뜻이 아니다. 기본 수집 목표는 제목·URL·발표/행사 날짜·작품 후보·출처 식별자 같은 **최소 메타데이터**로 하고, 원문 링크를 보존해 사실을 다시 확인한다. 이미지와 게시물 본문은 별도 권리·정책 검토 전까지 가져오지 않는 방향이 안전하다.

## 유형별 후보 목록

각 행은 후보 **출처 레코드 수** 기준이다. 같은 사이트가 여러 유형에 반복될 수 있으므로 총합은 고유 도메인 수가 아니다. 정확한 자동화 상태는 연결 직전에 다시 확인한다.

| 유형 | 후보 수 | 후보 출처 및 역할 | 우선 수집 경로 / 판정 |
|---|---:|---|---|
| 소식 `news` | 9 | 작품별 공식 사이트·제작사 공식 채널(1차 사실); [PR TIMES 애니메이션 토픽](https://prtimes.jp/topics/keywords/anime); [ORICON 애니 뉴스](https://www.oricon.co.jp/category/anime/); [Anime! Anime!](https://animeanime.jp/); [Anime Hack](https://anime.eiga.com/news/); [Comic Natalie](https://natalie.mu/comic); [MANTANWEB](https://mantan-web.jp/); [Dengeki Online](https://dengekionline.com/); [Anime News Network](https://www.animenewsnetwork.com/) | 공식 발표 페이지 우선. 업계 매체는 **발견용**으로 쓰고 공식 원문을 재확인. PR TIMES는 RSS/목록 후보지만 이용 목적과 기사·이미지 사용 범위 별도 확인. ORICON·Anime Hack 등은 공개 기사 목록이 보이나 공식 RSS/API 및 자동수집 조건은 미확정. |
| 행사 `event` | 9 | 작품/주최자 공식 행사 페이지; [AnimeJapan](https://anime-japan.jp/); [Jump Festa](https://www.jumpfesta.com/); [Animate Fair & Event](https://www.animate-onlineshop.jp/contents/fair_event/); [Animate Cafe](https://www.animatecafe.jp/); [Bandai Namco Cross Store 행사](https://bandainamco-am.co.jp/crossstore/event/); [AGF Korea](https://agfkorea.com/); [Tokyo Anime Center](https://tokyoanimecenter.jp/); [eplus 이벤트 검색](https://eplus.jp/) | 주최자/공식 행사 HTML 목록을 우선 검토. Animate는 실제 개최일·매장 필터를 제공하고 Jump Festa는 행사 일정·회장을 공지한다. 티켓 플랫폼은 행사 발견용이며 날짜·장소는 주최자 원문에서 검증. 페이지 변경 감시 방식과 약관 확인 필요. |
| 작품 가이드 `guide` | 8 | 작품별 공식 포털·공식 캐릭터/방송 정보; [Shueisha](https://www.shueisha.co.jp/); [Kodansha](https://www.kodansha.co.jp/); [Shogakukan](https://www.shogakukan.co.jp/); [KADOKAWA](https://www.kadokawa.co.jp/); [Japan Media Arts Database](https://mediaarts-db.artmuseums.go.jp/); [Anime Hack 작품 정보](https://anime.eiga.com/); [Anime News Network Encyclopedia](https://www.animenewsnetwork.com/encyclopedia/) | 공식 출판사·작품 페이지에서 작품명, 권·연재·방영 등 확인. 공공 DB/백과사전은 발견·교차검증 후보이지 무제한 복제 데이터로 간주하지 않는다. AniList는 전체 수집이 아니라 단건 식별/링크 확인용 별도 후보로 둔다. |
| 인물 `people` | 8 | 작품 공식 출연진·제작진 페이지; [81 Produce](https://www.81produce.co.jp/); [Aoni Production](https://www.aoni.co.jp/); [Arts Vision](https://www.artsvision.co.jp/); [Ken Production](https://www.kenproduction.co.jp/); [Atomic Monkey](https://www.atomicmonkey.jp/); [Mausu Promotion](https://mausu.net/); [I'm Enterprise](https://www.imenterprise.jp/) | 소속사 공식 인명록에서 표기명/소속을 확인하고 작품 공식 출연진에서 역할을 교차검증. 소속사 페이지는 HTML 중심으로 보이며 공개 API는 확인되지 않음. 프로필 사진은 저장·재사용하지 않고 링크만 둔다. |
| 굿즈·발매 `goods` | 9 | [BANDAI SPIRITS 신상품](https://www.bandaispirits.co.jp/products/topics/); [Good Smile Company 상품·공지](https://www.goodsmile.com/ja); [Animate 발매일 캘린더](https://www.animate-onlineshop.jp/calendar/); [Premium Bandai](https://p-bandai.jp/); [Kotobukiya](https://www.kotobukiya.co.jp/); [MegaHouse](https://www.megahouse.co.jp/); [COSPA](https://www.cospa.com/); [Aniplex Online](https://www.aniplexplus.com/); [Jump Characters Store](https://jumpcs.shueisha.co.jp/) | 제조사/권리자 공지가 1차, 판매처 캘린더는 예약·발매일 발견용. Animate 캘린더에는 상품·연관 작품·출시일이 노출된다. 가격·예약 종료·발매일은 변경될 수 있어 수집 시각과 최종 확인일을 기록. 상품 이미지는 별도 허가 없이 복제하지 않는다. |
| 리뷰 `review` | 8 | [Filmarks 애니 리뷰](https://filmarks.com/animes); [Anime News Network Reviews](https://www.animenewsnetwork.com/reviews/); [Anime Feminist](https://www.animefeminist.com/); [Anime Corner](https://animecorner.me/category/anime/); [Anime Hack 칼럼](https://anime.eiga.com/news/column/); [eiga.com](https://eiga.com/); [ORICON 특집](https://www.oricon.co.jp/); 작품 공식 페이지의 감상/평론 자료 | 매체 리뷰는 관점·비평 출처로만 연결하고, 문장 복사나 작성자 의견을 에이전트의 사실처럼 쓰지 않는다. UGC 리뷰는 사용자명·본문을 저장하지 않고, 집계 가능한 공개 지표가 허용되는지 먼저 확인. 리뷰 콘텐츠 분류 태그는 `review`. |
| 유머 `humor` | 17 | [Reddit Data API](https://redditinc.com/policies/data-api-terms); [Bluesky 공개 API 문서](https://docs.bsky.app/); TheQoo 애니/만화 게시판; DCInside 작품별 갤러리; FMKorea 애니메이션 게시판; Arca.live 애니메이션 채널; 공식 작품/제작진 SNS; Reddit/Bluesky의 작품별 공개 커뮤니티·피드 | 팬 게시물은 정책과 게시판 규칙을 먼저 확인. Reddit은 현행 Data API 약관·개발자 등록·삭제 데이터 처리 조건을 따라야 하며, 자동 스크래핑 대체재로 쓰지 않는다. Bluesky 공개 API는 기술 접근 후보일 뿐 콘텐츠 이용 허가와 같지 않다. TheQoo 등은 자동 LLM 입력 허용을 추정하지 말고 현재 출처 정책 확인 전까지 보류. 본문·짤 복사 대신 허용된 공개 신호를 집계하고 원 게시물 링크를 유지. |
| 캐릭터 `character` | 8 | 작품별 공식 캐릭터 소개 페이지; [TBS 애니메이션 캐릭터 소개 예시](https://www.tbs.co.jp/anime/k-on/k-on_tv/chara/chara.html); [Shochiku 작품 캐릭터 페이지 예시](https://sh-anime.shochiku.co.jp/chimimo/characters.html); [공식 작품 포털 예시](https://www.natsume-anime.jp/character); [Japan Media Arts Database](https://mediaarts-db.artmuseums.go.jp/); [Anime News Network Encyclopedia](https://www.animenewsnetwork.com/encyclopedia/); [AniList](https://anilist.co/); 작품별 공식 캐스트 페이지 | 1차 캐릭터 사실은 작품 공식 페이지에서 가져온다. 인물·성우는 해당 작품의 공식 캐스트 표기로 교차검증. AniList/백과사전은 단건 발견·매칭 보조만 검토하고 대량 덤프/저장은 금지 또는 미승인으로 취급. |
| 큐레이션 `curation` | 8 | [Anime Trending Awards 결과](https://www.anitrendz.com/ata/results/2025); [Anime Trending 투표](https://www.anitrendz.com/polls); [Anime Corner](https://animecorner.me/); [Filmarks 애니 랭킹](https://filmarks.com/animes); [AniLab 지역별 랭킹](https://anilabb.com/); [Anime Hack 랭킹/특집](https://anime.eiga.com/news/column/); [Tokyo Anime Award Festival](https://animefestival.jp/); Newtype/공식 시상식 결과 페이지 | 랭킹은 집계기간·지역·표본/투표수·집계주체를 함께 기록. Anime Trending은 투표 결과와 표 수를 공개하는 사례를 확인했지만 전체 애니 팬덤 대표값으로 일반화하지 않는다. 사용자 리뷰 점수는 표본 편향이 있음을 표시. |
| 음악 `music` | 9 | [Lantis](https://www.lantis.jp/); [King Records](https://www.kingrecords.co.jp/); [Sony Music Japan](https://www.sonymusic.co.jp/); [FlyingDog](https://www.jvcmusic.co.jp/flyingdog/); [Pony Canyon](https://news.ponycanyon.co.jp/); [Aniplex](https://www.aniplex.co.jp/); [avex](https://avex.jp/); [KADOKAWA](https://www.kadokawa.co.jp/); [Billboard Japan](https://www.billboard-japan.com/) | 음반사/작품 공식 페이지에서 아티스트·곡·타이업·발매일을 확인. 차트는 집계기관·기간을 함께 기록. Lantis의 작품별 디스코그래피에는 발매일이 표시되는 사례가 확인됨. 음원·가사·커버 이미지는 가져오지 않고 메타데이터와 출처 링크 중심. |

## 현재 조사에서 확인한 구체 사례

- **행사·상품 목록**: Animate의 행사 목록은 행사명, 대상 매장, 업데이트 날짜와 검색 필터를 제공하고, 상품 발매일 캘린더는 출시일·상품 분류·관련 작품명을 보여준다. 이는 event/goods의 공개 목록 어댑터 후보로 적합하지만, 약관·요청 허용 빈도 확인은 별도다. ([행사 목록](https://www.animate-onlineshop.jp/contents/fair_event/), [발매일 캘린더](https://www.animate-onlineshop.jp/calendar/))
- **공식 이벤트 일정**: Jump Festa 공식 사이트에서 개최 날짜·장소와 프로그램 업데이트를 확인할 수 있다. ([Jump Festa 2027](https://www.jumpfesta.com/))
- **성우 인명록**: 81 Produce, Arts Vision, Ken Production, Atomic Monkey가 공식 배우/탤런트 목록을 운영하는 것을 확인했다. 작품 출연 사실은 소속사 프로필만으로 단정하지 말고 작품 공식 출연진도 대조한다. ([81 Produce](https://www.81produce.co.jp/), [Arts Vision](https://www.artsvision.co.jp/), [Ken Production](https://www.kenproduction.co.jp/), [Atomic Monkey](https://www.atomicmonkey.jp/))
- **작품 캐릭터**: TBS 및 Shochiku 작품 페이지에서 캐릭터명·소개·성우 정보가 제공되는 사례를 확인했다. 이는 작품별 페이지를 개별 등록하는 방식이 현실적임을 보여준다. ([TBS 예시](https://www.tbs.co.jp/anime/k-on/k-on_tv/chara/chara.html), [Shochiku 예시](https://sh-anime.shochiku.co.jp/chimimo/characters.html))
- **음악**: Lantis의 작품 디스코그래피 페이지는 CD 제목과 발매일을 함께 제공하는 사례가 있다. ([Lantis 예시](https://umamusume.lantis.jp/discography/CD/))
- **유머 커뮤니티**: Reddit 공식 Data API 약관은 앱 등록과 정책 준수를 요구하며, 삭제된 콘텐츠를 계속 보유하는 것이 위반이라고 안내한다. Bluesky 문서는 공개 AppView API가 있음을 설명하지만, 공개 접근 가능성이 게시물 재사용 허가를 뜻하지는 않는다. ([Reddit Data API Terms](https://redditinc.com/policies/data-api-terms), [Reddit API 도움말](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki), [Bluesky API](https://docs.bsky.app/))
- **큐레이션**: Anime Trending 어워드 결과 사례는 순위와 투표수·비율을 표시한다. 출처별 표본이 다르므로 “전체 팬덤 선호도”가 아니라 해당 조사 참여자의 결과로 표기해야 한다. ([결과 예시](https://www.anitrendz.com/ata/results/2025))

## 연결 가능성을 높이는 공통 설계

에이전트에 실제 반영할 때는 출처마다 아래 필드를 가진 독립 레코드와 어댑터를 둔다. 지금은 설계안이며 실행 설정에는 반영하지 않았다.

```text
source_id
content_types[]          # news/event/guide/people/goods/review/humor/character/curation/music
role                     # primary / discovery / trend / index
work_ids[]               # 작품 한정 출처일 때 menu_work_id 연결
country_scope[]          # 행사가 열리는 국가. 작품·일반 소식에는 불필요하면 비움
collection_method        # API / RSS / HTML / official_channel_metadata
collection_status        # active / terms_review / permission_required / technical_review / blocked
terms_checked_at
robots_checked_at
min_interval_seconds
allowed_fields[]         # 제목, URL, 발표일, 시작일/종료일, 장소 국가 등 최소 허용 필드
image_rights_policy      # 기본값: 파일 저장 안 함
retention_and_deletion   # 원문 삭제·수정 감지 및 보유기간
```

권장 연결 순서는 **공식 RSS/API → 공식 공개 목록 HTML → 발견용 매체/커뮤니티**다. HTML은 우선 sitemap·목록 페이지의 얕은 메타데이터만 확인하고, 약관·robots·요청 빈도를 확인한다. 작품 공식 사이트를 1,000개 개별 등록하는 대신, 사용자가 보유한 작품 카탈로그에서 공식 사이트 URL을 매핑하고 작품별 sitemap/뉴스 경로를 어댑터가 검사하는 구조가 확장성이 높다. 단, 사이트별 정책 확인은 여전히 필요하다.

## 요약

이번 확장 목록은 유형별 후보 레코드 기준 **93개**(9+9+8+8+9+9+8+8+8+8+9)다. 유형 사이에 같은 사이트가 중복되므로 고유 도메인은 이보다 적다. 이것은 “93개를 즉시 자동수집해도 된다”는 의미가 아니라 조사·검증 후보 풀이다.

실제 연결 전에는 후보마다 다음을 확정해야 한다: (1) 공식 RSS/API/목록 URL, (2) 약관과 robots 및 LLM 입력 허용 범위, (3) 허용할 최소 필드, (4) 요청 주기와 실패 시 차단, (5) 삭제·수정·행사일 변경 반영, (6) 원문 링크와 발표일 보존, (7) 작품 `menu_work_id` 매칭 기준. 자동 연결 상태 `active`는 이 검토가 끝난 출처에만 부여한다.
