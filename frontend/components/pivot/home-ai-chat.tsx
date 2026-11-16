"use client";

import { useState } from "react";
import type { HomeDetailHandler } from "@/lib/pivot/home";
import { ChatDock } from "./chat-dock";
import { ChatSheet, HOME_CHAT_COPY } from "./chat-sheet";

/** 서비스 홈의 AI: 마스코트를 누르면 안내 바가 펼쳐지고, 바를 누르면 대화 시트가 열린다. */
export function HomeAIChat({ expanded, onExpandedChange, onViewDetail }: {
  expanded: boolean; onExpandedChange: (expanded: boolean) => void; onViewDetail?: HomeDetailHandler;
}) {
  const [open, setOpen] = useState(false);
  return <>
    <ChatDock home expanded={expanded} onExpandedChange={onExpandedChange} label="AI 콘텐츠 탐색" offFace="/pivot/home/chrome/ai.svg"
      prompt="어떤 콘텐츠를 찾고 계시나요? 무엇이든 물어보세요!" onOpen={() => setOpen(true)} />
    {open ? <ChatSheet place={{ kind: "home" }} copy={HOME_CHAT_COPY} onClose={() => setOpen(false)} onViewDetail={onViewDetail} /> : null}
  </>;
}
