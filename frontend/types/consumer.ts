export type AuthStatus = "unauthenticated" | "authenticated";

export type SignupStatus = "new" | "existing";

export type LoginProviderOption = {
  id: string;
  label: string;
};

export type LoginRequest = {
  providerId: string;
};

export type LoginResult = {
  authStatus: "authenticated";
  providerId: string;
  signupStatus: SignupStatus;
};

export type AccountType = "consumer" | "creator";

export type SubtopicOption = {
  id: string;
  label: string;
};

export type InterpretedSubtopic = {
  id: string;
  label: string;
};

export type FinalSubtopic = {
  id: string;
  label: string;
};

export type OnboardingTopicOption = {
  id: string;
  label: string;
  subtopicOptions: SubtopicOption[];
};

export type OnboardingOptions = {
  accountTypes: AccountType[];
  topics: OnboardingTopicOption[];
};

// 추가 분야 Flow의 FE Mock 모델이며 실제 Backend DTO / ID Contract가 아니다.
export type TopicOptions = Pick<OnboardingOptions, "topics">;

export type AddMyTopicInput = {
  topicId: string;
  subtopicIds: string[];
};

export type AddMyTopicResult = {
  topicId: string;
  subtopicIds: string[];
};

export type InterpretSubtopicsInput = {
  topicId: string;
  text: string;
};

export type SubtopicInterpretationResult = {
  topicId: string;
  subtopics: InterpretedSubtopic[];
};

export type OnboardingConsent = {
  terms: boolean;
  privacy: boolean;
  advertising: boolean;
  marketing: boolean;
};

export type OnboardingData = {
  consent: OnboardingConsent;
  birthdate: string;
  accountType: AccountType;
  selectedTopicId: string;
  subtopicInput: {
    selectedSubtopics: SubtopicOption[];
    freeText: string;
  };
  interpretedSubtopics: InterpretedSubtopic[];
  finalSubtopics: FinalSubtopic[];
};

export type AgeEligibilityStatus = "eligible" | "restricted";

export type AgeEligibilityResult = {
  status: AgeEligibilityStatus;
};

export type OnboardingResult = {
  completed: boolean;
  availableTopicIds: string[];
};
