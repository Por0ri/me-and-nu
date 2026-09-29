import {
  mockContentAIResponsesByTopicId,
  mockHomeAIAnswersByTopicId,
  mockHomeAIQuestionTargets,
} from "@/mocks/ai";
import { mockContentsByTopicId } from "@/mocks/contents";
import { mockTopics } from "@/mocks/topics";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import type {
  ContentAIRequest,
  ContentAIResponse,
  HomeAIRequest,
  HomeAIResponse,
} from "@/types/ai";

export async function sendContentAIQuestion({
  contentId,
  question,
  topicContext,
}: ContentAIRequest): Promise<ContentAIResponse> {
  if (isConsumerApiMode) {
    throw new Error("Content AI questions are not connected to the local API.");
  }
  const trimmedQuestion = question.trim();

  if (!trimmedQuestion) {
    throw new Error("A Content AI question is required.");
  }

  const topicId = topicContext.topicId;

  if (!mockTopics.some((topic) => topic.id === topicId)) {
    throw new Error(`Unknown Mock topic: ${topicId}`);
  }

  if (
    !mockContentsByTopicId[topicId]?.some((content) => content.id === contentId)
  ) {
    throw new Error(`Unknown Mock content in ${topicId}: ${contentId}`);
  }

  // 질문 내용 분석이나 저장 없이 고정 Fixture를 반환한다.
  // sourceTopicId는 참고 출처의 분야이므로 요청 Context 검증에 사용하지 않는다.
  const response =
    mockContentAIResponsesByTopicId[topicId]?.[contentId];

  if (!response) {
    throw new Error(
      `Unknown Mock AI context: ${topicContext.topicId}/${contentId}`,
    );
  }

  return structuredClone(response);
}

// Home 전용 Mock facade. 실제 연동의 호출 흐름과 DTO 변환은 이 경계 안에서 처리한다.
export async function sendHomeAIQuestion({
  topicContext,
  question,
}: HomeAIRequest): Promise<HomeAIResponse> {
  if (isConsumerApiMode) {
    throw new Error("Home AI questions are not connected to the local API.");
  }
  const trimmedQuestion = question.trim();

  if (!trimmedQuestion) {
    throw new Error("A Home AI question is required.");
  }

  const topicId = topicContext.topicId;

  if (!mockTopics.some((topic) => topic.id === topicId)) {
    throw new Error(`Unknown Mock topic: ${topicId}`);
  }

  const questionTarget = mockHomeAIQuestionTargets.find(
    (fixture) => fixture.question === trimmedQuestion,
  );

  if (questionTarget && questionTarget.topicId !== topicId) {
    const targetTopic = mockTopics.find(
      (topic) => topic.id === questionTarget.topicId,
    );

    if (!targetTopic) {
      throw new Error(`Unknown Mock target topic: ${questionTarget.topicId}`);
    }

    return {
      type: "topicSwitchSuggested",
      targetTopic: { ...targetTopic },
      message: `이 질문은 ${targetTopic.name} 분야에서 다루는 질문이에요. ${targetTopic.name} 분야에서 질문해 주세요.`,
    };
  }

  const answer = mockHomeAIAnswersByTopicId[topicId];

  if (!answer) {
    throw new Error(`Unknown Mock Home AI context: ${topicId}`);
  }

  return { type: "answer", answer };
}
