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
  title: "장면의 감정을 설계하는 연출",
  summary: "인물과 공간을 통해 장면의 감정을 전달하는 연출 방식을 살펴봅니다.",
  sourceName: "Mock Film Review",
  saved: false,
  recommendationReason: "영화 연출 관심사와 관련된 콘텐츠입니다.",
};

const movieCinematographyContent: Content = {
  id: "content-movie-cinematography",
  title: "빛과 구도로 완성하는 영화의 시선",
  summary: "촬영과 조명이 서사의 관점을 만드는 과정을 소개합니다.",
  sourceName: "Mock Cinema Journal",
  saved: false,
  recommendationReason: "영화 촬영 기법을 깊게 보고 싶은 취향을 반영했습니다.",
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
  [MOVIE_TOPIC_ID]: [movieDirectingContent, movieCinematographyContent],
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
      body: "이 Mock 본문은 장면의 감정을 구성하는 영화 연출에 대한 상세 화면 검증용 데이터입니다.",
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
      body: "이 Mock 본문은 영화의 촬영과 조명에 대한 상세 화면 검증용 데이터입니다.",
      sources: [
        {
          id: "source-movie-cinematography",
          name: "Mock Cinema Journal 원문",
          url: "https://example.com/movie/cinematography",
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
