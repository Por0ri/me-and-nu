import type { Topic, TopicContext } from "@/types/home";

export type Source = {
  id: string;
  name: string;
  url?: string;
};

export type Content = {
  id: string;
  title: string;
  summary: string;
  imageUrl?: string;
  sourceName: string;
  saved: boolean;
  recommendationReason?: string;
};

/** 현재 Mock의 추천 적합도 선택값이며 Backend 최종 Contract가 아니다. */
export type ContentFeedback = "O" | "X";

export type ContentDetail = Content & {
  body?: string;
  sources: Source[];
  topicContext: TopicContext;
  feedback: ContentFeedback | null;
};

export type GetContentInput = {
  contentId: string;
  topicContext: TopicContext;
};

export type SaveContentInput = GetContentInput;

export type GetSavedContentsInput = {
  topicId: string;
};

/** FE 목록 화면용 반환 형태이며 Backend DTO가 아니다. */
export type SavedContentsData = {
  topic: Topic;
  contents: Content[];
};

export type SetContentFeedbackInput = GetContentInput & {
  feedback: ContentFeedback;
};

export type ContentFeedbackResult = GetContentInput & {
  feedback: ContentFeedback;
};

export type SaveResult = {
  contentId: string;
  saved: boolean;
  topicContext: TopicContext;
};
