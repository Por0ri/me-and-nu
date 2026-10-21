"use client";

import { useEffect, useState } from "react";

import { getTopicOptions } from "@/lib/consumer-api/topics";
import type { OnboardingTopicOption } from "@/types/consumer";

/** 전체 분야 목록(애니·영화·음악)을 한 번 불러온다. */
export function useTopicOptions() {
  const [topics, setTopics] = useState<OnboardingTopicOption[] | null>(null);
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    let isCancelled = false;
    getTopicOptions()
      .then((result) => {
        if (!isCancelled) setTopics(result.topics);
      })
      .catch(() => {
        if (!isCancelled) setHasError(true);
      });
    return () => {
      isCancelled = true;
    };
  }, []);

  return { topics, hasError };
}

/** 로고 옆에 붙는 분야 이름(movie, music …). 모르면 "nu". */
export function topicLabelFor(topics: OnboardingTopicOption[] | null, topicId: string | null) {
  const topic = topics?.find((item) => item.id === topicId);
  return topic?.code?.toLowerCase() ?? "nu";
}
