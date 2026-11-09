"use client";

import { useId, useState } from "react";
import type { HomeDetailHandler } from "@/lib/pivot/home";
import type { SavedCard } from "@/lib/pivot/saved";
import { ProtectedSlideImage } from "./content-carousel";
import homeStyles from "./home.module.css";
import styles from "./saved.module.css";

export function ContentListCard({ card, onViewDetail, surface = "saved" }: {
  card: SavedCard & { summary?: string; sourceName?: string }; onViewDetail: HomeDetailHandler; surface?: "saved" | "activity";
}) {
  const id = useId(); const [failed, setFailed] = useState(false);
  return <button type="button" className={styles.card}
    data-saved-content={surface === "saved" ? card.contentId : undefined} data-saved-post={surface === "saved" ? card.postId : undefined}
    data-activity-content={surface === "activity" ? card.contentId : undefined}
    aria-labelledby={id} onClick={() => onViewDetail({ postId: card.postId, contentId: card.contentId, ...(card.topicId === undefined ? {} : { topicId: card.topicId }) })}>
    <span className={styles.thumbnail}>
      {card.image?.protected ? <ProtectedSlideImage key={card.image.src} src={card.image.src} alt="" retryable={false} /> : card.image?.src && !failed ? <img src={card.image.src} alt="" width={148} height={185} loading="lazy" draggable={false} onError={() => setFailed(true)} />
        : <span className={styles.placeholder}>이미지 준비 중</span>}
    </span>
    {/* Protected carousel covers already contain their copy. Keep that text unobscured. */}
    <span id={id} className={surface === "activity" && !card.image?.protected ? styles.overlay : styles.caption}>
      {surface === "activity" && !card.image?.protected && card.sourceName ? <span className={styles.eyebrow}>{card.sourceName}</span> : null}
      <span className={styles.title} data-saved-title={surface === "saved" ? "" : undefined}>{card.title}</span>
      {surface === "activity" && !card.image?.protected && card.summary ? <span className={styles.summary}>{card.summary}</span> : null}
      <span className={homeStyles.srOnly}> 자세히 보기</span>
    </span>
  </button>;
}
