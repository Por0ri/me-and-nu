"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import {
  OnboardingBack,
  OnboardingBackdrop,
  OnboardingBrand,
  OnboardingIcon,
} from "@/components/onboarding/onboarding-ui";
import { ANIME_TOPIC_ID, MOVIE_TOPIC_ID, MUSIC_TOPIC_ID } from "@/mocks/topics";
import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import { useConsumerSession } from "@/components/providers/consumer-session-provider";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import {
  createLocalOnboardingProfile,
  getOnboardingOptions,
  submitOnboarding,
} from "@/lib/consumer-api/onboarding";
import { filterSubtopics } from "@/lib/subtopics/filter-subtopics";
import { loadApiTopicOptions } from "@/lib/onboarding/api-catalog";
import type {
  AccountType,
  FinalSubtopic,
  OnboardingConsent,
  OnboardingData,
  OnboardingOptions,
  SubtopicOption,
} from "@/types/consumer";

type OnboardingStep =
  | "consent"
  | "account-type"
  | "topic"
  | "subtopics"
  | "confirmation";

type PendingAction = "submit" | null;
type StartChoicePhase = "idle" | "animating" | "helper";

// FE 1380:16324 -> 1380:15963 -> 1380:15965.
const START_CHOICE_ANIMATION_MS = 200;
const START_CHOICE_HELPER_MS = 300;

type SubtopicDraft = {
  selectedSubtopicIds: string[];
};

type SubtopicDraftByTopicId = Record<string, SubtopicDraft>;

type OnboardingConfirmation = {
  topicId: string;
  topicLabel: string;
  // Keep the review chips mounted when their Figma variant becomes unselected.
  reviewSubtopics: SubtopicOption[];
  selectedSubtopics: SubtopicOption[];
  finalSubtopics: FinalSubtopic[];
};

const CONSENT_OPTIONS = [
  { key: "terms", label: "서비스 설명 및 이용약관", required: true },
  { key: "privacy", label: "개인정보 수집 및 이용동의", required: true },
  { key: "advertising", label: "광고 수신 동의", required: false },
  { key: "marketing", label: "마케팅 수신 동의", required: false },
] as const;

const POLICY_LABELS: Record<string, string> = Object.fromEntries(
  CONSENT_OPTIONS.map(({ key, label }) => [key, label]),
);
const MINIMUM_SUBTOPICS = isConsumerApiMode ? 1 : 5;
const MAX_VISIBLE_SUBTOPICS = 7;

// Presentation order only; the existing taxonomy and Provider IDs are unchanged.
const TOPIC_ORDER = [ANIME_TOPIC_ID, MOVIE_TOPIC_ID, MUSIC_TOPIC_ID];
const API_TOPIC_ORDER = ["anime", "movie", "music"];

const OPTIONS_ERROR_MESSAGE =
  "온보딩 선택지를 불러오지 못했습니다. 다시 시도해 주세요.";
const SUBTOPIC_ERROR_MESSAGE =
  "선택한 분야 정보를 확인할 수 없습니다. 분야를 다시 선택해 주세요.";
const SUBMIT_ERROR_MESSAGE =
  "온보딩을 완료하지 못했습니다. 다시 시도해 주세요.";

const secondaryButtonClassName =
  "va-secondary w-full rounded-lg border border-black/20 px-4 py-3 text-sm font-medium text-black disabled:cursor-not-allowed disabled:opacity-60";

export function OnboardingScreen() {
  const router = useRouter();
  const { setFlowState } = useConsumerFlow();
  const { apiSession, isRestoring, refreshSession } = useConsumerSession();
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
  const [accountType, setAccountType] = useState<AccountType | null>(null);
  const [startChoicePhase, setStartChoicePhase] = useState<StartChoicePhase>("idle");
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
  const [onboardingSaved, setOnboardingSaved] = useState(false);
  const savedOnboarding = useRef<{ selectedTopicId: string; availableTopicIds: string[] } | null>(null);
  const localProfile = useRef<ReturnType<typeof createLocalOnboardingProfile> | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const previousStep = useRef(currentStep);

  useEffect(() => {
    if (isConsumerApiMode && !isRestoring && apiSession?.sessionState === "active" && !requestInFlight.current) {
      router.replace(consumerRoutes.home);
    }
  }, [apiSession, isRestoring, router]);

  useEffect(() => {
    if (previousStep.current !== currentStep) {
      titleRef.current?.focus({ preventScroll: true });
      window.scrollTo(0, 0);
      previousStep.current = currentStep;
    }
  }, [currentStep]);

  useEffect(() => {
    if (currentStep !== "account-type" || startChoicePhase === "idle" || pendingAction !== null || onboardingSaved) return;

    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (cancelled || savedOnboarding.current || requestInFlight.current) return;
      if (startChoicePhase === "animating") {
        setStartChoicePhase("helper");
      } else {
        setStartChoicePhase("idle");
        setCurrentStep("topic");
      }
    }, startChoicePhase === "animating" ? START_CHOICE_ANIMATION_MS : START_CHOICE_HELPER_MS);

    // Leaving the document/history entry must not complete a stale transition.
    const cancelTransition = () => {
      cancelled = true;
      window.clearTimeout(timer);
      setStartChoicePhase("idle");
    };
    window.addEventListener("pagehide", cancelTransition);
    window.addEventListener("popstate", cancelTransition);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      window.removeEventListener("pagehide", cancelTransition);
      window.removeEventListener("popstate", cancelTransition);
    };
  }, [currentStep, startChoicePhase, pendingAction, onboardingSaved]);

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

  useEffect(() => {
    if (!isConsumerApiMode || currentStep !== "topic") return;
    let isCancelled = false;
    setIsLoadingOptions(true);
    setOptionsError(null);

    void loadApiTopicOptions()
      .then((topics) => {
        if (!isCancelled) {
          setOptions((current) => current ? { ...current, topics } : current);
        }
      })
      .catch(() => {
        if (!isCancelled) setOptionsError(OPTIONS_ERROR_MESSAGE);
      })
      .finally(() => {
        if (!isCancelled) setIsLoadingOptions(false);
      });

    return () => { isCancelled = true; };
  }, [currentStep]);

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
    if (savedOnboarding.current) return;
    setStartChoicePhase("idle");
    setValidationMessage(null);
    setActionError(null);
    setCurrentStep(step);
  }

  function selectConsumer() {
    if (currentStep !== "account-type" || startChoicePhase !== "idle" || pendingAction !== null || savedOnboarding.current) return;
    setAccountType("consumer");
    setValidationMessage(null);
    setActionError(null);
    setStartChoicePhase("animating");
  }

  function handleConsentSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!requiredConsented) {
      setValidationMessage("필수 약관에 동의해 주세요.");
      return;
    }

    moveToStep("account-type");
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
    moveToStep("subtopics");
  }

  function toggleSubtopic(subtopicId: string) {
    if (!selectedTopicId || savedOnboarding.current || pendingAction !== null) {
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
    setConfirmation((current) => {
      if (currentStep !== "confirmation" || !current) return null;

      const selectedIds = new Set(current.selectedSubtopics.map(({ id }) => id));
      if (selectedIds.has(subtopicId)) selectedIds.delete(subtopicId);
      else selectedIds.add(subtopicId);
      const selectedSubtopics = current.reviewSubtopics.filter(({ id }) => selectedIds.has(id));
      return {
        ...current,
        selectedSubtopics,
        finalSubtopics: selectedSubtopics.map(({ id, label }) => ({ id, label })),
      };
    });
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

    if (selectedSubtopics.length < MINIMUM_SUBTOPICS) {
      setValidationMessage(`세부 토픽을 ${MINIMUM_SUBTOPICS}개 이상 선택해 주세요.`);
      return;
    }

    if (requestInFlight.current) {
      return;
    }

    setConfirmation({
      topicId: selectedTopic.id,
      topicLabel: selectedTopic.label,
      reviewSubtopics: selectedSubtopics,
      selectedSubtopics,
      finalSubtopics: selectedSubtopics.map(({ id, label }) => ({ id, label })),
    });
    moveToStep("confirmation");
  }

  function buildOnboardingData(): OnboardingData | null {
    if (
      !confirmation ||
      !requiredConsented ||
      accountType !== "consumer"
    ) {
      return null;
    }

    if (isConsumerApiMode && !localProfile.current) {
      localProfile.current = createLocalOnboardingProfile();
    }

    return {
      ...(localProfile.current ?? { birthdate: "" }),
      policyItems: options?.policyItems,
      consent: { ...consent },
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
    if (savedOnboarding.current) return;
    setSearchQuery("");
    setConfirmation(null);
    moveToStep("subtopics");
  }

  async function handleOnboardingSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!savedOnboarding.current && (!confirmation || confirmation.finalSubtopics.length < MINIMUM_SUBTOPICS)) {
      setValidationMessage(`세부 토픽을 ${MINIMUM_SUBTOPICS}개 이상 선택해 주세요.`);
      return;
    }

    if (requestInFlight.current) {
      return;
    }

    requestInFlight.current = true;
    setPendingAction("submit");
    setValidationMessage(null);
    setActionError(null);

    try {
      if (!savedOnboarding.current) {
        const onboardingData = buildOnboardingData();
        if (!onboardingData) throw new Error("Onboarding details are incomplete.");
        const result = await submitOnboarding(onboardingData);
        if (!result.completed || !result.availableTopicIds.includes(onboardingData.selectedTopicId)) {
          throw new Error("Selected topic is not available after onboarding.");
        }
        savedOnboarding.current = {
          selectedTopicId: onboardingData.selectedTopicId,
          availableTopicIds: [...result.availableTopicIds],
        };
        setOnboardingSaved(true);
      }

      setFlowState({
        initialHomeTopicId: savedOnboarding.current.selectedTopicId,
        availableTopicIds: [...savedOnboarding.current.availableTopicIds],
      });
      if (isConsumerApiMode) {
        await refreshSession();
        router.replace(consumerRoutes.home);
      } else {
        router.push(consumerRoutes.home);
      }
    } catch (error) {
      if (process.env.NODE_ENV === "development") {
        console.warn("Onboarding submission failed:", error instanceof Error ? error.message : "Unknown error");
      }
      requestInFlight.current = false;
      setPendingAction(null);
      setActionError(savedOnboarding.current
        ? "온보딩은 완료되었습니다. 홈 연결을 다시 시도해 주세요."
        : SUBMIT_ERROR_MESSAGE);
    }
  }

  const consentOptions = isConsumerApiMode
    ? (options?.policyItems ?? []).map((policy) => ({
        key: policy.type,
        label: POLICY_LABELS[policy.type] ?? policy.type,
        required: policy.required,
        text: policy.text ?? "",
      }))
    : CONSENT_OPTIONS.map((option) => ({ ...option, text: "현재 Mock 동의 화면이며 실제 약관 본문은 제공하지 않습니다." }));
  const allConsented = consentOptions.length > 0 && consentOptions.every(({ key }) => consent[key]);
  const requiredConsented = consentOptions.length > 0 && consentOptions.every(({ key, required }) => !required || consent[key]);
  const topicOptions = isConsumerApiMode
    ? [...(options?.topics ?? [])].sort((a, b) => {
        const rank = (code?: string) => {
          const index = API_TOPIC_ORDER.indexOf(code ?? "");
          return index < 0 ? API_TOPIC_ORDER.length : index;
        };
        return rank(a.code) - rank(b.code);
      })
    : TOPIC_ORDER.flatMap((id) => options?.topics.filter((topic) => topic.id === id) ?? []);
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
  // Search the entire catalog before limiting the existing chip layout.
  const visibleSubtopics = filteredSubtopics.slice(0, MAX_VISIBLE_SUBTOPICS);
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

  return (
    <main className="ui-version-a ui-onboarding">
      <section className={`va-shell ob-screen ob-${currentStep}`} aria-labelledby="onboarding-title" aria-busy={pendingAction !== null || startChoicePhase !== "idle"}>
        {currentStep === "consent" ? (
          <form onSubmit={handleConsentSubmit} className="ob-entry ob-consent-form">
            <OnboardingBackdrop />
            <OnboardingBrand>
              <h1 id="onboarding-title" ref={titleRef} tabIndex={-1}>시작하기 전에 확인해주세요</h1>
              <p className="ob-brand-description">서비스 이용에 필요한 항목입니다</p>
            </OnboardingBrand>
            <fieldset className="ob-consents">
              <legend className="sr-only">약관 동의</legend>
              <label className="ob-consent-all">
                <input type="checkbox" className="ob-checkbox" checked={allConsented} onChange={(event) => {
                  const checked = event.target.checked;
                  setConsent((current) => ({ ...current, ...Object.fromEntries(consentOptions.map(({ key }) => [key, checked])) }));
                  setValidationMessage(null);
                }} />
                <span>모두 동의합니다</span>
              </label>
              {consentOptions.map(({ key, label, required, text }) => (
                <div className="ob-consent-row" key={key}>
                  <label>
                    <input type="checkbox" className="ob-checkbox" checked={consent[key] ?? false} onChange={(event) => {
                      const checked = event.target.checked;
                      setConsent((current) => ({ ...current, [key]: checked }));
                      setValidationMessage(null);
                    }} />
                    <span>{label}</span>
                    <span className="ob-consent-kind">{required ? "필수" : "선택"}</span>
                  </label>
                  <details className="ob-consent-details">
                    <summary aria-label={`${label} 안내`}><OnboardingIcon name="chevron-down" /></summary>
                    <p className="whitespace-pre-wrap">{text}</p>
                  </details>
                </div>
              ))}
            </fieldset>
            <div className="ob-consent-action">
              <button type="submit" className="ob-primary" disabled={!requiredConsented}>시작하기</button>
            </div>
          </form>
        ) : currentStep === "account-type" && startChoicePhase === "helper" ? (
          <div className="ob-choice-helper">
            <div className="ob-choice-helper-art" aria-hidden="true"><OnboardingIcon name="topic-background" /></div>
            <h1 id="onboarding-title" ref={titleRef} tabIndex={-1} className="sr-only">탐색할 분야를 준비하고 있어요</h1>
          </div>
        ) : currentStep === "account-type" ? (
          <div className="ob-start-choice" data-transition={startChoicePhase}>
            <div className="ob-choice-art" aria-hidden="true">
              <div className="ob-choice-top"><OnboardingIcon name="choice-top" /></div>
              <div className="ob-choice-bottom"><OnboardingIcon name="choice-bottom" /></div>
            </div>
            <div className="ob-choice-intro">
              <h1 id="onboarding-title" ref={titleRef} tabIndex={-1}>어떻게 시작할까요?</h1>
              <p>선택한 유형에 맞는 시작 흐름으로 이동합니다</p>
            </div>
            <button className="ob-consumer-choice" type="button" disabled={startChoicePhase !== "idle" || pendingAction !== null || onboardingSaved} onClick={selectConsumer}>
              <span className="ob-choice-heading">콘텐츠 소비자</span>
              <span className="ob-choice-description">관심사별 매거진 읽기</span>
              <OnboardingIcon name="choice-chevron" />
            </button>
            <button className="ob-creator-choice" type="button" disabled aria-label="콘텐츠 크리에이터, 관심사별 매거진 생성하기 (준비 중)">
              <span className="ob-choice-heading">콘텐츠<br />크리에이터</span>
              <span className="ob-choice-description">관심사별 매거진 생성하기</span>
              <OnboardingIcon name="choice-chevron" />
            </button>
          </div>
        ) : (
          <>
            {currentStep === "topic" && <div className="ob-topic-art" aria-hidden="true"><OnboardingIcon name="topic-background" /></div>}
            <OnboardingBack disabled={pendingAction !== null || onboardingSaved} onBack={() => currentStep === "confirmation" ? editSubtopics() : moveToStep(currentStep === "topic" ? "account-type" : "topic")} />
            {currentStep === "topic" ? (
              <div className="ob-topic-content">
                <div className="ob-intro">
                  <h1 id="onboarding-title" ref={titleRef} tabIndex={-1}>탐색할 분야를<br />하나만 골라주세요</h1>
                  <p>다른 분야는 나중에 언제든 추가할 수 있어요</p>
                </div>
                <div className="ob-topic-list">
                  {topicOptions.map((topic) => (
                    <button type="button" className="ob-topic-button" key={topic.id} onClick={() => selectTopic(topic.id)}>
                      <span>{topic.label}</span><OnboardingIcon name="topic-arrow" />
                    </button>
                  ))}
                </div>
              </div>
            ) : currentStep === "subtopics" && selectedTopic ? (
              <form className="ob-subtopic-form" onSubmit={handleSubtopicSubmit}>
                <div className="ob-intro">
                  <h1 id="onboarding-title" ref={titleRef} tabIndex={-1}>탐색하고 싶은 세부 토픽을<br />{MINIMUM_SUBTOPICS}개 이상 선택하세요.</h1>
                </div>
                <div className="ob-search-group" data-has-selection={selectedSubtopics.length > 0}>
                  <div className="ob-search">
                    <input ref={searchInputRef} type="search" aria-label={`${selectedTopic.label} 세부 토픽 검색`} placeholder="장르, 스토리, 배경, 분위기로 검색하세요." value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }} />
                    <button type="button" aria-label="세부 토픽 검색" onClick={() => searchInputRef.current?.focus()}><span className="ob-search-icon"><OnboardingIcon name="search-arrow" /></span></button>
                  </div>
                  {selectedSubtopics.length > 0 && (
                    <ul className="ob-selected-chips" aria-label="선택한 세부 토픽" tabIndex={0}>
                      {selectedSubtopics.map((subtopic) => (
                        <li className="ob-confirmed-chip" key={subtopic.id}>
                          {subtopic.label}
                          <button type="button" aria-label={`${subtopic.label} 선택 해제`} onClick={() => toggleSubtopic(subtopic.id)}><OnboardingIcon name="chip-close" /></button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <fieldset className="ob-subtopic-options">
                  <legend className="sr-only">{selectedTopic.label} 세부 토픽</legend>
                  <div className="ob-chips">
                    {visibleSubtopics.map((subtopic) => (
                      <label className="ob-chip" key={subtopic.id}>
                        <input className="sr-only" type="checkbox" checked={selectedTopicDraft?.selectedSubtopicIds.includes(subtopic.id) ?? false} onChange={() => toggleSubtopic(subtopic.id)} />
                        {subtopic.label}
                        {selectedTopicDraft?.selectedSubtopicIds.includes(subtopic.id) && <OnboardingIcon name="chip-close" />}
                      </label>
                    ))}
                  </div>
                  {filteredSubtopics.length === 0 && <p role="status" className="ob-empty">검색 결과가 없어요. 다른 키워드로 검색해 주세요.</p>}
                </fieldset>
                {/* FE 1380:16028 has copy/layout only; no prototype action is defined. */}
                <p className="ob-more">더보기</p>
                <p className="sr-only" role="status">{selectedSubtopics.length}개 선택 / 최소 {MINIMUM_SUBTOPICS}개</p>
                <button type="submit" className="ob-primary" disabled={selectedSubtopics.length < MINIMUM_SUBTOPICS}>취향 매거진 보러가기</button>
              </form>
            ) : currentStep === "confirmation" && confirmation ? (
              <form className="ob-confirmation-form" onSubmit={handleOnboardingSubmit}>
                <div className="ob-intro">
                  <h1 id="onboarding-title" ref={titleRef} tabIndex={-1}>이렇게 이해했어요.</h1>
                  <p>이 토픽으로 매거진을 보여드릴게요.</p>
                </div>
                <div className="ob-confirmation-card">
                  <h2>{confirmation.topicId === MOVIE_TOPIC_ID || selectedTopic?.code === "movie" ? "MOVIE" : confirmation.topicLabel}</h2>
                  <ul className="ob-chips" aria-label="세부 토픽 선택 확인">
                    {confirmation.reviewSubtopics.map((subtopic) => {
                      const selected = confirmation.selectedSubtopics.some(({ id }) => id === subtopic.id);
                      return (
                        <li key={subtopic.id} className={selected ? "ob-confirmed-chip" : "ob-review-option"}>
                          {selected ? <>
                            {subtopic.label}
                            <button type="button" disabled={pendingAction !== null || onboardingSaved} aria-label={`${subtopic.label} 선택 해제`} onClick={() => toggleSubtopic(subtopic.id)}><OnboardingIcon name="chip-close" /></button>
                          </> : <button type="button" className="ob-chip" disabled={pendingAction !== null || onboardingSaved} aria-label={`${subtopic.label} 다시 선택`} onClick={() => toggleSubtopic(subtopic.id)}>{subtopic.label}</button>}
                        </li>
                      );
                    })}
                  </ul>
                </div>
                <p className="sr-only" role="status">{confirmation.finalSubtopics.length}개 선택 / 최소 {MINIMUM_SUBTOPICS}개</p>
                <button type="submit" className="ob-primary" disabled={pendingAction !== null || (!onboardingSaved && confirmation.finalSubtopics.length < MINIMUM_SUBTOPICS)}>{pendingAction === "submit" ? "준비 중..." : onboardingSaved ? "홈 연결 다시 시도" : "취향 매거진 보러가기"}</button>
                {!onboardingSaved && <button type="button" className="ob-edit" onClick={editSubtopics} disabled={pendingAction !== null}>수정하기</button>}
              </form>
            ) : null}
          </>
        )}
        {validationMessage && <p role="alert" className="ob-error">{validationMessage}</p>}
        {actionError && <p role="alert" className="ob-error">{actionError}</p>}
      </section>
    </main>
  );
}
