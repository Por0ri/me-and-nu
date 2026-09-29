"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import Image from "next/image";
import Link from "next/link";
import { useSyncExternalStore } from "react";

import { ContentCardActions } from "@/components/home/content-card-actions";
import { readContentSavedState, subscribeContentState } from "@/lib/content-state";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { formatRelativeTime } from "@/lib/relative-time";
import type { Content } from "@/types/content";

// Figma thumbnails for the existing Movie fixtures, used only in Home presentation.
const movieHomeThumbnails: Record<
  string,
  { src: string; objectPosition?: string } | undefined
> = {
  "content-movie-directing": { src: "/ui-home/movie/thumbnail-01.jpeg" },
  "content-movie-cinematography": { src: "/ui-home/movie/thumbnail-02.png" },
  "content-movie-adventure": { src: "/ui-home/movie/thumbnail-03.png" },
  "content-movie-fantasy": { src: "/ui-home/movie/thumbnail-04.png" },
  "content-movie-sci-fi": {
    src: "/ui-home/movie/thumbnail-05.png",
    // Match the wide image's crop in the Figma Movie thumbnail variant.
    objectPosition: "34% center",
  },
};

type ContentCardProps = {
  content: Content;
  topicId: string;
  sectionTitle: string;
  viewMode: "list" | "card";
};

export function ContentCard({
  content,
  topicId,
  sectionTitle,
  viewMode,
}: ContentCardProps) {
  const saved = useSyncExternalStore(
    subscribeContentState,
    () => readContentSavedState({ contentId: content.id, topicContext: { topicId } }, content.saved),
    () => content.saved,
  );
  const movieThumbnail =
    !isConsumerApiMode && topicId === "topic-movie" ? movieHomeThumbnails[content.id] : undefined;
  const imageUrl = content.imageUrl ?? movieThumbnail?.src;
  const chip = content.tag ?? sectionTitle;
  const byline = content.aiGenerated ? "me;nu" : content.sourceName;
  const relativeTime = formatRelativeTime(content.publishedAt);

  if (viewMode === "card") {
    return (
      <div className="va-content-card-shell">
        <Link
          href={{
            pathname: consumerRoutes.content(content.id),
            query: { topicId },
          }}
          aria-label={`${content.title} 상세 보기`}
          className="va-content-link va-content-card-link block"
        >
          <article className="va-content-card">
            {imageUrl ? (
              <Image
                src={imageUrl}
                alt=""
                fill
                sizes="(max-width: 375px) calc(100vw - 48px), 327px"
                unoptimized
                className="va-card-image"
                style={
                  content.imageUrl
                    ? undefined
                    : { objectPosition: movieThumbnail?.objectPosition }
                }
              />
            ) : (
              <div className="va-card-image-fallback" aria-hidden="true" />
            )}
            <div className="va-card-overlay">
              {chip ? <p className="va-section-chip">{chip}</p> : null}
              <h3>{content.title}</h3>
              <div className="va-card-metadata">
                <p>
                  By {byline}
                  {content.aiGenerated ? <span className="block">AI생성 컨텐츠</span> : null}
                </p>
                {relativeTime ? <p>{relativeTime}</p> : null}
              </div>
            </div>
          </article>
        </Link>
        <ContentCardActions
          key={JSON.stringify([topicId, content.id])}
          contentId={content.id}
          title={content.title}
          topicId={topicId}
          saved={saved}
          liked={content.liked ?? false}
        />
      </div>
    );
  }

  return (
    <Link
      href={{
        pathname: consumerRoutes.content(content.id),
        query: { topicId },
      }}
      aria-label={`${content.title} 상세 보기`}
      className="va-content-link block"
    >
      <article className="va-content-row">
        <div className="min-w-0 space-y-2">
          {chip ? <p className="va-section-chip">{chip}</p> : null}
          <h3 className="text-base leading-[1.4] tracking-[-0.02em]">
            {content.title}
          </h3>
          <p className="va-muted text-sm leading-normal">By {byline}</p>
          {relativeTime ? <p className="va-row-time">{relativeTime}</p> : null}
        </div>
        <div className="va-thumbnail">
          {imageUrl ? (
            <Image
              src={imageUrl}
              alt=""
              width={94}
              height={98}
              unoptimized
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="va-card-image-fallback" aria-hidden="true" />
          )}
        </div>
      </article>
    </Link>
  );
}
