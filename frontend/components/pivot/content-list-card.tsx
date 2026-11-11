"use client";

import { useId, useState } from "react";
import type { HomeDetailHandler } from "@/lib/pivot/home";
import type { SavedCard } from "@/lib/pivot/saved";
import { ProtectedSlideImage } from "./content-carousel";
import homeStyles from "./home.module.css";
import styles from "./saved.module.css";

export function ContentListCard({ card, onViewDetail, surface = "saved" }: {
  card: SavedCard; onViewDetail: HomeDetailHandler; surface?: "saved" | "activity";
}) {
  const id = useId(); const [failed, setFailed] = useState(false);
  // Authentication says nothing about image copy. Only explicit provenance may
  // hide duplicate text; normal service covers always retain their HTML copy.
  const embeddedCopy = card.imageHasEmbeddedCopy === true;
  const overlay = <span className={styles.savedOverlay}>
    {card.eyebrow ? <span className={styles.savedEyebrow} data-saved-eyebrow="">{card.eyebrow}</span> : null}
    <span id={id} className={styles.savedLabel}>
      <span className={styles.title} data-saved-title="">{card.title}</span>
      <span className={homeStyles.srOnly}> 자세히 보기</span>
    </span>
    {card.summary ? <span className={styles.savedSummary} data-saved-summary="">{card.summary}</span> : null}
  </span>;
  return <button type="button" className={styles.card}
    data-saved-content={surface === "saved" ? card.contentId : undefined} data-saved-post={surface === "saved" ? card.postId : undefined}
    data-activity-content={surface === "activity" ? card.contentId : undefined}
    aria-labelledby={id} onClick={() => onViewDetail({ postId: card.postId, contentId: card.contentId, ...(card.topicId === undefined ? {} : { topicId: card.topicId }) })}>
    <span className={styles.thumbnail} data-embedded-copy={embeddedCopy && !failed || undefined}>
      {card.image?.protected ? <ProtectedSlideImage key={card.image.src} src={card.image.src} alt="" retryable={false} /> : card.image?.src && !failed ? <img src={card.image.src} alt="" width={148} height={185} loading="lazy" draggable={false} onError={() => setFailed(true)} />
        : <span className={styles.placeholder}>이미지 준비 중</span>}
      {overlay}
    </span>
  </button>;
}
