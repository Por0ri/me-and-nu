import type { Source } from "@/types/content";
import type { Topic, TopicContext } from "@/types/home";

export type EvidenceStatus = "sufficient" | "insufficient";

export type ContentAIRequest = {
  contentId: string;
  question: string;
  topicContext: TopicContext;
};

// FE Mock/View Model이며 실제 Backend API DTO 확정본이 아니다. 실제 연동 시 Adapter 매핑 대상이다.
export type ContentAIResponse = {
  answer: string;
  sources: Source[];
  evidenceStatus: EvidenceStatus;
  sourceTopicId?: string;
  affectsPreference: boolean;
};

// Home 전용 FE Mock/View Model이다. 실제 Backend API-053 DTO 확정본이 아니다.
export type HomeAIRequest = {
  topicContext: TopicContext;
  question: string;
};

export type HomeAIResponse =
  | { type: "answer"; answer: string }
  | { type: "topicSwitchSuggested"; targetTopic: Topic; message: string };
