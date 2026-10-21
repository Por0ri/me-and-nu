import type { Topic } from "@/types/home";

// FE Flow 검증용 임시 Mock ID이며 Backend의 최종 Topic ID Contract가 아니다.
export const MUSIC_TOPIC_ID = "topic-music";
export const MOVIE_TOPIC_ID = "topic-movie";
export const ANIME_TOPIC_ID = "topic-anime";

export const mockTopics: Topic[] = [
  {
    id: MUSIC_TOPIC_ID,
    name: "음악",
  },
  {
    id: MOVIE_TOPIC_ID,
    name: "영화",
  },
  {
    id: ANIME_TOPIC_ID,
    name: "애니메이션",
  },
];
