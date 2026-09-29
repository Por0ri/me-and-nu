"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import { addMyTopic, getTopicOptions } from "@/lib/consumer-api/topics";
import { filterSubtopics } from "@/lib/subtopics/filter-subtopics";
import type { TopicOptions } from "@/types/consumer";

function AddTopicForm({ targetTopicId }: { targetTopicId: string | null }) {
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
  const inFlight = useRef(false);
  const requestGeneration = useRef(0);

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
    if (inFlight.current || topicId === selectedTopicId) {
      return;
    }
    setSelectedTopicId(topicId);
    setSelectedSubtopicIds([]);
    setSearchQuery("");
    setValidationMessage(null);
    setActionError(false);
  }

  function toggleSubtopic(subtopicId: string) {
    if (inFlight.current) {
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
    if (inFlight.current || !flowState || !options) {
      return;
    }

    const topic = options.topics.find((item) => item.id === selectedTopicId);
    if (!topic) {
      setValidationMessage("추가할 분야를 선택해 주세요.");
      return;
    }
    if (flowState.availableTopicIds.includes(topic.id)) {
      setValidationMessage("이미 생성된 분야입니다. 다른 분야를 선택해 주세요.");
      return;
    }
    if (selectedSubtopicIds.length < 5) {
      setValidationMessage("세부 취향을 5개 이상 선택해 주세요.");
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
      const result = await addMyTopic(
        { topicId: topic.id, subtopicIds: [...selectedSubtopicIds] },
        { availableTopicIds: [...flowState.availableTopicIds] },
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
        throw new Error("Added Mock topic does not match the request.");
      }

      addAvailableTopic(result.topicId);
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

    if (flowState?.availableTopicIds.includes(targetTopic.id)) {
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
    (topic) => !flowState?.availableTopicIds.includes(topic.id),
  );
  const selectedTopic = availableOptions.find(
    (topic) => topic.id === selectedTopicId,
  );
  const filteredSubtopics = filterSubtopics(
    selectedTopic?.subtopicOptions ?? [],
    searchQuery,
  );
  const selectedSubtopics = (selectedTopic?.subtopicOptions ?? []).filter(
    (subtopic) => selectedSubtopicIds.includes(subtopic.id),
  );

  if (availableOptions.length === 0) {
    return <p role="status">추가할 분야가 없습니다.</p>;
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      <p className="text-sm leading-6 text-black/60">
        분야와 세부주제를 선택해 주세요. 현재는 Mock이며 선택에 따라 홈 콘텐츠를
        다시 구성하지 않습니다.
      </p>
      <fieldset disabled={isSubmitting} className="space-y-3">
        <legend className="mb-3 font-medium">추가할 분야</legend>
        <div className="flex flex-wrap gap-3">
          {availableOptions.map((topic) => (
            <label
              key={topic.id}
              className="flex items-center gap-2 rounded-lg border border-black/20 px-4 py-2"
            >
              <input
                type="radio"
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
      {selectedTopic ? (
        <fieldset disabled={isSubmitting} className="space-y-3">
          <legend className="mb-3 font-medium">세부주제 (5개 이상 선택)</legend>
          <div className="space-y-2">
            <label
              htmlFor="add-topic-subtopic-search"
              className="block text-sm font-medium"
            >
              취향 검색
            </label>
            <input
              id="add-topic-subtopic-search"
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
          <p className="text-sm font-medium">검색 결과</p>
          {selectedTopic.subtopicOptions.length === 0 ? (
            <p role="status">선택할 세부주제가 없습니다.</p>
          ) : filteredSubtopics.length === 0 ? (
            <p role="status" className="text-sm text-black/60">
              검색 결과가 없어요. 다른 키워드로 검색해 주세요.
            </p>
          ) : (
            <div className="flex flex-wrap gap-3">
              {filteredSubtopics.map((subtopic) => (
                <label
                  key={subtopic.id}
                  className="flex items-center gap-2 rounded-full border border-black/20 px-4 py-2"
                >
                  <input
                    type="checkbox"
                    checked={selectedSubtopicIds.includes(subtopic.id)}
                    onChange={() => toggleSubtopic(subtopic.id)}
                  />
                  {subtopic.label}
                </label>
              ))}
            </div>
          )}
          <div className="space-y-3">
            <h3 className="text-sm font-medium">현재 선택된 취향 ({selectedSubtopics.length}개)</h3>
            {selectedSubtopics.length > 0 ? (
              <div className="flex flex-wrap gap-3">
                {selectedSubtopics.map((subtopic) => (
                  <button
                    key={subtopic.id}
                    type="button"
                    onClick={() => toggleSubtopic(subtopic.id)}
                    aria-label={`${subtopic.label} 선택 해제`}
                    className="rounded-full border border-black/20 px-4 py-2 text-sm"
                  >
                    {subtopic.label} <span aria-hidden="true">×</span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-sm text-black/60">
                선택한 취향이 없습니다. 5개 이상 선택해 주세요.
              </p>
            )}
          </div>
        </fieldset>
      ) : null}
      {validationMessage ? (
        <p role="alert" className="text-sm text-red-600">{validationMessage}</p>
      ) : null}
      {actionError ? (
        <p role="alert" className="text-sm text-red-600">
          분야를 추가하지 못했습니다. 선택을 유지한 채 다시 시도해 주세요.
        </p>
      ) : null}
      {isSubmitting ? <p role="status">분야를 추가하는 중입니다.</p> : null}
      <button
        type="submit"
        disabled={isSubmitting}
        className="rounded-lg bg-black px-4 py-3 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
      >
        {actionError ? "다시 시도" : "분야 추가"}
      </button>
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
  const { flowState } = useConsumerFlow();
  const invalidReturnTopic =
    returnTopicId !== null &&
    !flowState?.availableTopicIds.includes(returnTopicId);
  const homeHref =
    returnTopicId && !invalidReturnTopic
      ? `${consumerRoutes.home}?topicId=${encodeURIComponent(returnTopicId)}`
      : consumerRoutes.home;

  return (
    <main className="flex flex-1 justify-center bg-zinc-50 px-6 py-12">
      <div className="w-full max-w-3xl space-y-6 text-black">
        <h1 className="text-3xl font-semibold">분야 추가</h1>
        <Link
          href={homeHref}
          className="inline-flex rounded-lg border border-black/20 px-4 py-2 text-sm font-medium"
        >
          취소 / 홈으로 돌아가기
        </Link>
        {!flowState ? (
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
          <AddTopicForm targetTopicId={targetTopicId} />
        )}
      </div>
    </main>
  );
}
