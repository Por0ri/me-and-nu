import { consumerRoutes } from "@/lib/consumer-routes";

import Image from "next/image";
import Link from "next/link";

import type { Content } from "@/types/content";

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
  return (
    <Link
      href={{
        pathname: consumerRoutes.content(content.id),
        query: { topicId },
      }}
      aria-label={`${content.title} 상세 보기`}
      className={
        viewMode === "card"
          ? "va-content-link va-content-card-link block"
          : "va-content-link block"
      }
    >
      <article
        className={viewMode === "card" ? "va-content-card" : "va-content-row"}
      >
        <div className="min-w-0 space-y-2">
          {sectionTitle ? <p className="va-section-chip">{sectionTitle}</p> : null}
          <h3 className="text-base leading-[1.4] tracking-[-0.02em]">
            {content.title}
          </h3>
          <p className="va-muted text-sm leading-normal">
            출처: {content.sourceName}
          </p>
          {content.saved ? <p className="va-saved-label">저장됨</p> : null}
        </div>
        <div className="va-thumbnail">
          {content.imageUrl ? (
            <Image
              src={content.imageUrl}
              alt=""
              width={viewMode === "card" ? 327 : 94}
              height={viewMode === "card" ? 218 : 98}
              unoptimized
              className="h-full w-full object-cover"
            />
          ) : (
            <span className="text-xs">이미지 없음</span>
          )}
        </div>
        <div className="col-span-2 space-y-2">
          <p className="va-muted text-sm leading-6">{content.summary}</p>
          {content.recommendationReason ? (
            <p className="va-recommendation text-xs leading-5">
              {content.recommendationReason}
            </p>
          ) : null}
        </div>
      </article>
    </Link>
  );
}
