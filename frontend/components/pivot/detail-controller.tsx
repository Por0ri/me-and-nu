"use client";

import { useEffect, useState } from "react";
import { createDetailRequestController, type DetailDataSource, type DetailState } from "@/lib/pivot/detail";
import type { HomeDetailTarget } from "@/lib/pivot/home";
import type { DemoBookmarkControls } from "@/lib/pivot/saved";
import { PivotDetailScreen } from "./detail-screen";

const loading: DetailState = { status: "loading" };
export function DetailController({ source, target, onBack, onMy, onSaved, onHome, fromSaved = false, bookmarks }: {
  source: DetailDataSource; target: HomeDetailTarget; onBack: () => void; onMy?: () => void; onSaved?: () => void; onHome?: () => void;
  fromSaved?: boolean; bookmarks?: DemoBookmarkControls;
}) {
  const [request, setRequest] = useState<{ source: DetailDataSource; postId: string; contentId: string; state: DetailState }>();
  const [attempt, setAttempt] = useState(0);
  const { postId, contentId } = target;
  useEffect(() => {
    const controller = createDetailRequestController(source, (state) => setRequest({ source, postId, contentId, state }));
    void controller.load({ postId, contentId });
    return () => controller.cancel();
  }, [source, postId, contentId, attempt]);
  const state = request?.source === source && request.postId === postId && request.contentId === contentId ? request.state : loading;
  return <PivotDetailScreen key={`${postId}:${contentId}`} state={state} demo={source.kind === "demo"} onBack={onBack} onMy={onMy} onSaved={onSaved} onHome={onHome} fromSaved={fromSaved} bookmarks={bookmarks} onRetry={() => setAttempt((value) => value + 1)} />;
}
