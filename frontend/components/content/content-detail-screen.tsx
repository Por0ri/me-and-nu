"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";

import { ContentAIPanel } from "@/components/content/content-ai-panel";
import { ContentCardActions } from "@/components/home/content-card-actions";
import { SourceInfo } from "@/components/content/source-info";
import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import { useContentNavigation, type PreparedContentResult } from "@/components/providers/content-navigation-provider";
import {
  ContentNotFoundError,
  getContent,
  saveContent,
  setContentFeedback,
  unsaveContent,
} from "@/lib/consumer-api/contents";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { readContentSavedState, subscribeContentState } from "@/lib/content-state";
import { formatRelativeTime } from "@/lib/relative-time";
import type { ContentDetail, ContentFeedback } from "@/types/content";

type ContentDetailScreenProps = {
  contentId: string;
  topicId: string | null;
};

type ContentDetailLoaderProps = {
  contentId: string;
  topicId: string;
  initialResult?: PreparedContentResult | null;
};

type ContentAction =
  | { type: "feedback"; feedback: ContentFeedback }
  | { type: "save"; saved: boolean };

function ContentDetailReady({ contentId, topicId }: ContentDetailLoaderProps) {
  const { read, release } = useContentNavigation();
  const [prepared] = useState(() => read(contentId, topicId));
  useLayoutEffect(() => {
    if (prepared) release(prepared);
  }, [prepared, release]);

  return <ContentDetailLoader contentId={contentId} topicId={topicId} initialResult={prepared} />;
}

function ContentDetailLoader({ contentId, topicId, initialResult }: ContentDetailLoaderProps) {
  const [content, setContent] = useState<ContentDetail | null>(initialResult?.content ?? null);
  const [isLoading, setIsLoading] = useState(!initialResult);
  const [error, setError] = useState<"not-found" | "error" | null>(initialResult?.error ?? null);
  const [retryCount, setRetryCount] = useState(0);
  const [pendingAction, setPendingAction] = useState<ContentAction | null>(null);
  const [failedAction, setFailedAction] = useState<ContentAction | null>(null);
  const actionInFlight = useRef(false);
  const requestGeneration = useRef(0);
  // 상단 저장 아이콘은 홈 카드와 같은 상태 저장소를 본다.
  const saved = useSyncExternalStore(
    subscribeContentState,
    () => readContentSavedState({ contentId, topicContext: { topicId } }, content?.saved ?? false),
    () => content?.saved ?? false,
  );

  useEffect(() => {
    requestGeneration.current += 1;
    let isCancelled = false;

    async function loadContent() {
      try {
        const result = await getContent({
          contentId,
          topicContext: { topicId },
        });

        if (result.id !== contentId || result.topicContext.topicId !== topicId) {
          throw new Error("Content does not match the requested context.");
        }

        if (!isCancelled) {
          setContent(result);
        }
      } catch (cause) {
        if (!isCancelled) {
          setError(cause instanceof ContentNotFoundError ? "not-found" : "error");
        }
      } finally {
        if (!isCancelled) {
          setIsLoading(false);
        }
      }
    }

    if (!initialResult || retryCount > 0) void loadContent();

    return () => {
      isCancelled = true;
      requestGeneration.current += 1;
      actionInFlight.current = false;
    };
  }, [contentId, topicId, retryCount, initialResult]);

  function retryContent() {
    setIsLoading(true);
    setError(null);
    setRetryCount((count) => count + 1);
  }

  async function runAction(action: ContentAction) {
    if (!content || actionInFlight.current) {
      return;
    }

    // Mock preserves its existing behavior; the API mode clears the same O/X choice.
    if (!isConsumerApiMode && action.type === "feedback" && content.feedback === action.feedback) {
      return;
    }

    const generation = requestGeneration.current;
    const input = { contentId, topicContext: { topicId } };
    actionInFlight.current = true;
    setPendingAction(action);
    setFailedAction(null);

    try {
      if (action.type === "feedback") {
        const result = await setContentFeedback({
          ...input,
          feedback: action.feedback,
          currentFeedback: content.feedback,
        });

        if (
          result.contentId !== contentId ||
          result.topicContext.topicId !== topicId
        ) {
          throw new Error(
            "Feedback response does not match the requested context.",
          );
        }

        if (requestGeneration.current === generation) {
          setContent((current) =>
            current ? { ...current, feedback: result.feedback } : current,
          );
        }
      } else {
        const result = await (action.saved
          ? saveContent(input)
          : unsaveContent(input));

        if (
          result.contentId !== contentId ||
          result.topicContext.topicId !== topicId
        ) {
          throw new Error("Save response does not match the requested context.");
        }

        if (requestGeneration.current === generation) {
          setContent((current) =>
            current ? { ...current, saved: result.saved } : current,
          );
        }
      }
    } catch {
      if (requestGeneration.current === generation) {
        setFailedAction(action);
      }
    } finally {
      if (requestGeneration.current === generation) {
        actionInFlight.current = false;
        setPendingAction(null);
      }
    }
  }

  if (isLoading) {
    return (
      <p role="status" className="va-message va-muted text-sm leading-6">
        콘텐츠를 불러오는 중입니다.
      </p>
    );
  }

  if (error === "error") {
    return (
      <section className="va-message space-y-4">
        <p role="alert" className="text-sm text-red-600">
          콘텐츠를 불러오지 못했습니다. 다시 시도해 주세요.
        </p>
        <button
          type="button"
          onClick={retryContent}
          className="va-secondary"
        >
          다시 시도
        </button>
      </section>
    );
  }

  if (error === "not-found" || !content) {
    return (
      <p role="status" className="va-message va-muted text-sm leading-6">
        현재 분야에서 해당 콘텐츠를 찾을 수 없습니다.
      </p>
    );
  }

  const relativeTime = formatRelativeTime(content.publishedAt);

  return (
    <article>
      <header className="va-detail-header va-detail-header-image">
        {content.imageUrl ? (
          <Image
            src={content.imageUrl}
            alt=""
            fill
            sizes="(max-width: 375px) 100vw, 375px"
            unoptimized
            className="va-detail-image"
          />
        ) : (
          <div className="va-detail-image-fallback" aria-hidden="true" />
        )}
        <div className="va-detail-topbar">
          <Link
            href={{ pathname: consumerRoutes.home, query: { topicId } }}
            aria-label="홈으로 돌아가기"
            className="va-detail-back"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M15 5l-7 7 7 7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </Link>
          <ContentCardActions
            contentId={contentId}
            title={content.title}
            topicId={topicId}
            saved={saved}
            liked={content.liked ?? false}
          />
        </div>
        <div className="va-detail-heading">
          {content.tag ? <p className="va-section-chip">{content.tag}</p> : null}
          <h1>{content.title}</h1>
          <div className="va-detail-byline">
            <p>
              By {content.aiGenerated ? "me;nu" : content.sourceName}
              {content.aiGenerated ? <span className="block">AI생성 컨텐츠</span> : null}
            </p>
            {relativeTime ? <p>{relativeTime}</p> : null}
          </div>
        </div>
      </header>
      <div className="va-detail-content space-y-8">
        <p className="va-detail-body whitespace-pre-wrap">
          {content.body?.trim() ? content.body : content.summary}
        </p>
        {content.recommendationReason ? (
          <section className="va-detail-reason space-y-2">
            <h2 className="text-sm font-semibold">추천 이유</h2>
            <p className="va-muted text-sm leading-6">
              {content.recommendationReason}
            </p>
          </section>
        ) : null}
        <section
          aria-labelledby="content-feedback-title"
          aria-busy={pendingAction !== null}
          className="va-detail-actions space-y-4"
        >
          <h2 id="content-feedback-title" className="text-base font-semibold">
            이런 글, 더 볼래요?
          </h2>
          <div className="flex gap-3">
            {(["O", "X"] as const).map((feedback) => (
              <button
                key={feedback}
                type="button"
                aria-label={feedback === "O" ? "더 볼래요" : "그만 볼래요"}
                aria-pressed={content.feedback === feedback}
                disabled={pendingAction !== null}
                onClick={() => void runAction({ type: "feedback", feedback })}
                className="va-detail-feedback"
              >
                {feedback === "O" ? "더 볼래요" : "그만 볼래요"}
              </button>
            ))}
          </div>
          {pendingAction ? (
            <p role="status" className="va-muted text-sm leading-6">
              {pendingAction.type === "feedback"
                ? "추천 의견을 반영하는 중입니다."
                : pendingAction.saved
                  ? "저장하는 중입니다."
                  : "저장을 취소하는 중입니다."}
            </p>
          ) : null}
          {failedAction ? (
            <div className="space-y-3">
              <p role="alert" className="text-sm text-red-600">
                {failedAction.type === "feedback"
                  ? "추천 의견을 반영하지 못했습니다. 다시 시도해 주세요."
                  : failedAction.saved
                    ? "저장하지 못했습니다. 다시 시도해 주세요."
                    : "저장을 취소하지 못했습니다. 다시 시도해 주세요."}
              </p>
              <button
                type="button"
                disabled={pendingAction !== null}
                onClick={() => void runAction(failedAction)}
                className="va-secondary"
              >
                실패한 작업 다시 시도
              </button>
            </div>
          ) : null}
        </section>
        <div className="va-article-sources">
          <SourceInfo sources={content.sources} />
        </div>
      </div>
      <ContentAIPanel
        key={JSON.stringify([topicId, contentId])}
        contentId={contentId}
        topicId={topicId}
      />
    </article>
  );
}

export function ContentDetailScreen({
  contentId,
  topicId,
}: ContentDetailScreenProps) {
  const { flowState, isRestoring, restoreError, refreshTopics } = useConsumerFlow();
  const validatedTopicId =
    topicId && flowState?.availableTopicIds.includes(topicId) ? topicId : null;

  if (isConsumerApiMode && isRestoring) return null;
  // 본문을 보여줄 때는 사진 위 뒤로가기 버튼을 쓰므로 위쪽 글자 메뉴를 숨긴다.
  const showsArticle = !(isConsumerApiMode && restoreError) && Boolean(flowState) && Boolean(validatedTopicId);

  return (
    <main className="ui-version-a ui-home">
      <div className="va-shell va-detail">
        {showsArticle ? null : (
        <nav aria-label="콘텐츠 탐색" className="va-detail-nav">
          <Link
            href={
              validatedTopicId
                ? { pathname: consumerRoutes.home, query: { topicId: validatedTopicId } }
                : consumerRoutes.home
            }
            className="va-detail-nav-link"
          >
            홈으로 돌아가기
          </Link>
          {validatedTopicId ? (
            <Link
              href={{ pathname: consumerRoutes.saved, query: { topicId } }}
              className="va-detail-nav-link"
            >
              저장 목록으로 이동
            </Link>
          ) : null}
        </nav>
        )}
        {isConsumerApiMode && restoreError ? (
          <section className="va-message space-y-4">
            <p role="alert" className="text-sm text-red-600">{restoreError}</p>
            <button
              type="button"
              onClick={() => void refreshTopics().catch(() => undefined)}
              className="va-secondary inline-flex"
            >
              다시 시도
            </button>
          </section>
        ) : !flowState ? (
          <section className="va-message space-y-4">
            <p role="status" className="va-muted text-sm leading-6">
              현재 연결된 분야 정보가 없습니다. 분야를 선택해 주세요.
            </p>
            <Link
              href={isConsumerApiMode ? consumerRoutes.addTopic : consumerRoutes.onboarding}
              className="va-primary inline-flex"
            >
              {isConsumerApiMode ? "분야 추가로 이동" : "온보딩으로 이동"}
            </Link>
          </section>
        ) : !topicId ? (
          <p role="status" className="va-message va-muted text-sm leading-6">
            콘텐츠의 분야 정보를 확인할 수 없습니다. 홈에서 다시 선택해 주세요.
          </p>
        ) : !validatedTopicId ? (
          <p role="status" className="va-message va-muted text-sm leading-6">
            현재 연결된 분야의 콘텐츠가 아닙니다. 홈에서 다시 선택해 주세요.
          </p>
        ) : (
          <ContentDetailReady
            key={JSON.stringify([topicId, contentId])}
            contentId={contentId}
            topicId={topicId}
          />
        )}
      </div>
    </main>
  );
}
