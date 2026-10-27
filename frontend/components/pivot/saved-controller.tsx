"use client";

import { useEffect, useState } from "react";
import { createSavedRequestController, type SavedDataSource, type SavedState } from "@/lib/pivot/saved";
import type { HomeDetailHandler } from "@/lib/pivot/home";
import { PivotSavedScreen } from "./saved-screen";

const loading: SavedState = { status: "loading" };
export function SavedController({ source, onHome, onMy, onViewDetail, onReady }: {
  source: SavedDataSource; onHome: () => void; onMy: () => void; onViewDetail: HomeDetailHandler; onReady: () => void;
}) {
  const [request, setRequest] = useState<{ source: SavedDataSource; state: SavedState }>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = createSavedRequestController(source, (state) => setRequest({ source, state }));
    const unsubscribe = source.subscribe?.(() => { void controller.load(); });
    void controller.load();
    return () => { unsubscribe?.(); controller.cancel(); };
  }, [source, attempt]);
  const state = request?.source === source ? request.state : loading;
  useEffect(() => { if (state.status === "ready" || state.status === "empty") onReady(); }, [state.status, onReady]);
  return <PivotSavedScreen state={state} demo={source.kind === "demo"} onHome={onHome} onMy={onMy} onViewDetail={onViewDetail} onRetry={() => setAttempt((value) => value + 1)} />;
}
