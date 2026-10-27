"use client";

import { useEffect, useState } from "react";
import { createMyPageRequestController, type MyPageDataSource, type MyPageState } from "@/lib/pivot/my-page";
import { PivotMyPageScreen } from "./my-page-screen";

const loading: MyPageState = { status: "loading" };
export function MyPageController({ source, onHome, onSaved, onLogout }: { source: MyPageDataSource; onHome: () => void; onSaved?: () => void; onLogout?: () => Promise<void> }) {
  const [request, setRequest] = useState<{ source: MyPageDataSource; state: MyPageState }>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = createMyPageRequestController(source, (state) => setRequest({ source, state }));
    void controller.load();
    return () => controller.cancel();
  }, [source, attempt]);
  const state = request?.source === source ? request.state : loading;
  return <PivotMyPageScreen key={source.kind} state={state} demo={source.kind === "demo"} onHome={onHome} onSaved={onSaved} onLogout={onLogout} onRetry={() => setAttempt((value) => value + 1)} />;
}
