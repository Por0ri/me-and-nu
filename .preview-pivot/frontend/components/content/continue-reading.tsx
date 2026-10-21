"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";

import { getSavedContents } from "@/lib/consumer-api/contents";
import { getHomeContents } from "@/lib/consumer-api/home";
import { consumerRoutes } from "@/lib/consumer-routes";
import { readContentSavedState, subscribeContentState } from "@/lib/content-state";
import { selectContinueReadingContents, type ContentReadingOrigin } from "@/lib/continue-reading";
import type { Content } from "@/types/content";

type ContinueReadingProps = {
  contentId: string;
  topicId: string;
  readingOrigin: ContentReadingOrigin;
};

export function ContinueReading({ contentId, topicId, readingOrigin }: ContinueReadingProps) {
  const [contents, setContents] = useState<Content[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const savedFlags = useSyncExternalStore(
    subscribeContentState,
    () => readingOrigin === "saved"
      ? contents.map((content) => readContentSavedState(
        { contentId: content.id, topicContext: { topicId } }, content.saved,
      ) ? "1" : "0").join("")
      : "",
    () => "",
  );

  useEffect(() => {
    let cancelled = false;
    async function loadContents() {
      try {
        let candidates: Content[];
        if (readingOrigin === "saved") {
          const result = await getSavedContents({ topicId }, { syncReactions: false });
          if (result.topic.id !== topicId) throw new Error("Saved contents topic mismatch.");
          candidates = result.contents;
        } else {
          const result = await getHomeContents({ topicId }, { syncReactions: false });
          if (result.selectedTopicId !== topicId) throw new Error("Home contents topic mismatch.");
          candidates = result.sections.flatMap((section) => section.contents);
        }
        if (!cancelled) setContents(candidates);
      } catch {
        if (!cancelled) setHasError(true);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    void loadContents();
    return () => { cancelled = true; };
  }, [contentId, topicId, readingOrigin, retryCount]);

  const candidates = readingOrigin === "saved"
    ? contents.filter((_, index) => savedFlags[index] === "1")
    : contents;
  const nextContents = selectContinueReadingContents(candidates, contentId);

  return (
    <section className="va-continue-reading" aria-labelledby="continue-reading-title" aria-busy={isLoading}>
      <h2 id="continue-reading-title" className="va-continue-heading">이어서 볼 콘텐츠</h2>
      {isLoading ? (
        <p role="status" className="va-continue-message">콘텐츠를 불러오는 중입니다.</p>
      ) : hasError ? (
        <div className="va-continue-message">
          <p role="alert">이어서 볼 콘텐츠를 불러오지 못했습니다.</p>
          <button
            type="button"
            className="va-secondary mt-3"
            onClick={() => {
              setIsLoading(true);
              setHasError(false);
              setRetryCount((count) => count + 1);
            }}
          >
            다시 시도
          </button>
        </div>
      ) : nextContents.length === 0 ? (
        <p role="status" className="va-continue-message">
          {readingOrigin === "saved" ? "이어서 볼 저장한 콘텐츠가 없습니다." : "이어서 볼 콘텐츠가 없습니다."}
        </p>
      ) : (
        <ul className="va-continue-list">
          {nextContents.map((content) => (
            <li key={content.id}>
              <Link
                href={{
                  pathname: consumerRoutes.content(content.id),
                  query: { topicId, ...(readingOrigin === "saved" ? { from: "saved" } : {}) },
                }}
                className="va-continue-link"
                aria-label={`${content.title} 이어서 보기`}
              >
                <span className="va-continue-chip"><span>{content.tag?.trim() || "콘텐츠"}</span></span>
                <span className="va-continue-title"><span>{content.title}</span></span>
                <Image src="/ui-content/arrow-forward.svg" alt="" width={24} height={24} className="va-continue-arrow" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
