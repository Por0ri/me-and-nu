"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import { getSavedContents } from "@/lib/consumer-api/contents";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import type { SavedContentsData } from "@/types/content";

function SavedContentsLoader({ topicId }: { topicId: string }) {
  const [data, setData] = useState<SavedContentsData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    let isCancelled = false;

    async function loadSavedContents() {
      try {
        const result = await getSavedContents({ topicId });

        if (result.topic.id !== topicId) {
          throw new Error("Saved contents topic does not match the request.");
        }

        if (!isCancelled) {
          setData(result);
        }
      } catch {
        if (!isCancelled) {
          setHasError(true);
        }
      } finally {
        if (!isCancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadSavedContents();

    return () => {
      isCancelled = true;
    };
  }, [topicId, retryCount]);

  function retry() {
    setIsLoading(true);
    setHasError(false);
    setRetryCount((count) => count + 1);
  }

  if (isLoading) {
    return (
      <p role="status" className="py-12 text-center text-sm text-black/60">
        저장 목록을 불러오는 중입니다.
      </p>
    );
  }

  if (hasError || !data) {
    return (
      <section className="space-y-4 rounded-xl border border-red-200 bg-white p-6 text-center">
        <p role="alert" className="text-sm text-red-600">
          저장 목록을 불러오지 못했습니다. 다시 시도해 주세요.
        </p>
        <button
          type="button"
          onClick={retry}
          className="rounded-lg border border-black/20 px-4 py-2 text-sm font-medium text-black"
        >
          다시 시도
        </button>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-medium text-black">
        현재 분야: {data.topic.name}
      </h2>
      {data.contents.length === 0 ? (
        <p role="status" className="py-12 text-center text-sm text-black/60">
          현재 분야에 저장한 콘텐츠가 없습니다.
        </p>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2">
          {data.contents.map((content) => (
            <li key={content.id}>
              <Link
                href={{
                  pathname: consumerRoutes.content(content.id),
                  query: { topicId },
                }}
                aria-label={`${content.title} 상세 보기`}
                className="block space-y-3 rounded-xl border border-black/10 bg-white p-5 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-black"
              >
                <h3 className="text-lg font-semibold text-black">
                  {content.title}
                </h3>
                <p className="text-sm leading-6 text-black/70">
                  {content.summary}
                </p>
                <p className="text-xs text-black/50">
                  출처: {content.sourceName}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function SavedScreen({ topicId }: { topicId: string | null }) {
  const { flowState, isRestoring, restoreError, refreshTopics } = useConsumerFlow();
  const validatedTopicId =
    topicId && flowState?.availableTopicIds.includes(topicId) ? topicId : null;

  if (isConsumerApiMode && isRestoring) return null;

  return (
    <main className="flex flex-1 justify-center bg-zinc-50 px-6 py-12">
      <div className="w-full max-w-3xl space-y-6">
        <h1 className="text-3xl font-semibold text-black">저장 목록</h1>
        <Link
          href={
            validatedTopicId
              ? { pathname: consumerRoutes.home, query: { topicId: validatedTopicId } }
              : consumerRoutes.home
          }
          className="inline-flex rounded-lg border border-black/20 px-4 py-2 text-sm font-medium text-black"
        >
          홈으로 돌아가기
        </Link>
        {isConsumerApiMode && restoreError ? (
          <section className="space-y-4 rounded-xl border border-red-200 bg-white p-6 text-center">
            <p role="alert" className="text-sm text-red-600">{restoreError}</p>
            <button
              type="button"
              onClick={() => void refreshTopics().catch(() => undefined)}
              className="rounded-lg border border-black/20 px-4 py-2 text-sm font-medium text-black"
            >
              다시 시도
            </button>
          </section>
        ) : !flowState ? (
          <section className="space-y-4 rounded-xl border border-black/10 bg-white p-6 text-center">
            <p role="status" className="text-sm leading-6 text-black/60">
              현재 연결된 분야 정보가 없습니다. 분야를 선택해 주세요.
            </p>
            <Link
              href={isConsumerApiMode ? consumerRoutes.addTopic : consumerRoutes.onboarding}
              className="inline-flex rounded-lg bg-black px-4 py-3 text-sm font-medium text-white"
            >
              {isConsumerApiMode ? "분야 추가로 이동" : "온보딩으로 이동"}
            </Link>
          </section>
        ) : !topicId ? (
          <p role="status" className="py-12 text-center text-sm text-black/60">
            저장 목록의 분야 정보를 확인할 수 없습니다. 홈에서 다시 선택해 주세요.
          </p>
        ) : !validatedTopicId ? (
          <p role="status" className="py-12 text-center text-sm text-black/60">
            현재 연결된 분야의 저장 목록이 아닙니다. 홈에서 다시 선택해 주세요.
          </p>
        ) : (
          <SavedContentsLoader key={topicId} topicId={topicId} />
        )}
      </div>
    </main>
  );
}
