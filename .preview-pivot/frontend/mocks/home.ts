import { mockContentsByTopicId } from "@/mocks/contents";
import {
  ANIME_TOPIC_ID,
  mockTopics,
  MOVIE_TOPIC_ID,
  MUSIC_TOPIC_ID,
} from "@/mocks/topics";
import type { HomeData, HomeSection } from "@/types/home";

const musicContents = mockContentsByTopicId[MUSIC_TOPIC_ID];
const movieContents = mockContentsByTopicId[MOVIE_TOPIC_ID];
const animeContents = mockContentsByTopicId[ANIME_TOPIC_ID];

export const mockHomeDataByTopicId: Record<string, HomeData> = {
  [MUSIC_TOPIC_ID]: {
    topics: mockTopics,
    selectedTopicId: MUSIC_TOPIC_ID,
    sections: [
      {
        id: "section-music-production",
        title: "프로덕션 이야기",
        contents: [musicContents[0]],
      },
      {
        id: "section-music-scene",
        title: "새로운 음악 신",
        contents: [musicContents[1]],
      },
    ],
  },
  [MOVIE_TOPIC_ID]: {
    topics: mockTopics,
    selectedTopicId: MOVIE_TOPIC_ID,
    sections: [
      {
        id: "section-movie-directing",
        title: "괴물",
        contents: [movieContents[0]],
      },
      {
        id: "section-movie-cinematography",
        title: "드라마",
        contents: [movieContents[1]],
      },
      {
        id: "section-movie-adventure",
        title: "모험",
        contents: [movieContents[2]],
      },
      {
        id: "section-movie-fantasy",
        title: "판타지",
        contents: [movieContents[3]],
      },
      {
        id: "section-movie-sci-fi",
        title: "SF",
        contents: [movieContents[4]],
      },
    ],
  },
  [ANIME_TOPIC_ID]: {
    topics: mockTopics,
    selectedTopicId: ANIME_TOPIC_ID,
    sections: [
      {
        id: "section-anime-animation",
        title: "애니메이션 작화",
        contents: [animeContents[0]],
      },
      {
        id: "section-anime-worldbuilding",
        title: "세계관 깊이 보기",
        contents: [animeContents[1]],
      },
    ],
  },
};

/** Home AI가 반환하는 고정 Mock 결과다. 입력 분석이나 추천 로직을 수행하지 않는다. */
export const mockIntentSectionsByTopicId: Record<string, HomeSection[]> = {
  [MUSIC_TOPIC_ID]: [
    {
      id: "section-music-scene",
      title: "현재 요구에 맞춘 음악 신",
      contents: [musicContents[1]],
    },
    {
      id: "section-music-production",
      title: "이어서 볼 프로덕션 이야기",
      contents: [musicContents[0]],
    },
  ],
  [MOVIE_TOPIC_ID]: [
    {
      id: "section-movie-cinematography",
      title: "현재 요구에 맞춘 영화 촬영",
      contents: [movieContents[1]],
    },
    {
      id: "section-movie-directing",
      title: "이어서 볼 영화 연출",
      contents: [movieContents[0]],
    },
  ],
};
