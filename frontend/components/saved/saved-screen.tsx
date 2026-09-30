"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ContentCard } from "@/components/home/content-card";
import { BrandHeader } from "@/components/navigation/brand-header";
import { ConsumerBottomNav } from "@/components/navigation/consumer-bottom-nav";
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
      <>
        <BrandHeader topicLabel="nu" title="SAVE" />
        <p role="status" className="va-message va-muted text-center text-sm">
          저장 목록을 불러오는 중입니다.
        </p>
      </>
    );
  }

  if (hasError || !data) {
    return (
      <>
        <BrandHeader topicLabel="nu" title="SAVE" />
        <section className="va-message space-y-4 text-center">
          <p role="alert" className="text-sm text-red-600">
            저장 목록을 불러오지 못했습니다. 다시 시도해 주세요.
          </p>
          <button type="button" onClick={retry} className="va-secondary">
            다시 시도
          </button>
        </section>
      </>
    );
  }

  return (
    <>
      <BrandHeader topicLabel={data.topic.code?.toLowerCase() ?? data.topic.name} title="SAVE" />
      <p className="sr-only">현재 분야: {data.topic.name}</p>
      {data.contents.length === 0 ? (
        <p role="status" className="va-message va-muted text-center text-sm">
          아직 저장한 콘텐츠가 없어요. 홈에서 마음에 드는 글을 저장해 보세요.
        </p>
      ) : (
        <ul>
          {data.contents.map((content) => (
            <li key={content.id}>
              <ContentCard content={content} topicId={topicId} sectionTitle="" viewMode="list" readingOrigin="saved" />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export function SavedScreen({ topicId }: { topicId: string | null }) {
  const { flowState, isRestoring, restoreError, refreshTopics } = useConsumerFlow();
  // 주소에 분야가 없으면(탭으로 바로 들어온 경우) 처음 고른 분야를 쓴다.
  const requestedTopicId = topicId ?? flowState?.initialHomeTopicId ?? null;
  const validatedTopicId =
    requestedTopicId && flowState?.availableTopicIds.includes(requestedTopicId)
      ? requestedTopicId
      : null;

  if (isConsumerApiMode && isRestoring) return null;

  return (
    <main className="ui-version-a ui-home">
      <div className="va-shell va-home va-tab-page">
        {isConsumerApiMode && restoreError ? (
          <section className="va-message space-y-4 text-center">
            <p role="alert" className="text-sm text-red-600">{restoreError}</p>
            <button
              type="button"
              onClick={() => void refreshTopics().catch(() => undefined)}
              className="va-secondary"
            >
              다시 시도
            </button>
          </section>
        ) : !flowState ? (
          <section className="va-message space-y-4 text-center">
            <p role="status" className="va-muted text-sm leading-6">
              현재 연결된 분야 정보가 없습니다. 분야를 선택해 주세요.
            </p>
            <Link
              href={isConsumerApiMode ? consumerRoutes.addTopic : consumerRoutes.onboarding}
              className="va-primary inline-flex w-full items-center justify-center"
            >
              {isConsumerApiMode ? "분야 추가로 이동" : "온보딩으로 이동"}
            </Link>
          </section>
        ) : !validatedTopicId ? (
          <p role="status" className="va-message va-muted text-center text-sm">
            현재 연결된 분야의 저장 목록이 아닙니다. 홈에서 다시 선택해 주세요.
          </p>
        ) : (
          <SavedContentsLoader key={validatedTopicId} topicId={validatedTopicId} />
        )}
        <ConsumerBottomNav current="saved" topicId={validatedTopicId} />
      </div>
    </main>
  );
}
