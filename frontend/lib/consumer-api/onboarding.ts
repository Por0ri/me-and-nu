import {
  mockInterpretedSubtopicsByTopicId,
  mockOnboardingOptions,
} from "@/mocks/consumer";
import { getAgeEligibility } from "@/lib/onboarding/birthdate";
import type {
  AgeEligibilityResult,
  InterpretSubtopicsInput,
  OnboardingData,
  OnboardingOptions,
  OnboardingResult,
  SubtopicInterpretationResult,
} from "@/types/consumer";

export async function getOnboardingOptions(): Promise<OnboardingOptions> {
  return structuredClone(mockOnboardingOptions);
}

export async function checkAgeEligibility(
  birthdate: string,
): Promise<AgeEligibilityResult> {
  return {
    status: getAgeEligibility(birthdate),
  };
}

export async function interpretOnboardingSubtopics({
  topicId,
  text,
}: InterpretSubtopicsInput): Promise<SubtopicInterpretationResult> {
  if (!text.trim()) {
    throw new Error("Subtopic text is required for interpretation.");
  }

  const subtopics = mockInterpretedSubtopicsByTopicId[topicId];

  if (!subtopics) {
    throw new Error(`Unknown onboarding topic: ${topicId}`);
  }

  return {
    topicId,
    subtopics: structuredClone(subtopics),
  };
}

export async function submitOnboarding(
  data: OnboardingData,
): Promise<OnboardingResult> {
  const topicExists = mockOnboardingOptions.topics.some(
    (topic) => topic.id === data.selectedTopicId,
  );

  if (!topicExists) {
    throw new Error(`Unknown onboarding topic: ${data.selectedTopicId}`);
  }

  // Mock 단계에서는 Topic이나 Subtopic을 영속 저장하지 않는다.
  return {
    completed: true,
    availableTopicIds: [data.selectedTopicId],
  };
}
