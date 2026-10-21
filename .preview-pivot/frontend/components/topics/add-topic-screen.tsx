"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { OnboardingBack, OnboardingIcon } from "@/components/onboarding/onboarding-ui";
import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import { addMyTopic, getTopicOptions } from "@/lib/consumer-api/topics";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { filterSubtopics } from "@/lib/subtopics/filter-subtopics";
import type { AddMyTopicResult, TopicOptions } from "@/types/consumer";

const MINIMUM_SUBTOPICS = isConsumerApiMode ? 1 : 5;

function AddTopicForm({ targetTopicId, onTopicSaved }: {
  targetTopicId: string | null;
  onTopicSaved: () => void;
}) {
  const router = useRouter();
  const { flowState, addAvailableTopic } = useConsumerFlow();
  const [options, setOptions] = useState<TopicOptions | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [optionsError, setOptionsError] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  // The query is only an initial candidate; validate it after loading options.
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(targetTopicId);
  const [selectedSubtopicIds, setSelectedSubtopicIds] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [validationMessage, setValidationMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [createdTopicId, setCreatedTopicId] = useState<string | null>(null);
  const [savedTopic, setSavedTopic] = useState<AddMyTopicResult | null>(null);
  const inFlight = useRef(false);
  const requestGeneration = useRef(0);
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    requestGeneration.current += 1;

    return () => {
      requestGeneration.current += 1;
      inFlight.current = false;
    };
  }, []);

  useEffect(() => {
    let isCancelled = false;

    async function loadOptions() {
      try {
        const result = await getTopicOptions();
        if (!isCancelled) {
          setOptions(result);
        }
      } catch {
        if (!isCancelled) {
          setOptionsError(true);
        }
      } finally {
        if (!isCancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadOptions();
    return () => {
      isCancelled = true;
    };
  }, [retryCount]);

  useEffect(() => {
    // Provider에 활성 분야가 반영된 render 이후에만 Home으로 이동한다.
    if (createdTopicId && flowState?.availableTopicIds.includes(createdTopicId)) {
      router.push(`${consumerRoutes.home}?topicId=${encodeURIComponent(createdTopicId)}`);
    }
  }, [createdTopicId, flowState, router]);

  function selectTopic(topicId: string) {
    if (inFlight.current || savedTopic || topicId === selectedTopicId) {
      return;
    }
    setSelectedTopicId(topicId);
    setSelectedSubtopicIds([]);
    setSearchQuery("");
    setValidationMessage(null);
    setActionError(false);
  }

  function toggleSubtopic(subtopicId: string) {
    if (inFlight.current || savedTopic) {
      return;
    }
    setSelectedSubtopicIds((current) =>
      current.includes(subtopicId)
        ? current.filter((id) => id !== subtopicId)
        : [...current, subtopicId],
    );
    setValidationMessage(null);
    setActionError(false);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || (!isConsumerApiMode && !flowState) || !options) {
      return;
    }

    const topic = options.topics.find((item) => item.id === selectedTopicId);
    if (!topic) {
      setValidationMessage("추가할 분야를 선택해 주세요.");
      return;
    }
    if (!savedTopic && flowState?.availableTopicIds.includes(topic.id)) {
      setValidationMessage("이미 생성된 분야입니다. 다른 분야를 선택해 주세요.");
      return;
    }
    if (selectedSubtopicIds.length < MINIMUM_SUBTOPICS) {
      setValidationMessage(`세부 취향을 ${MINIMUM_SUBTOPICS}개 이상 선택해 주세요.`);
      return;
    }
    if (
      new Set(selectedSubtopicIds).size !== selectedSubtopicIds.length ||
      !selectedSubtopicIds.every((id) =>
        topic.subtopicOptions.some((option) => option.id === id),
      )
    ) {
      setValidationMessage("선택한 분야의 세부주제를 다시 확인해 주세요.");
      return;
    }

    const generation = requestGeneration.current;
    inFlight.current = true;
    setIsSubmitting(true);
    setActionError(false);
    setValidationMessage(null);

    try {
      const result = savedTopic ?? await addMyTopic(
        { topicId: topic.id, subtopicIds: [...selectedSubtopicIds] },
        { availableTopicIds: [...(flowState?.availableTopicIds ?? [])] },
      );
      if (requestGeneration.current !== generation) {
        return;
      }
      if (
        result.topicId !== topic.id ||
        result.subtopicIds.length !== selectedSubtopicIds.length ||
        new Set(result.subtopicIds).size !== result.subtopicIds.length ||
        !result.subtopicIds.every((id) => selectedSubtopicIds.includes(id))
      ) {
        throw new Error("Added topic does not match the request.");
      }

      setSavedTopic(result);
      onTopicSaved();
      await addAvailableTopic(result.topicId);
      if (requestGeneration.current !== generation) return;
      setCreatedTopicId(result.topicId);
    } catch {
      if (requestGeneration.current === generation) {
        setActionError(true);
        setIsSubmitting(false);
        inFlight.current = false;
      }
    }
  }

  if (isLoading) {
    return <p role="status">추가할 분야를 불러오는 중입니다.</p>;
  }
  if (optionsError || !options) {
    return (
      <section className="space-y-4">
        <p role="alert" className="text-sm text-red-600">
          분야를 불러오지 못했습니다. 다시 시도해 주세요.
        </p>
        <button
          type="button"
          onClick={() => {
            setIsLoading(true);
            setOptionsError(false);
            setRetryCount((count) => count + 1);
          }}
          className="rounded-lg border border-black/20 px-4 py-2 text-sm"
        >
          다시 시도
        </button>
      </section>
    );
  }
  if (createdTopicId) {
    return <p role="status">새 분야의 홈으로 이동하는 중입니다.</p>;
  }

  if (targetTopicId !== null) {
    const targetTopic = options.topics.find((topic) => topic.id === targetTopicId);

    if (!targetTopic) {
      return (
        <p role="status" className="text-sm text-black/60">
          추가할 분야 정보를 확인할 수 없습니다. 홈에서 다시 선택해 주세요.
        </p>
      );
    }

    if (!savedTopic && flowState?.availableTopicIds.includes(targetTopic.id)) {
      return (
        <section className="space-y-4">
          <p role="status" className="text-sm text-black/60">
            {targetTopic.label} 분야는 이미 생성되어 있습니다.
          </p>
          <Link
            href={{ pathname: consumerRoutes.home, query: { topicId: targetTopic.id } }}
            className="inline-flex rounded-lg border border-black/20 px-4 py-2 text-sm font-medium"
          >
            {targetTopic.label} 홈으로 이동
          </Link>
        </section>
      );
    }
  }

  const availableOptions = options.topics.filter(
    (topic) => topic.id === savedTopic?.topicId || !flowState?.availableTopicIds.includes(topic.id),
  );
  const selectedTopic = availableOptions.find(
    (topic) => topic.id === selectedTopicId,
  );
  const filteredSubtopics = filterSubtopics(
    selectedTopic?.subtopicOptions ?? [],
    searchQuery,
  );
  if (availableOptions.length === 0) {
    return <p role="status">추가할 분야가 없습니다.</p>;
  }

  const topicPicker = (
    <details className="ob-add-topic-picker" open={!selectedTopic}>
      <summary>{selectedTopic ? `${selectedTopic.label} · 분야 변경` : "추가할 분야"}</summary>
      <fieldset disabled={isSubmitting || savedTopic !== null}>
        <legend className="sr-only">추가할 분야</legend>
        <div className="ob-chips">
          {availableOptions.map((topic) => (
            <label
              key={topic.id}
              className="ob-chip"
            >
              <input
                type="radio"
                className="sr-only"
                name="new-topic"
                value={topic.id}
                checked={selectedTopicId === topic.id}
                onChange={() => selectTopic(topic.id)}
              />
              {topic.label}
            </label>
          ))}
        </div>
      </fieldset>
    </details>
  );

  return (
    <form onSubmit={submit} className="ob-subtopic-form">
      <div className="ob-intro">
        <h1>탐색하고 싶은 세부 토픽을<br />{MINIMUM_SUBTOPICS}개 이상 선택하세요.</h1>
      </div>
      {!selectedTopic ? topicPicker : null}
      {selectedTopic ? (
        <>
          <div className="ob-search">
            <input
              ref={searchInputRef}
              id="add-topic-subtopic-search"
              type="search"
              aria-label={`${selectedTopic.label} 세부 토픽 검색`}
              disabled={isSubmitting || savedTopic !== null}
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                }
              }}
              placeholder="장르, 스토리, 배경, 분위기로 검색하세요."
            />
            <button type="button" aria-label="세부 토픽 검색" disabled={isSubmitting || savedTopic !== null} onClick={() => searchInputRef.current?.focus()}>
              <OnboardingIcon name="search-arrow" />
            </button>
          </div>
          <fieldset disabled={isSubmitting || savedTopic !== null} className="ob-subtopic-options">
            <legend className="sr-only">{selectedTopic.label} 세부 토픽</legend>
            {selectedTopic.subtopicOptions.length === 0 ? (
              <p role="status" className="ob-empty">선택할 세부주제가 없습니다.</p>
            ) : filteredSubtopics.length === 0 ? (
              <p role="status" className="ob-empty">
                검색 결과가 없어요. 다른 키워드로 검색해 주세요.
              </p>
            ) : (
              <div className="ob-chips">
                {filteredSubtopics.map((subtopic) => (
                  <label
                    key={subtopic.id}
                    className="ob-chip"
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={selectedSubtopicIds.includes(subtopic.id)}
                      onChange={() => toggleSubtopic(subtopic.id)}
                    />
                    {subtopic.label}
                  </label>
                ))}
              </div>
            )}
          </fieldset>
          <p className="ob-selection-count" role="status">
            {selectedSubtopicIds.length}개 선택 <span>/ 최소 {MINIMUM_SUBTOPICS}개</span>
          </p>
        </>
      ) : null}
      {validationMessage ? (
        <p role="alert" className="text-sm text-red-600">{validationMessage}</p>
      ) : null}
      {actionError ? (
        <p role="alert" className="text-sm text-red-600">
          {savedTopic ? "분야는 저장되었습니다. 홈 연결을 다시 시도해 주세요." : "분야를 추가하지 못했습니다. 선택을 유지한 채 다시 시도해 주세요."}
        </p>
      ) : null}
      {isSubmitting ? <p role="status">분야를 추가하는 중입니다.</p> : null}
      <button
        type="submit"
        disabled={isSubmitting || !selectedTopic || selectedSubtopicIds.length < MINIMUM_SUBTOPICS}
        className="ob-primary"
      >
        {actionError ? "다시 시도" : "분야 추가"}
      </button>
      {selectedTopic ? topicPicker : null}
    </form>
  );
}

export function AddTopicScreen({
  returnTopicId,
  targetTopicId,
}: {
  returnTopicId: string | null;
  targetTopicId: string | null;
}) {
  const router = useRouter();
  const { flowState, isRestoring, restoreError, refreshTopics } = useConsumerFlow();
  // Keep the form's successful POST result mounted while its first Topic read retries.
  const [hasSavedTopic, setHasSavedTopic] = useState(false);
  const invalidReturnTopic =
    returnTopicId !== null &&
    !flowState?.availableTopicIds.includes(returnTopicId);
  const homeHref =
    returnTopicId && !invalidReturnTopic
      ? `${consumerRoutes.home}?topicId=${encodeURIComponent(returnTopicId)}`
      : consumerRoutes.home;

  return (
    <main className="ui-version-a ui-onboarding">
      <div className="va-shell ob-screen ob-add-topic">
        <OnboardingBack onBack={() => router.push(homeHref)} />
        {isRestoring && !flowState && !hasSavedTopic ? <p role="status">내 분야를 불러오는 중입니다.</p> : restoreError && !flowState && !hasSavedTopic ? (
          <section className="space-y-4">
            <p role="alert">{restoreError}</p>
            <button type="button" className="ob-primary" onClick={() => void refreshTopics().catch(() => undefined)}>다시 시도</button>
          </section>
        ) : !flowState && !isConsumerApiMode ? (
          <section className="space-y-4">
            <p role="status" className="text-sm leading-6 text-black/60">
              현재 연결된 분야 정보가 없습니다. 온보딩에서 분야를 다시 선택해
              주세요.
            </p>
            <Link
              href={consumerRoutes.onboarding}
              className="inline-flex rounded-lg bg-black px-4 py-3 text-sm font-medium text-white"
            >
              온보딩으로 이동
            </Link>
          </section>
        ) : invalidReturnTopic ? (
          <p role="status" className="text-sm text-black/60">
            돌아갈 분야 정보를 확인할 수 없습니다. 홈에서 다시 선택해 주세요.
          </p>
        ) : (
          <AddTopicForm targetTopicId={targetTopicId} onTopicSaved={() => setHasSavedTopic(true)} />
        )}
        <Link href={homeHref} className="ob-edit ob-add-topic-cancel">
          취소 / 홈으로 돌아가기
        </Link>
      </div>
    </main>
  );
}
