"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { createActivityRequestController, type ActivityDataSource, type ActivityState, type ActivityTab } from "@/lib/pivot/my-activity";
import type { HomeDetailHandler } from "@/lib/pivot/home";
import { MyActivityScreen } from "./my-activity-screen";

const loading: ActivityState = { status: "loading" };
const unavailable: ActivityState = { status: "unconfigured", message: "로그인 상태를 확인한 뒤 내 활동을 다시 열어주세요." };
export function MyActivityController({ source, demo, tab, onTab, onHome, onMy, onSaved, onViewDetail }: {
  source?: ActivityDataSource; demo: boolean; tab: ActivityTab; onTab: (tab: ActivityTab) => void;
  onHome: () => void; onMy: () => void; onSaved: () => void; onViewDetail: HomeDetailHandler;
}) {
  const [request, setRequest] = useState<{ source: ActivityDataSource; tab: ActivityTab; state: ActivityState }>();
  const controller = useRef<ReturnType<typeof createActivityRequestController> | null>(null);
  // Layout cleanup cancels old fetches before a replacement session can paint or emit a late 401.
  useLayoutEffect(() => {
    if (!source || demo) return;
    const current = createActivityRequestController(source, tab, state => setRequest({ source, tab, state }));
    controller.current = current;
    void current.load();
    return () => { current.cancel(); controller.current = null; };
  }, [source, tab, demo]);
  let state = !source || demo ? unavailable : request?.source === source && request.tab === tab ? request.state : loading;
  if (source && !demo) { try { source.assertCurrent(); } catch { state = loading; } }
  return <MyActivityScreen state={state} tab={tab} demo={demo} onTab={onTab} onHome={onHome} onMy={onMy} onSaved={onSaved} onViewDetail={onViewDetail}
    onRetry={() => { void controller.current?.load(); }} onMore={() => { void controller.current?.loadMore(); }} />;
}
