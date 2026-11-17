"use client";

import styles from "./home.module.css";

export function HomeAIButton({ onNotice }: { onNotice: (message: string) => void }) {
  return <button type="button" className={styles.aiButton} aria-label="AI 콘텐츠 탐색" aria-disabled="true" data-home-ai=""
    onClick={() => onNotice("AI 콘텐츠 탐색은 준비 중이에요.")}><img src="/pivot/home/chrome/ai.svg" alt="" /></button>;
}
