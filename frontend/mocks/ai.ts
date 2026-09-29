import {
  ANIME_TOPIC_ID,
  MOVIE_TOPIC_ID,
  MUSIC_TOPIC_ID,
} from "@/mocks/topics";
import type { ContentAIResponse } from "@/types/ai";

// 콘텐츠별 고정 응답이다. 질문 분석 / 실제 AI 품질 / RAG 검증용이 아니다.
export const mockContentAIResponsesByTopicId: Record<
  string,
  Record<string, ContentAIResponse>
> = {
  [MUSIC_TOPIC_ID]: {
    "content-music-production": {
      answer:
        "이 콘텐츠는 프로듀서가 제작 과정에서 내리는 사운드 선택을 중심으로 설명합니다.",
      sources: [
        {
          id: "source-ai-music-production",
          name: "Mock Music Review 참고 자료",
          url: "https://example.com/music/production/reference",
        },
      ],
      evidenceStatus: "sufficient",
      sourceTopicId: MUSIC_TOPIC_ID,
      affectsPreference: false,
    },
    "content-music-scene": {
      answer:
        "장면 연출과 비교하기 위해 영화 분야의 참고 자료를 사용했지만, 음악 분야의 장기 Preference에는 반영하지 않습니다.",
      sources: [
        {
          id: "source-ai-cross-domain",
          name: "Mock Movie Reference",
          url: "https://example.com/movie/reference",
        },
      ],
      evidenceStatus: "sufficient",
      sourceTopicId: MOVIE_TOPIC_ID,
      affectsPreference: false,
    },
  },
  [MOVIE_TOPIC_ID]: {
    "content-movie-directing": {
      answer:
        "이 콘텐츠는 인물과 공간을 활용해 장면의 감정을 구성하는 연출 방식을 다룹니다.",
      sources: [
        {
          id: "source-ai-movie-directing",
          name: "Mock Film Review 참고 자료",
          url: "https://example.com/movie/directing/reference",
        },
      ],
      evidenceStatus: "sufficient",
      sourceTopicId: MOVIE_TOPIC_ID,
      affectsPreference: false,
    },
    "content-movie-cinematography": {
      answer: "현재 Mock Source만으로는 이 질문에 답할 충분한 근거가 없습니다.",
      sources: [],
      evidenceStatus: "insufficient",
      affectsPreference: false,
    },
  },
  [ANIME_TOPIC_ID]: {
    "content-anime-animation": {
      answer:
        "이 콘텐츠는 인물의 움직임과 장면 리듬을 만드는 애니메이션 작화 표현을 다룹니다.",
      sources: [
        {
          id: "source-ai-anime-animation",
          name: "Mock Anime Review 참고 자료",
          url: "https://example.com/anime/animation/reference",
        },
      ],
      evidenceStatus: "sufficient",
      sourceTopicId: ANIME_TOPIC_ID,
      affectsPreference: false,
    },
    "content-anime-worldbuilding": {
      answer:
        "이 콘텐츠는 작품 속 규칙과 배경이 이야기를 지탱하는 방식을 설명합니다.",
      sources: [
        {
          id: "source-ai-anime-worldbuilding",
          name: "Mock Animation Journal 참고 자료",
        },
      ],
      evidenceStatus: "sufficient",
      sourceTopicId: ANIME_TOPIC_ID,
      affectsPreference: false,
    },
  },
};

// Home 전용 고정 답변이다. Content AI Fixture나 Home Feed를 변경하지 않는다.
export const mockHomeAIAnswersByTopicId: Record<string, string> = {
  [MUSIC_TOPIC_ID]:
    "음악 분야에서는 사운드와 제작 과정, 음악 장면에 대해 살펴볼 수 있습니다. 현재는 질문과 무관한 고정 Mock 답변입니다.",
  [MOVIE_TOPIC_ID]:
    "영화 분야에서는 연출과 촬영, 이야기 표현에 대해 살펴볼 수 있습니다. 현재는 질문과 무관한 고정 Mock 답변입니다.",
  [ANIME_TOPIC_ID]:
    "애니메이션 분야에서는 작화와 세계관, 장면 표현에 대해 살펴볼 수 있습니다. 현재는 질문과 무관한 고정 Mock 답변입니다.",
};

// trim 후 정확히 일치하는 예시에서만 분야 이동 제안을 재현한다.
// 실제 자연어 분류가 아니며 이외 모든 질문은 현재 분야의 고정 답변을 사용한다.
export const mockHomeAIQuestionTargets = [
  { question: "음악 이야기를 알려주세요", topicId: MUSIC_TOPIC_ID },
  { question: "영화 이야기를 알려주세요", topicId: MOVIE_TOPIC_ID },
  { question: "애니메이션 이야기를 알려주세요", topicId: ANIME_TOPIC_ID },
];
