"use client";

import { useEffect, useState } from "react";
import { createDetailRequestController, type DetailDataSource, type DetailState } from "@/lib/pivot/detail";
import type { HomeDetailTarget } from "@/lib/pivot/home";
import type { BookmarkControls } from "@/lib/pivot/bookmark-service";
import { PivotDetailScreen } from "./detail-screen";

const loading: DetailState = { status: "loading" };
export function DetailController({ source, target, onBack, onMy, onSaved, onHome, fromSaved = false, bookmarks }: {
  source: DetailDataSource; target: HomeDetailTarget; onBack: () => void; onMy?: () => void; onSaved?: () => void; onHome?: () => void;
  fromSaved?: boolean; bookmarks?: BookmarkControls;
}) {
  const [request, setRequest] = useState<{ source: DetailDataSource; postId: string; contentId: string; topicId?: number; state: DetailState }>();
  const [attempt, setAttempt] = useState(0);
  const { postId, contentId, topicId } = target;
  useEffect(() => {
    const controller = createDetailRequestController(source, (state) => setRequest({ source, postId, contentId, topicId, state }));
    void controller.load({ postId, contentId, ...(topicId === undefined ? {} : { topicId }) });
    return () => controller.cancel();
  }, [source, postId, contentId, topicId, attempt]);
  const state = request?.source === source && request.postId === postId && request.contentId === contentId && request.topicId === topicId ? request.state : loading;
  return <PivotDetailScreen key={`${topicId ?? "demo"}:${postId}:${contentId}`} state={state} demo={source.kind === "demo"} onBack={onBack} onMy={onMy} onSaved={onSaved} onHome={onHome} fromSaved={fromSaved} bookmarks={bookmarks} onRetry={() => setAttempt((value) => value + 1)} />;
}
