import {
  ANIME_TOPIC_ID,
  MOVIE_TOPIC_ID,
  MUSIC_TOPIC_ID,
} from "@/mocks/topics";
import type { Content, ContentDetail } from "@/types/content";

const musicProductionContent: Content = {
  id: "content-music-production",
  title: "프로듀서가 설계하는 새로운 사운드",
  summary: "프로듀서의 제작 방식과 최근 사운드의 변화를 살펴봅니다.",
  sourceName: "Mock Music Review",
  saved: false,
  recommendationReason: "음악 프로덕션 관심사와 연결된 콘텐츠입니다.",
};

const musicSceneContent: Content = {
  id: "content-music-scene",
  title: "작은 공연장에서 시작된 장면들",
  summary: "지역 음악 신과 창작자들이 연결되는 방식을 소개합니다.",
  sourceName: "Mock Culture Journal",
  saved: false,
  recommendationReason: "새로운 음악 신을 깊게 보고 싶은 취향을 반영했습니다.",
};

const movieDirectingContent: Content = {
  id: "content-movie-directing",
  title: "대낮에 나타난 괴물보다 더 무서운 것",
  summary: "발표용 Mock: 괴물과 마주한 인물들의 두려움을 연출의 관점에서 살펴봅니다.",
  sourceName: "Mock Film Review",
  saved: false,
  recommendationReason: "영화 연출 관심사와 관련된 콘텐츠입니다.",
};

const movieCinematographyContent: Content = {
  id: "content-movie-cinematography",
  title: "엇갈린 속도로 흐르는 여름",
  summary: "발표용 Mock: 서로 다른 속도로 성장하는 인물들과 여름의 분위기를 소개합니다.",
  sourceName: "Mock Cinema Journal",
  saved: false,
  recommendationReason: "영화 촬영 기법을 깊게 보고 싶은 취향을 반영했습니다.",
};

const movieAdventureContent: Content = {
  id: "content-movie-adventure",
  title: "돌아가는 사람의 이름",
  summary: "발표용 Mock: 귀환을 향한 모험과 여정 속 인물의 선택을 살펴봅니다.",
  sourceName: "Mock Adventure Review",
  saved: false,
  recommendationReason: "영화 속 모험과 인물의 이야기를 소개하는 Mock 추천입니다.",
};

const movieFantasyContent: Content = {
  id: "content-movie-fantasy",
  title: "현실을 견디는 어두운 동화",
  summary: "발표용 Mock: 현실과 상상의 경계에서 펼쳐지는 판타지 표현을 소개합니다.",
  sourceName: "Mock Fantasy Journal",
  saved: false,
  recommendationReason: "영화의 판타지와 세계관을 소개하는 Mock 추천입니다.",
};

const movieSciFiContent: Content = {
  id: "content-movie-sci-fi",
  title: "기억을 잃은 교사가 우주에서 맡은 마지막 임무",
  summary: "발표용 Mock: 기억과 임무를 따라가는 우주 SF의 이야기 구성을 살펴봅니다.",
  sourceName: "Mock SF Review",
  saved: false,
  recommendationReason: "영화의 SF 설정과 서사를 소개하는 Mock 추천입니다.",
};

const animeAnimationContent: Content = {
  id: "content-anime-animation",
  title: "움직임으로 완성하는 애니메이션 작화",
  summary: "인물의 동작과 장면의 리듬을 만드는 작화 표현을 살펴봅니다.",
  sourceName: "Mock Anime Review",
  saved: false,
  recommendationReason: "애니메이션 작화에 대한 취향과 연결된 콘텐츠입니다.",
};

const animeWorldbuildingContent: Content = {
  id: "content-anime-worldbuilding",
  title: "이야기를 확장하는 세계관의 설계",
  summary: "작품의 규칙과 배경이 서사를 지탱하는 방식을 소개합니다.",
  sourceName: "Mock Animation Journal",
  saved: false,
  recommendationReason: "애니메이션 세계관을 깊게 보고 싶은 취향을 반영했습니다.",
};

export const mockContentsByTopicId: Record<string, Content[]> = {
  [MUSIC_TOPIC_ID]: [musicProductionContent, musicSceneContent],
  [MOVIE_TOPIC_ID]: [
    movieDirectingContent,
    movieCinematographyContent,
    movieAdventureContent,
    movieFantasyContent,
    movieSciFiContent,
  ],
  [ANIME_TOPIC_ID]: [animeAnimationContent, animeWorldbuildingContent],
};

export const mockContentDetailsByTopicId: Record<string, ContentDetail[]> = {
  [MUSIC_TOPIC_ID]: [
    {
      ...musicProductionContent,
      feedback: null,
      body: "이 Mock 본문은 제작 과정과 사운드 선택을 설명하는 상세 화면 검증용 데이터입니다.",
      sources: [
        {
          id: "source-music-production",
          name: "Mock Music Review 원문",
          url: "https://example.com/music/production",
        },
      ],
      topicContext: { topicId: MUSIC_TOPIC_ID },
    },
    {
      ...musicSceneContent,
      feedback: null,
      body: "이 Mock 본문은 지역 음악 신에 대한 상세 화면 검증용 데이터입니다.",
      sources: [
        {
          id: "source-music-scene",
          name: "Mock Culture Journal 원문",
          url: "https://example.com/music/scene",
        },
      ],
      topicContext: { topicId: MUSIC_TOPIC_ID },
    },
  ],
  [MOVIE_TOPIC_ID]: [
    {
      ...movieDirectingContent,
      feedback: null,
      body: "발표용 Mock 본문입니다. 괴물과 마주한 인물들의 두려움을 장면과 공간의 연출 관점에서 소개합니다. 실제 기사나 작품 분석을 인용한 내용이 아닌 Detail 화면 검증용 데이터입니다.",
      sources: [
        {
          id: "source-movie-directing",
          name: "Mock Film Review 원문",
          url: "https://example.com/movie/directing",
        },
      ],
      topicContext: { topicId: MOVIE_TOPIC_ID },
    },
    {
      ...movieCinematographyContent,
      feedback: null,
      body: "발표용 Mock 본문입니다. 서로 다른 속도로 성장하는 인물들과 여름의 분위기를 촬영과 장면 구성의 관점에서 소개합니다. 실제 기사나 작품 분석을 인용한 내용이 아닌 Detail 화면 검증용 데이터입니다.",
      sources: [
        {
          id: "source-movie-cinematography",
          name: "Mock Cinema Journal 원문",
          url: "https://example.com/movie/cinematography",
        },
      ],
      topicContext: { topicId: MOVIE_TOPIC_ID },
    },
    {
      ...movieAdventureContent,
      feedback: null,
      body: "발표용 Mock 본문입니다. 귀환을 향한 여정과 그 안에서 인물이 내리는 선택을 모험 서사의 관점에서 소개합니다. 실제 기사나 작품 분석을 인용한 내용이 아닌 Detail 화면 검증용 데이터입니다.",
      sources: [
        {
          id: "source-movie-adventure",
          name: "Mock Adventure Review 데모 원문",
          url: "https://example.com/movie/adventure",
        },
      ],
      topicContext: { topicId: MOVIE_TOPIC_ID },
    },
    {
      ...movieFantasyContent,
      feedback: null,
      body: "발표용 Mock 본문입니다. 현실과 상상의 경계를 오가는 인물을 통해 어두운 동화의 분위기와 판타지 표현을 소개합니다. 실제 기사나 작품 분석을 인용한 내용이 아닌 Detail 화면 검증용 데이터입니다.",
      sources: [
        {
          id: "source-movie-fantasy",
          name: "Mock Fantasy Journal 데모 원문",
          url: "https://example.com/movie/fantasy",
        },
      ],
      topicContext: { topicId: MOVIE_TOPIC_ID },
    },
    {
      ...movieSciFiContent,
      feedback: null,
      body: "발표용 Mock 본문입니다. 기억을 잃은 교사와 우주에서의 임무라는 설정을 통해 SF 서사의 긴장감을 소개합니다. 실제 기사나 작품 분석을 인용한 내용이 아닌 Detail 화면 검증용 데이터입니다.",
      sources: [
        {
          id: "source-movie-sci-fi",
          name: "Mock SF Review 데모 원문",
          url: "https://example.com/movie/sci-fi",
        },
      ],
      topicContext: { topicId: MOVIE_TOPIC_ID },
    },
  ],
  [ANIME_TOPIC_ID]: [
    {
      ...animeAnimationContent,
      feedback: null,
      body: "이 Mock 본문은 애니메이션의 동작과 장면 리듬에 대한 상세 화면 검증용 데이터입니다.",
      sources: [
        {
          id: "source-anime-animation",
          name: "Mock Anime Review 원문",
          url: "https://example.com/anime/animation",
        },
      ],
      topicContext: { topicId: ANIME_TOPIC_ID },
    },
    {
      ...animeWorldbuildingContent,
      feedback: null,
      body: "이 Mock 본문은 애니메이션 세계관의 규칙과 배경에 대한 상세 화면 검증용 데이터입니다.",
      sources: [
        {
          id: "source-anime-worldbuilding",
          name: "Mock Animation Journal 원문",
          url: "https://example.com/anime/worldbuilding",
        },
      ],
      topicContext: { topicId: ANIME_TOPIC_ID },
    },
  ],
};
