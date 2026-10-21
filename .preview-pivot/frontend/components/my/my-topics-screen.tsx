"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { MySubPage } from "@/components/my/my-sub-page";
import { useTopicOptions } from "@/components/my/use-topic-options";
import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { consumerRoutes } from "@/lib/consumer-routes";

export function MyTopicsScreen({ topicId }: { topicId: string | null }) {
  const { flowState, isRestoring } = useConsumerFlow();
  const { topics, hasError } = useTopicOptions();
  const [notice, setNotice] = useState<string | null>(null);
  const currentTopicId = topicId ?? flowState?.initialHomeTopicId ?? null;
  const activeTopicIds = flowState?.availableTopicIds ?? [];

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 2000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  if (isConsumerApiMode && isRestoring) return null;

  return (
    <MySubPage
      backHref={currentTopicId ? { pathname: consumerRoutes.my, query: { topicId: currentTopicId } } : consumerRoutes.my}
    >
      <h1 className="va-step-title pt-8 text-center">
        추가 또는 삭제 할<br />관심분야를 골라주세요
      </h1>
      {hasError ? (
        <p role="alert" className="va-message text-center text-sm text-red-600">
          분야 목록을 불러오지 못했습니다.
        </p>
      ) : !topics ? (
        <p role="status" className="va-message va-muted text-center text-sm">불러오는 중입니다.</p>
      ) : (
        <ul className="va-my-topic-list">
          {topics.map((topic) => {
            const isActive = activeTopicIds.includes(topic.id);
            return (
              <li key={topic.id} className="va-my-topic-row">
                <span>{topic.label}</span>
                {isActive ? (
                  <button
                    type="button"
                    // 분야 삭제 API(API-023)가 아직 없어 안내만 한다.
                    onClick={() => setNotice("분야 삭제는 준비 중인 기능이에요.")}
                    className="va-my-topic-button va-my-topic-remove"
                  >
                    삭제
                  </button>
                ) : (
                  <Link
                    href={{
                      pathname: consumerRoutes.addTopic,
                      query: currentTopicId
                        ? { returnTopicId: currentTopicId, targetTopicId: topic.id }
                        : { targetTopicId: topic.id },
                    }}
                    className="va-my-topic-button va-my-topic-add"
                  >
                    추가
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <p role="status" className={notice ? "va-toast" : "sr-only"}>{notice}</p>
    </MySubPage>
  );
}
