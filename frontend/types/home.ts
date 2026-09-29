import type { Content } from "@/types/content";

export type Topic = {
  id: string;
  name: string;
  code?: string;
};

/**
 * FE Mock 단계에서 Topic별 요청과 상태를 분리하기 위한 임시 Context다.
 * Backend의 최종 Topic Contract를 의미하지 않는다.
 */
export type TopicContext = {
  topicId: string;
};

export type HomeSection = {
  id: string;
  title: string;
  contents: Content[];
};

export type HomeData = {
  topics: Topic[];
  selectedTopicId: string;
  sections: HomeSection[];
  currentIntent?: string;
};

export type GetHomeContentsInput = {
  topicId: string;
};

export type HomeIntentRequest = {
  topicId: string;
  intent: string;
};

export type HomeIntentResponse = {
  topicId: string;
  currentIntent: string;
  sections: HomeSection[];
};
