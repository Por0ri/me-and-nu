import {
  ANIME_TOPIC_ID,
  mockTopics,
  MOVIE_TOPIC_ID,
  MUSIC_TOPIC_ID,
} from "@/mocks/topics";
import type {
  InterpretedSubtopic,
  LoginProviderOption,
  LoginResult,
  OnboardingOptions,
  SubtopicOption,
} from "@/types/consumer";

export const mockLoginProviderOptions: LoginProviderOption[] = [
  {
    id: "kakao",
    label: "카카오 로그인",
  },
  {
    id: "naver",
    label: "네이버 로그인",
  },
];

export const mockLoginResult: Omit<LoginResult, "providerId"> = {
  authStatus: "authenticated",
  signupStatus: "new",
};

/**
 * Consumer MVP의 세 Topic에서 동일한 선택 Flow를 확인하기 위한 임시 Mock이다.
 * 이 목록과 ID는 최종 Subtopic taxonomy나 canonical ID Contract가 아니다.
 */
export const mockSubtopicOptionsByTopicId: Record<
  string,
  SubtopicOption[]
> = {
  [MUSIC_TOPIC_ID]: [
    { id: "topic-music-rnb", label: "알앤비" },
    { id: "topic-music-soul", label: "소울" },
    { id: "topic-music-hiphop", label: "힙합" },
    { id: "topic-music-indie", label: "인디" },
    { id: "topic-music-production", label: "프로덕션" },
    { id: "topic-music-pop", label: "팝" },
    { id: "topic-music-jazz", label: "재즈" },
  ],
  [MOVIE_TOPIC_ID]: [
    { id: "topic-movie-directing", label: "연출" },
    { id: "topic-movie-cinematography", label: "촬영" },
    { id: "topic-movie-actor", label: "배우" },
    { id: "topic-movie-story", label: "스토리" },
    { id: "topic-movie-mood", label: "분위기" },
    { id: "topic-movie-sf", label: "SF" },
    { id: "topic-movie-comedy", label: "코미디" },
  ],
  [ANIME_TOPIC_ID]: [
    { id: "topic-anime-animation", label: "작화" },
    { id: "topic-anime-voice-actor", label: "성우" },
    { id: "topic-anime-studio", label: "스튜디오" },
    { id: "topic-anime-worldbuilding", label: "세계관" },
    { id: "topic-anime-action", label: "액션" },
    { id: "topic-anime-isekai", label: "이세계" },
    { id: "topic-anime-healing", label: "힐링" },
  ],
};

export const mockOnboardingOptions: OnboardingOptions = {
  accountTypes: ["consumer"],
  topics: mockTopics.map((topic) => ({
    id: topic.id,
    label: topic.name,
    subtopicOptions: mockSubtopicOptionsByTopicId[topic.id] ?? [],
  })),
};

/**
 * 실제 AI 의미 분석 없이 Topic별 Merge Flow를 검증하기 위한 고정 Mock이다.
 * 각 결과에는 선택형 Chip과 ID가 겹치는 값과 Interpretation 전용 값이 있다.
 * 실제 AI 결과가 반드시 선택형 Chip 목록에 속해야 하는 것은 아니며,
 * 의미상 중복 제거, taxonomy, canonical ID는 추후 PM/Data/Backend 협의 대상이다.
 */
export const mockInterpretedSubtopicsByTopicId: Record<
  string,
  InterpretedSubtopic[]
> = {
  [MUSIC_TOPIC_ID]: [
    { id: "topic-music-soul", label: "소울" },
    { id: "topic-music-male-vocals", label: "남성보컬" },
  ],
  [MOVIE_TOPIC_ID]: [
    { id: "topic-movie-cinematography", label: "촬영" },
    { id: "topic-movie-noir", label: "느와르" },
  ],
  [ANIME_TOPIC_ID]: [
    { id: "topic-anime-animation", label: "작화" },
    { id: "topic-anime-dark-fantasy", label: "다크판타지" },
  ],
};
