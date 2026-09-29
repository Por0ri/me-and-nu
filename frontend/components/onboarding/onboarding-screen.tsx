"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import {
  checkAgeEligibility,
  getOnboardingOptions,
  submitOnboarding,
} from "@/lib/consumer-api/onboarding";
import {
  formatDateInputValue,
  validateBirthdate,
} from "@/lib/onboarding/birthdate";
import { filterSubtopics } from "@/lib/subtopics/filter-subtopics";
import type {
  AccountType,
  AgeEligibilityStatus,
  FinalSubtopic,
  OnboardingConsent,
  OnboardingData,
  OnboardingOptions,
  SubtopicOption,
} from "@/types/consumer";

type OnboardingStep =
  | "consent"
  | "birthdate"
  | "age-result"
  | "account-type"
  | "topic"
  | "subtopics"
  | "confirmation";

type PendingAction = "age-check" | "submit" | null;

type SubtopicDraft = {
  selectedSubtopicIds: string[];
};

type SubtopicDraftByTopicId = Record<string, SubtopicDraft>;

type OnboardingConfirmation = {
  topicId: string;
  topicLabel: string;
  selectedSubtopics: SubtopicOption[];
  finalSubtopics: FinalSubtopic[];
};

const STEP_ORDER: OnboardingStep[] = [
  "consent",
  "birthdate",
  "age-result",
  "account-type",
  "topic",
  "subtopics",
  "confirmation",
];

const CONSENT_OPTIONS = [
  { key: "terms", label: "이용약관 동의 (필수)" },
  { key: "privacy", label: "개인정보 동의 (필수)" },
  { key: "advertising", label: "광고 수신 동의 (선택)" },
  { key: "marketing", label: "마케팅 수신 동의 (선택)" },
] as const;

const OPTIONS_ERROR_MESSAGE =
  "온보딩 선택지를 불러오지 못했습니다. 다시 시도해 주세요.";
const AGE_CHECK_ERROR_MESSAGE =
  "연령 확인에 실패했습니다. 다시 시도해 주세요.";
const SUBTOPIC_ERROR_MESSAGE =
  "선택한 분야 정보를 확인할 수 없습니다. 분야를 다시 선택해 주세요.";
const SUBMIT_ERROR_MESSAGE =
  "온보딩을 완료하지 못했습니다. 다시 시도해 주세요.";

const primaryButtonClassName =
  "va-primary w-full rounded-lg bg-black px-4 py-3 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-60";
const secondaryButtonClassName =
  "va-secondary w-full rounded-lg border border-black/20 px-4 py-3 text-sm font-medium text-black disabled:cursor-not-allowed disabled:opacity-60";

export function OnboardingScreen() {
  const router = useRouter();
  const { setFlowState } = useConsumerFlow();
  const [options, setOptions] = useState<OnboardingOptions | null>(null);
  const [isLoadingOptions, setIsLoadingOptions] = useState(true);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [currentStep, setCurrentStep] =
    useState<OnboardingStep>("consent");
  const [consent, setConsent] = useState<OnboardingConsent>({
    terms: false,
    privacy: false,
    advertising: false,
    marketing: false,
  });
  const [birthdate, setBirthdate] = useState("");
  const [ageEligibility, setAgeEligibility] =
    useState<AgeEligibilityStatus | null>(null);
  const [accountType, setAccountType] = useState<AccountType | null>(null);
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(null);
  const [subtopicDraftByTopicId, setSubtopicDraftByTopicId] =
    useState<SubtopicDraftByTopicId>({});
  const [searchQuery, setSearchQuery] = useState("");
  const [confirmation, setConfirmation] =
    useState<OnboardingConfirmation | null>(null);
  const [pendingAction, setPendingAction] =
    useState<PendingAction>(null);
  const [validationMessage, setValidationMessage] = useState<string | null>(
    null,
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const requestInFlight = useRef(false);
  const maximumBirthdate = formatDateInputValue();

  useEffect(() => {
    let isCancelled = false;

    async function loadOnboardingOptions() {
      try {
        const onboardingOptions = await getOnboardingOptions();

        if (!isCancelled) {
          setOptions(onboardingOptions);
        }
      } catch {
        if (!isCancelled) {
          setOptionsError(OPTIONS_ERROR_MESSAGE);
        }
      } finally {
        if (!isCancelled) {
          setIsLoadingOptions(false);
        }
      }
    }

    void loadOnboardingOptions();

    return () => {
      isCancelled = true;
    };
  }, []);

  async function retryOnboardingOptions() {
    setIsLoadingOptions(true);
    setOptionsError(null);

    try {
      const onboardingOptions = await getOnboardingOptions();
      setOptions(onboardingOptions);
    } catch {
      setOptions(null);
      setOptionsError(OPTIONS_ERROR_MESSAGE);
    } finally {
      setIsLoadingOptions(false);
    }
  }

  function moveToStep(step: OnboardingStep) {
    setValidationMessage(null);
    setActionError(null);
    setCurrentStep(step);
  }

  function handleConsentSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!(consent.terms && consent.privacy)) {
      setValidationMessage("이용약관과 개인정보 필수 동의가 필요합니다.");
      return;
    }

    moveToStep("birthdate");
  }

  async function handleAgeCheck(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const birthdateValidation = validateBirthdate(birthdate);

    if (!birthdateValidation.valid) {
      setActionError(null);
      setValidationMessage(
        birthdateValidation.reason === "required"
          ? "생년월일을 입력해 주세요."
          : "올바른 생년월일을 입력해 주세요.",
      );
      return;
    }

    if (requestInFlight.current) {
      return;
    }

    requestInFlight.current = true;
    setPendingAction("age-check");
    setValidationMessage(null);
    setActionError(null);

    try {
      const result = await checkAgeEligibility(birthdate);
      setAgeEligibility(result.status);
      setCurrentStep("age-result");
    } catch {
      setActionError(AGE_CHECK_ERROR_MESSAGE);
    } finally {
      requestInFlight.current = false;
      setPendingAction(null);
    }
  }

  function handleAccountTypeSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (accountType !== "consumer") {
      setValidationMessage("Consumer를 선택해 주세요.");
      return;
    }

    moveToStep("topic");
  }

  function selectTopic(topicId: string) {
    setSelectedTopicId(topicId);
    setSearchQuery("");
    setConfirmation(null);
    setSubtopicDraftByTopicId((currentDrafts) =>
      currentDrafts[topicId]
        ? currentDrafts
        : {
            ...currentDrafts,
            [topicId]: { selectedSubtopicIds: [] },
          },
    );
    setValidationMessage(null);
    setActionError(null);
  }

  function handleTopicSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!selectedTopicId) {
      setValidationMessage("분야를 선택해 주세요.");
      return;
    }

    moveToStep("subtopics");
  }

  function toggleSubtopic(subtopicId: string) {
    if (!selectedTopicId) {
      return;
    }

    setSubtopicDraftByTopicId((currentDrafts) => {
      const currentDraft = currentDrafts[selectedTopicId] ?? {
        selectedSubtopicIds: [],
      };
      const selectedSubtopicIds = currentDraft.selectedSubtopicIds.includes(
        subtopicId,
      )
        ? currentDraft.selectedSubtopicIds.filter((id) => id !== subtopicId)
        : [...currentDraft.selectedSubtopicIds, subtopicId];

      return {
        ...currentDrafts,
        [selectedTopicId]: {
          ...currentDraft,
          selectedSubtopicIds,
        },
      };
    });
    setConfirmation(null);
    setValidationMessage(null);
    setActionError(null);
  }

  function handleSubtopicSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!options || !selectedTopicId) {
      setActionError(SUBTOPIC_ERROR_MESSAGE);
      return;
    }

    const selectedTopic = options.topics.find(
      (topic) => topic.id === selectedTopicId,
    );
    const subtopicDraft = subtopicDraftByTopicId[selectedTopicId] ?? {
      selectedSubtopicIds: [],
    };
    const selectedSubtopicIdSet = new Set(
      subtopicDraft.selectedSubtopicIds,
    );
    const selectedSubtopics =
      selectedTopic?.subtopicOptions.filter((subtopic) =>
        selectedSubtopicIdSet.has(subtopic.id),
      ) ?? [];

    if (!selectedTopic) {
      setActionError(SUBTOPIC_ERROR_MESSAGE);
      return;
    }

    if (selectedSubtopics.length < 5) {
      setValidationMessage("세부 취향을 5개 이상 선택해 주세요.");
      return;
    }

    if (requestInFlight.current) {
      return;
    }

    setConfirmation({
      topicId: selectedTopic.id,
      topicLabel: selectedTopic.label,
      selectedSubtopics,
      finalSubtopics: selectedSubtopics.map(({ id, label }) => ({ id, label })),
    });
    moveToStep("confirmation");
  }

  function buildOnboardingData(): OnboardingData | null {
    if (
      !confirmation ||
      !(consent.terms && consent.privacy) ||
      ageEligibility !== "eligible" ||
      accountType !== "consumer"
    ) {
      return null;
    }

    return {
      consent: { ...consent },
      birthdate,
      accountType,
      selectedTopicId: confirmation.topicId,
      subtopicInput: {
        selectedSubtopics: confirmation.selectedSubtopics,
        freeText: "",
      },
      interpretedSubtopics: [],
      finalSubtopics: confirmation.finalSubtopics,
    };
  }

  function editSubtopics() {
    setSearchQuery("");
    setConfirmation(null);
    moveToStep("subtopics");
  }

  async function handleOnboardingSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (requestInFlight.current) {
      return;
    }

    const onboardingData = buildOnboardingData();

    if (!onboardingData) {
      setActionError(SUBMIT_ERROR_MESSAGE);
      return;
    }

    requestInFlight.current = true;
    setPendingAction("submit");
    setValidationMessage(null);
    setActionError(null);

    try {
      const result = await submitOnboarding(onboardingData);

      if (!result.completed) {
        throw new Error("Onboarding was not completed.");
      }

      if (!result.availableTopicIds.includes(onboardingData.selectedTopicId)) {
        throw new Error("Selected topic is not available after onboarding.");
      }

      setFlowState({
        initialHomeTopicId: onboardingData.selectedTopicId,
        availableTopicIds: [...result.availableTopicIds],
      });

      router.push(consumerRoutes.home);
    } catch {
      requestInFlight.current = false;
      setPendingAction(null);
      setActionError(SUBMIT_ERROR_MESSAGE);
    }
  }

  const currentStepNumber = STEP_ORDER.indexOf(currentStep) + 1;
  const consumerAvailable = options?.accountTypes.includes("consumer") ?? false;
  const selectedTopic =
    options?.topics.find((topic) => topic.id === selectedTopicId) ?? null;
  const selectedTopicDraft = selectedTopicId
    ? subtopicDraftByTopicId[selectedTopicId]
    : null;
  const filteredSubtopics = filterSubtopics(
    selectedTopic?.subtopicOptions ?? [],
    searchQuery,
  );
  const selectedSubtopics = (selectedTopic?.subtopicOptions ?? []).filter(
    (subtopic) => selectedTopicDraft?.selectedSubtopicIds.includes(subtopic.id),
  );

  if (isLoadingOptions) {
    return (
      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <p role="status" className="text-sm text-black/60">
          온보딩 선택지를 불러오는 중입니다.
        </p>
      </main>
    );
  }

  if (optionsError) {
    return (
      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <section className="w-full max-w-md space-y-4 text-center">
          <p role="alert" className="text-sm text-red-600">
            {optionsError}
          </p>
          <button
            type="button"
            onClick={() => void retryOnboardingOptions()}
            className={secondaryButtonClassName}
          >
            다시 시도
          </button>
        </section>
      </main>
    );
  }

  if (!options || !consumerAvailable || options.topics.length === 0) {
    return (
      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <p role="status" className="text-sm text-black/60">
          사용 가능한 Consumer 온보딩 항목이 없습니다.
        </p>
      </main>
    );
  }

  const isVisualStep =
    currentStep === "account-type" ||
    currentStep === "topic" ||
    currentStep === "subtopics" ||
    currentStep === "confirmation";

  return (
    <main
      className={isVisualStep ? "ui-version-a" : "flex flex-1 items-center justify-center px-6 py-12"}
    >
      <section
        aria-labelledby="onboarding-title"
        aria-busy={pendingAction !== null}
        className={
          isVisualStep
            ? "va-shell va-onboarding"
            : "w-full max-w-md space-y-6 rounded-2xl border border-black/10 bg-white p-8 shadow-sm"
        }
      >
        <header className={isVisualStep ? "va-onboarding-header" : "space-y-2"}>
          {isVisualStep ? (
            <p className="text-lg font-extrabold tracking-tight">
              me;<span className="va-accent">nu</span>
            </p>
          ) : null}
          <p className={isVisualStep ? "va-muted text-xs" : "text-sm text-black/50"}>
            단계 {currentStepNumber} / {STEP_ORDER.length}
          </p>
          <h1
            id="onboarding-title"
            className={isVisualStep ? "sr-only" : "text-2xl font-semibold text-black"}
          >
            Consumer 온보딩
          </h1>
        </header>

        {currentStep === "consent" ? (
          <form className="space-y-6" onSubmit={handleConsentSubmit}>
            <fieldset className="space-y-3">
              <legend className="text-lg font-medium text-black">약관 동의</legend>
              <p className="text-sm text-black/50">
                현재 Mock 동의 화면이며 실제 약관 본문은 제공하지 않습니다.
              </p>
              {CONSENT_OPTIONS.map(({ key, label }) => (
                <label
                  key={key}
                  className="flex items-start gap-3 rounded-lg border border-black/10 p-4 text-sm text-black/80"
                >
                  <input
                    type="checkbox"
                    checked={consent[key]}
                    onChange={(event) => {
                      const checked = event.target.checked;
                      setConsent((current) => ({ ...current, [key]: checked }));
                      setValidationMessage(null);
                    }}
                    className="mt-0.5 size-4"
                  />
                  {label}
                </label>
              ))}
            </fieldset>
            <button type="submit" className={primaryButtonClassName}>
              다음
            </button>
          </form>
        ) : null}

        {currentStep === "birthdate" ? (
          <form className="space-y-6" onSubmit={handleAgeCheck}>
            <div className="space-y-3">
              <label
                htmlFor="birthdate"
                className="block text-lg font-medium text-black"
              >
                생년월일 입력
              </label>
              <input
                id="birthdate"
                name="birthdate"
                type="date"
                required
                max={maximumBirthdate}
                disabled={pendingAction === "age-check"}
                value={birthdate}
                onChange={(event) => {
                  setBirthdate(event.target.value);
                  setAgeEligibility(null);
                  setValidationMessage(null);
                  setActionError(null);
                }}
                className="w-full rounded-lg border border-black/20 px-4 py-3 text-black"
              />
              <p className="text-sm text-black/50">
                올바른 생년월일을 입력하면 Mock 연령 확인을 진행합니다.
              </p>
            </div>
            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => moveToStep("consent")}
                disabled={pendingAction !== null}
                className={secondaryButtonClassName}
              >
                이전
              </button>
              <button
                type="submit"
                disabled={pendingAction !== null}
                className={primaryButtonClassName}
              >
                {pendingAction === "age-check" ? "확인 중..." : "연령 확인"}
              </button>
            </div>
          </form>
        ) : null}

        {currentStep === "age-result" && ageEligibility ? (
          <div className="space-y-6">
            <div className="space-y-2">
              <h2 className="text-lg font-medium text-black">Mock 연령 확인 결과</h2>
              <p className="text-sm text-black/70">
                {ageEligibility === "eligible"
                  ? "만 14세 이상으로 다음 단계로 진행할 수 있습니다."
                  : "만 14세 미만으로 현재 가입을 계속할 수 없습니다."}
              </p>
            </div>
            {ageEligibility === "eligible" ? (
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => moveToStep("birthdate")}
                  className={secondaryButtonClassName}
                >
                  이전
                </button>
                <button
                  type="button"
                  onClick={() => moveToStep("account-type")}
                  className={primaryButtonClassName}
                >
                  다음
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => moveToStep("birthdate")}
                className={secondaryButtonClassName}
              >
                생년월일 다시 입력
              </button>
            )}
          </div>
        ) : null}

        {currentStep === "account-type" ? (
          <form
            className="va-onboarding-form va-start-choice"
            onSubmit={handleAccountTypeSubmit}
          >
            <fieldset className="min-w-0">
              <legend className="sr-only">가입 유형 확인</legend>
              <div className="va-step-intro">
                <h2 className="va-step-title">어떻게 시작할까요?</h2>
                <p className="va-muted mt-3 text-sm leading-6">
                  관심사별 매거진을 만나보세요.
                </p>
              </div>
              <label className="va-consumer-choice">
                <input
                  type="radio"
                  name="accountType"
                  value="consumer"
                  checked={accountType === "consumer"}
                  onChange={() => {
                    setAccountType("consumer");
                    setValidationMessage(null);
                  }}
                  className="size-4 shrink-0 accent-[#252522]"
                />
                <span className="space-y-2">
                  <span className="block text-2xl font-bold">콘텐츠 소비자</span>
                  <span className="block text-base">관심사별 매거진 읽기</span>
                  <span className="sr-only">Consumer</span>
                </span>
              </label>
            </fieldset>
            <div className="va-actions">
              <button
                type="button"
                onClick={() => moveToStep("age-result")}
                className={secondaryButtonClassName}
              >
                이전
              </button>
              <button type="submit" className={primaryButtonClassName}>
                다음
              </button>
            </div>
          </form>
        ) : null}

        {currentStep === "topic" ? (
          <form className="va-onboarding-form" onSubmit={handleTopicSubmit}>
            <fieldset className="min-w-0">
              <legend className="sr-only">분야 선택</legend>
              <div className="va-step-intro">
                <h2 className="va-step-title">
                  탐색할 분야를<br />하나만 골라주세요.
                </h2>
                <p className="va-muted mt-3 text-sm leading-6">
                  다른 분야는 나중에 언제든 추가할 수 있어요.
                </p>
              </div>
              {options.topics.map((topic, index) => (
                <label
                  key={topic.id}
                  className="va-topic-choice"
                >
                  <input
                    type="radio"
                    name="topic"
                    value={topic.id}
                    checked={selectedTopicId === topic.id}
                    onChange={() => selectTopic(topic.id)}
                    className="sr-only"
                  />
                  <span aria-hidden="true" className="va-accent text-sm tabular-nums">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="flex-1 text-xl font-medium">{topic.label}</span>
                  <span aria-hidden="true" className="va-choice-indicator" />
                </label>
              ))}
            </fieldset>
            <div className="va-actions">
              <button
                type="button"
                onClick={() => moveToStep("account-type")}
                className={secondaryButtonClassName}
              >
                이전
              </button>
              <button type="submit" className={primaryButtonClassName}>
                다음
              </button>
            </div>
          </form>
        ) : null}

        {currentStep === "subtopics" && selectedTopic ? (
          <form className="va-onboarding-form" onSubmit={handleSubtopicSubmit}>
            <div className="space-y-8">
              <div className="va-step-intro">
                <h2 className="va-step-title">
                  {selectedTopic.label}에서는 무엇에<br />관심있나요?
                </h2>
                <p className="va-muted mt-3 text-sm leading-6">
                  기존 취향 Chip을 검색하고 5개 이상 선택해 주세요.
                </p>
              </div>

              <div className="space-y-2">
                <label
                  htmlFor="onboarding-subtopic-search"
                  className="block text-sm font-medium"
                >
                  취향 검색
                </label>
                <input
                  id="onboarding-subtopic-search"
                  type="search"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                    }
                  }}
                  placeholder="기존 취향 Chip 검색"
                  className="w-full rounded-lg border border-black/20 px-4 py-3 text-base"
                />
              </div>

              <fieldset className="min-w-0">
                <legend className="mb-3 text-sm font-medium">검색 결과</legend>
                <div className="flex flex-wrap justify-center gap-2">
                  {filteredSubtopics.length > 0 ? (
                    filteredSubtopics.map((subtopic) => (
                      <label
                        key={subtopic.id}
                        className="va-subtopic-chip"
                      >
                        <input
                          type="checkbox"
                          checked={
                            selectedTopicDraft?.selectedSubtopicIds.includes(
                              subtopic.id,
                            ) ?? false
                          }
                          onChange={() => toggleSubtopic(subtopic.id)}
                          className="sr-only"
                        />
                        {subtopic.label}
                      </label>
                    ))
                  ) : (
                    <p role="status" className="va-muted text-sm">
                      {selectedTopic.subtopicOptions.length === 0
                        ? "선택 가능한 취향 Chip이 없습니다."
                        : "검색 결과가 없어요. 다른 키워드로 검색해 주세요."}
                    </p>
                  )}
                </div>
              </fieldset>

              <fieldset className="min-w-0">
                <legend className="mb-3 text-sm font-medium">현재 선택된 취향 ({selectedSubtopics.length}개)</legend>
                {selectedSubtopics.length > 0 ? (
                  <div className="flex flex-wrap justify-center gap-2">
                    {selectedSubtopics.map((subtopic) => (
                      <button
                        key={subtopic.id}
                        type="button"
                        onClick={() => toggleSubtopic(subtopic.id)}
                        aria-label={`${subtopic.label} 선택 해제`}
                        className="va-confirmed-chip"
                      >
                        {subtopic.label} <span aria-hidden="true">×</span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="va-muted text-sm">
                    선택한 취향이 없습니다. 5개 이상 선택해 주세요.
                  </p>
                )}
              </fieldset>
            </div>
            <div className="va-actions">
              <button
                type="button"
                onClick={() => moveToStep("topic")}
                disabled={pendingAction !== null}
                className={secondaryButtonClassName}
              >
                이전
              </button>
              <button
                type="submit"
                disabled={pendingAction !== null}
                className={primaryButtonClassName}
              >
                취향 확인
              </button>
            </div>
          </form>
        ) : null}

        {currentStep === "confirmation" && confirmation ? (
          <form className="va-onboarding-form" onSubmit={handleOnboardingSubmit}>
            <div className="space-y-8">
              <div className="va-step-intro">
                <h2 className="va-step-title">
                  선택한 취향을 확인해 주세요.
                </h2>
                <p className="va-muted mt-3 text-base">
                  이 주제로 분야를 만들까요?
                </p>
              </div>
              <div className="va-confirmation space-y-4">
                <div className="space-y-1">
                  <p className="va-muted text-xs">분야</p>
                  <p className="font-semibold">{confirmation.topicLabel}</p>
                </div>
                <p className="sr-only">선택한 취향</p>
                <ul className="flex flex-wrap justify-center gap-2">
                  {confirmation.finalSubtopics.map((subtopic) => (
                    <li
                      key={subtopic.id}
                      className="va-confirmed-chip"
                    >
                      #{subtopic.label}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="va-actions va-confirmation-actions">
              <button
                type="submit"
                disabled={pendingAction !== null}
                className={primaryButtonClassName}
              >
                {pendingAction === "submit" ? "만드는 중..." : "분야 만들기"}
              </button>
              <button
                type="button"
                onClick={editSubtopics}
                disabled={pendingAction !== null}
                className={secondaryButtonClassName}
              >
                수정하기
              </button>
            </div>
          </form>
        ) : null}

        {validationMessage ? (
          <p role="alert" className="text-sm text-red-600">
            {validationMessage}
          </p>
        ) : null}

        {actionError ? (
          <p role="alert" className="text-sm text-red-600">
            {actionError}
          </p>
        ) : null}
      </section>
    </main>
  );
}
