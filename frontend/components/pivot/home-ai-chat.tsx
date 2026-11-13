"use client";

import { useState } from "react";
import type { HomeDetailHandler } from "@/lib/pivot/home";
import { ChatSheet, HOME_CHAT_COPY } from "./chat-sheet";
import homeStyles from "./home.module.css";
import styles from "./chat.module.css";

/** 서비스 홈의 AI: 마스코트를 누르면 상세와 같은 안내 바가 펼쳐지고, 바를 누르면 대화 시트가 열린다. */
export function HomeAIChat({ expanded, onExpandedChange, onViewDetail }: {
  expanded: boolean; onExpandedChange: (expanded: boolean) => void; onViewDetail?: HomeDetailHandler;
}) {
  const [open, setOpen] = useState(false);
  return <>
    {expanded ? <div className={styles.homePrompt}>
      <button type="button" aria-haspopup="dialog" onClick={() => setOpen(true)}>어떤 콘텐츠를 찾고 계시나요? 무엇이든 물어보세요!</button>
    </div> : null}
    <button type="button" className={`${homeStyles.aiButton} ${styles.homeAiButton}`} aria-label="AI 콘텐츠 탐색" aria-expanded={expanded} data-home-ai=""
      onClick={() => onExpandedChange(!expanded)}><img src={expanded ? "/pivot/detail/ai-expanded.svg" : "/pivot/home/chrome/ai.svg"} alt="" /></button>
    {open ? <ChatSheet place={{ kind: "home" }} copy={HOME_CHAT_COPY} onClose={() => setOpen(false)} onViewDetail={onViewDetail} /> : null}
  </>;
}
