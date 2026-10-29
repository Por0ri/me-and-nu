# 애니메이션 콘텐츠 출처 한눈에 보기

조사 기준일: 2026-10-21  
범위: 기존 출처 정리 + 유형별 확장 후보. **에이전트 연결 완료 목록이 아니다.** 후보 수는 조사 당시 분류의 출처 목록을 가리키며 같은 사이트가 여러 유형에 반복될 수 있다. 비하인드 후보는 현재 `humor` 유형에 포함한다.

## 유형별 출처 맵

| 콘텐츠 유형 | 후보 수 | 우선 출처 | 발견·보조 출처 | 자동수집 판단 |
|---|---:|---|---|---|
| **소식** `news` | 9 | 작품·제작사 공식 사이트, [PR TIMES](https://prtimes.jp/topics/keywords/anime) | [ORICON](https://www.oricon.co.jp/category/anime/), [Anime! Anime!](https://animeanime.jp/), [Anime Hack](https://anime.eiga.com/news/), [Comic Natalie](https://natalie.mu/comic), [MANTANWEB](https://mantan-web.jp/), [Dengeki Online](https://dengekionline.com/), [ANN](https://www.animenewsnetwork.com/) | 공식 원문 우선. 매체는 발견용, 사실은 공식 발표로 재검증. RSS/API 및 약관은 출처별 확인. |
| **행사** `event` | 9 | 주최자·작품 공식 행사 페이지, [AnimeJapan](https://anime-japan.jp/), [Jump Festa](https://www.jumpfesta.com/) | [Animate 행사](https://www.animate-onlineshop.jp/contents/fair_event/), [Animate Cafe](https://www.animatecafe.jp/), [Bandai Namco Cross Store](https://bandainamco-am.co.jp/crossstore/event/), [AGF Korea](https://agfkorea.com/), [Tokyo Anime Center](https://tokyoanimecenter.jp/), [eplus](https://eplus.jp/) | 주최자 원문에서 행사 국가·장소·시작일·종료일 확인. 티켓/캘린더는 발견용. |
| **작품 가이드** `guide` | 8 | 작품 공식 포털·출판사: [Shueisha](https://www.shueisha.co.jp/), [Kodansha](https://www.kodansha.co.jp/), [Shogakukan](https://www.shogakukan.co.jp/), [KADOKAWA](https://www.kadokawa.co.jp/) | [Japan Media Arts Database](https://mediaarts-db.artmuseums.go.jp/), [Anime Hack](https://anime.eiga.com/), [ANN Encyclopedia](https://www.animenewsnetwork.com/encyclopedia/), [AniList](https://anilist.co/) | 공식 정보 우선. 데이터베이스는 단건 확인·발견용으로 제한하고 대량 동기화는 하지 않음. |
| **인물** `people` | 8 | 작품 공식 출연진·제작진 페이지, [81 Produce](https://www.81produce.co.jp/), [Aoni Production](https://www.aoni.co.jp/), [Arts Vision](https://www.artsvision.co.jp/) | [Ken Production](https://www.kenproduction.co.jp/), [Atomic Monkey](https://www.atomicmonkey.jp/), [Mausu Promotion](https://mausu.net/), [I'm Enterprise](https://www.imenterprise.jp/) | 소속사 프로필과 작품별 배역을 교차 확인. 프로필 사진은 기본적으로 저장하지 않음. |
| **굿즈·발매** `goods` | 9 | 제조사/권리자: [BANDAI SPIRITS](https://www.bandaispirits.co.jp/products/topics/), [Good Smile](https://www.goodsmile.com/ja), [Premium Bandai](https://p-bandai.jp/), [Kotobukiya](https://www.kotobukiya.co.jp/), [MegaHouse](https://www.megahouse.co.jp/) | [Animate 발매일 캘린더](https://www.animate-onlineshop.jp/calendar/), [COSPA](https://www.cospa.com/), [Aniplex Online](https://www.aniplexplus.com/), [Jump Characters Store](https://jumpcs.shueisha.co.jp/) | 제조사 공지 우선, 판매처는 발매일 발견용. 가격·예약·발매일 변경을 재확인. 상품 이미지는 별도 검토. |
| **리뷰·분석** `review` | 8 | [Filmarks](https://filmarks.com/animes), [Anime News Network Reviews](https://www.animenewsnetwork.com/reviews/), [Anime Feminist](https://www.animefeminist.com/) | [Anime Corner](https://animecorner.me/category/anime/), [Anime Hack 칼럼](https://anime.eiga.com/news/column/), [eiga.com](https://eiga.com/), [ORICON](https://www.oricon.co.jp/), 작품 공식 페이지 | 리뷰는 의견 출처로만 사용하고 본문을 복제하지 않음. 사용자 리뷰는 허용 여부 확인 전 자동수집 보류. 태그는 `review`. |
| **유머** `humor` | 17 | [Reddit 공식 Data API](https://redditinc.com/policies/data-api-terms), [Bluesky API](https://docs.bsky.app/) | [더쿠](https://theqoo.net/), [디시인사이드](https://gall.dcinside.com/), [에펨코리아](https://www.fmkorea.com/ani), [아카라이브](https://arca.live/), 작품·제작진 공식 SNS | 커뮤니티 글·짤 자동 복제는 보류. 공식 API/게시판 규칙 및 AI 입력·삭제 조건 확인 후 최소한의 화제 신호만 검토. |
| **캐릭터** `character` | 8 | 작품별 공식 캐릭터·출연진 페이지 | [TBS 캐릭터 소개 예시](https://www.tbs.co.jp/anime/k-on/k-on_tv/chara/chara.html), [Shochiku 예시](https://sh-anime.shochiku.co.jp/chimimo/characters.html), [나츠메 우인장 공식](https://www.natsume-anime.jp/character), [Japan Media Arts Database](https://mediaarts-db.artmuseums.go.jp/), [ANN Encyclopedia](https://www.animenewsnetwork.com/encyclopedia/), [AniList](https://anilist.co/) | 이름·설정·성우는 공식 작품 페이지 우선. 색인 DB는 단건 매칭 보조만. |
| **큐레이션** `curation` | 8 | [Anime Trending 투표·결과](https://www.anitrendz.com/polls), [Tokyo Anime Award Festival](https://animefestival.jp/) | [Anime Corner](https://animecorner.me/), [Filmarks](https://filmarks.com/animes), [AniLab](https://anilabb.com/), [Anime Hack](https://anime.eiga.com/news/column/), Newtype/공식 시상식 결과 | 순위와 함께 조사 기간·지역·표본·집계 주체를 기록. 특정 투표를 전체 팬덤 취향으로 일반화하지 않음. |
| **음악** `music` | 9 | [Lantis](https://www.lantis.jp/), [King Records](https://www.kingrecords.co.jp/), [Sony Music Japan](https://www.sonymusic.co.jp/), [FlyingDog](https://www.jvcmusic.co.jp/flyingdog/), [Pony Canyon](https://news.ponycanyon.co.jp/), [Aniplex](https://www.aniplex.co.jp/), [avex](https://avex.jp/), [KADOKAWA](https://www.kadokawa.co.jp/) | [Billboard Japan](https://www.billboard-japan.com/) | 레이블 공식 발매 정보 우선. 차트는 집계 기간·기관 표시. 음원·가사·커버 이미지는 가져오지 않음. |

## 수집 상태 읽는 법

- **공식 1차 출처**: 사실 확인의 기준. 작품·제작사·권리자·행사 주최자·레이블이 직접 발표한 페이지.
- **발견·보조 출처**: 주제나 변경 소식을 찾는 용도. 중요한 사실은 1차 출처에서 다시 확인.
- **조건 확인**: 기술적으로 접근 가능해도 약관, RSS/API의 실제 상태, 호출 간격, AI 입력·저장 조건 확인 전에는 자동 연결하지 않음.
- **보류**: 커뮤니티 UGC, 접근 차단·불안정 출처 등. 허가·정책 확인 전에는 자동수집 대상으로 삼지 않음.

## 현재 결론

**93건은 조사 후보 풀이지, 연결 완료나 자동수집 승인 수가 아니다.** 사이트마다 별도 어댑터와 검증이 필요하다. 첫 단계는 공식 RSS/API에서 제목·URL·게시일 같은 최소 메타데이터만 가져오고, HTML 목록과 커뮤니티는 조건을 확인한 뒤 단계적으로 검토하는 것이다. 이미지와 원문 전체는 기본 수집 대상에서 제외한다.

상세 확인: [후보별 목록 및 사례](EXPANDED_SOURCE_CANDIDATES.md) · [자동수집 가능성/조건 검토](AUTOMATED_SOURCE_FEASIBILITY.md) · [기존 사이트별 감사](CONTENT_TYPE_SOURCE_CATALOG.md) · [현재 등록된 수집 사이트](SOURCES.md)
