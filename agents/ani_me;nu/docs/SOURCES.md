# 수집 사이트와 정보

| 사이트 | 수집·확인할 정보 |
|---|---|
| [AnimeSchedule](https://animeschedule.net/jpnrss.xml) | 신작·방영 일정 후보 |
| [애니플러스](https://www.aniplustv.com/) | 국내 방영·행사·콜라보 |
| [애니플러스 뉴스](https://news.aniplustv.com/) | 국내 애니메이션 소식 |
| [Crunchyroll Announcements](https://www.crunchyroll.com/news/announcements) | 해외 방영·서비스 발표 |
| [HIDIVE Press](https://news.hidive.com/press) | 해외 방영·서비스 발표 |
| [Bilibili Global](https://www.bilibili.tv/en) | 아시아권 공개·방영 정보 |
| [Muse Asia YouTube](https://www.youtube.com/@MuseAsia) | 아시아권 공개·방영 정보 |
| [Ani-One Asia YouTube](https://www.youtube.com/@AniOneAsia) | 아시아권 공개·방영 정보 |
| [MyAnimeList News](https://myanimelist.net/news) | 신작·성우·제작 소식 후보 |
| [Anime News Network](https://www.animenewsnetwork.com/) | 신작·성우·제작 소식 후보 |
| [Animenomics](https://news.animenomics.com/) | 애니메이션 산업·비즈니스 소식 |
| [Anime Hack 이벤트 캘린더](https://anime.eiga.com/event/) | 일본 행사 일정 후보 |
| [Anime! Anime! 굿즈](https://animeanime.jp/category/news/goods/latest/) | 굿즈 소식 후보 |
| [Animate Times 굿즈](https://www.animatetimes.com/goods/) | 굿즈 소식 후보 |
| [AmiAmi](https://www.amiami.com/eng/) | 예약·재판 상품 후보 |
| [애니뉴잉](https://aninewing.com/newsboard) | 국내 애니메이션 소식 후보 |
| [온나다](https://onnada.com/) | 국내 애니메이션 편성·정보 후보 |
| [루리웹 애니 게시판](https://m.ruliweb.com/family/211/board/300015) | 팬 관심사·질문·반응 |
| [Reddit r/anime](https://www.reddit.com/r/anime/) | 해외 팬 관심사·질문·반응 |
| [더쿠 애니/만화](https://theqoo.net/) | 팬덤 밈·반응 참고용. 약관 검토 전 자동 수집 비활성 |
| [디시인사이드 애니메이션 관련 갤러리](https://gall.dcinside.com/) | 팬덤 밈·반응 참고용. 사전 서면 동의 전 자동 수집 금지 |
| [아카라이브 애니메이션 채널](https://arca.live/) | 팬덤 밈·반응 참고용. 약관·접근 검토 전 자동 수집 비활성 |
| [에펨코리아 애니메이션 게시판](https://www.fmkorea.com/ani) | 팬덤 밈·반응 참고용. 약관·접근 검토 전 자동 수집 비활성 |
| [LiveChart](https://www.livechart.me/) | 방영 일정 후보 |
| [AniList GraphQL API](https://graphql.anilist.co) | 방영 일정 후보 |

수집 연결기는 RSS·공개 API·HTML 목록을 지원합니다. `discover`는 허용된 활성 출처만 호출합니다. 커뮤니티 게시물·이미지는 자동 복제하지 않고 관심도 참고용으로만 다룹니다. 새 네 곳은 현재 `manual` 방식으로 등록되어 자동 접근 대상이 아닙니다. 같은 호스트에는 기본 2초 간격을 두며, robots.txt·약관·사이트 정책이 허용하지 않으면 중단합니다.

`terms_review`, `permission_required`, `technical_review`, `setup_required`, `blocked`, `later` 상태는 자동 호출하지 않습니다. 특히 LiveChart의 차단을 우회하지 않으며 YouTube 채널은 실제 채널 ID로 만든 RSS URL이 등록되기 전까지 보류합니다. 자동 예약 실행은 아직 없고, 현재 후보 수집은 `discover` 명령으로 실행합니다. 최종 정보는 공식 1차 출처에서 다시 검증해야 합니다.
