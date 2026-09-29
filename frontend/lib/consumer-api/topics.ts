import { mockOnboardingOptions } from "@/mocks/consumer";
import { addMyTopic as createMyTopic } from "@/lib/api";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { loadApiTopicOptions, positiveApiId } from "@/lib/onboarding/api-catalog";
import type {
  AddMyTopicInput,
  AddMyTopicResult,
  TopicOptions,
} from "@/types/consumer";

export async function getTopicOptions(): Promise<TopicOptions> {
  if (isConsumerApiMode) {
    return { topics: await loadApiTopicOptions() };
  }
  return structuredClone({ topics: mockOnboardingOptions.topics });
}

export async function addMyTopic(
  { topicId, subtopicIds }: AddMyTopicInput,
  // Mock 중복 검증용 정보다. 실제 API-022 Payload에는 포함하지 않는다.
  mockContext: { availableTopicIds: readonly string[] },
): Promise<AddMyTopicResult> {
  if (isConsumerApiMode) {
    const numericTopicId = positiveApiId(topicId);
    const numericSubtopicIds = subtopicIds.map(positiveApiId);
    if (!numericSubtopicIds.length || new Set(numericSubtopicIds).size !== numericSubtopicIds.length) {
      throw new Error("Unique Subtopic IDs are required.");
    }
    const response = await createMyTopic(numericTopicId, numericSubtopicIds);
    return {
      topicId: String(response.topic.id),
      subtopicIds: response.topic.subtopicIds.map(String),
    };
  }
  const topic = mockOnboardingOptions.topics.find((item) => item.id === topicId);

  if (!topic) {
    throw new Error(`Unknown Mock topic: ${topicId}`);
  }

  if (mockContext.availableTopicIds.includes(topicId)) {
    throw new Error(`Mock topic is already active: ${topicId}`);
  }

  if (!Array.isArray(subtopicIds) || subtopicIds.length === 0) {
    throw new Error("At least one subtopic is required.");
  }

  if (new Set(subtopicIds).size !== subtopicIds.length) {
    throw new Error("Duplicate subtopic IDs are not allowed.");
  }

  if (
    !subtopicIds.every((id) =>
      topic.subtopicOptions.some((option) => option.id === id),
    )
  ) {
    throw new Error("Subtopics must belong to the selected Mock topic.");
  }

  // 활성 목록은 Provider만 관리한다. 구독 Store나 개인화 Feed는 만들지 않는다.
  return { topicId: topic.id, subtopicIds: [...subtopicIds] };
}
