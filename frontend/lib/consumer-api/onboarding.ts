import {
  mockInterpretedSubtopicsByTopicId,
  mockOnboardingOptions,
} from "@/mocks/consumer";
import { apiBaseUrl, getPolicies, onboard } from "@/lib/api";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { loadApiTopicOptions, positiveApiId } from "@/lib/onboarding/api-catalog";
import { getAgeEligibility, validateBirthdate } from "@/lib/onboarding/birthdate";
import type {
  AgeEligibilityResult,
  InterpretSubtopicsInput,
  OnboardingData,
  OnboardingOptions,
  OnboardingResult,
  SubtopicInterpretationResult,
} from "@/types/consumer";

/** Temporary local test profile. Replace with verified SNS/profile input later. */
export function createLocalOnboardingProfile() {
  const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
  if (
    typeof window === "undefined" ||
    !loopbackHosts.has(window.location.hostname) ||
    !loopbackHosts.has(new URL(apiBaseUrl).hostname)
  ) {
    throw new Error("Automatic onboarding profiles are only available for local testing.");
  }
  // Pending sessions intentionally omit user details until onboarding completes.
  return { nickname: `로컬사용자${window.crypto.randomUUID().slice(0, 8)}`, birthdate: "2000-01-01" };
}

export async function getOnboardingOptions(): Promise<OnboardingOptions> {
  if (isConsumerApiMode) {
    const [policies, topics] = await Promise.all([getPolicies(), loadApiTopicOptions()]);
    if (policies.consentItems.length === 0) {
      throw new Error("Current consent policies are unavailable.");
    }
    return {
      accountTypes: ["consumer"],
      topics,
      policyItems: policies.consentItems.map(({ type, policyVersion, required, text }) => ({
        type, policyVersion, required, text,
      })),
    };
  }
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
  if (isConsumerApiMode) {
    throw new Error("Natural-language subtopic interpretation is not connected.");
  }
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
  if (isConsumerApiMode) {
    const nickname = data.nickname?.trim();
    if (!nickname || nickname.length > 30) {
      throw new Error("A nickname of 1 to 30 characters is required.");
    }
    if (!validateBirthdate(data.birthdate).valid || getAgeEligibility(data.birthdate) !== "eligible") {
      throw new Error("A valid age-eligible birth date is required.");
    }
    if (data.accountType !== "consumer") {
      throw new Error("Consumer onboarding requires a consumer account.");
    }
    const policies = data.policyItems;
    if (!policies?.length || new Set(policies.map((item) => item.type)).size !== policies.length) {
      throw new Error("Current policy versions are required for onboarding.");
    }
    const consents = policies.map((policy) => {
      const agreed = data.consent[policy.type] === true;
      if (policy.required && !agreed) {
        throw new Error(`Required policy ${policy.type} was not accepted.`);
      }
      return { type: policy.type, policyVersion: policy.policyVersion, agreed };
    });
    const topicId = positiveApiId(data.selectedTopicId);
    const subtopicIds = data.finalSubtopics.map((subtopic) => positiveApiId(subtopic.id));
    if (!subtopicIds.length || new Set(subtopicIds).size !== subtopicIds.length) {
      throw new Error("Unique Subtopic IDs are required.");
    }
    const response = await onboard({
      nickname,
      birthDate: data.birthdate,
      accountType: "consumer",
      consents,
      topicId,
      subtopicIds,
    });
    if (!response.onboardingCompleted || response.activeTopic?.id !== topicId) {
      throw new Error("The selected Topic was not activated.");
    }
    return { completed: true, availableTopicIds: [data.selectedTopicId] };
  }
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
