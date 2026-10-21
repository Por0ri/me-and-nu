"use client";

import { useEffect, useState } from "react";
import { createHomeRequestController, type HomeDataSource, type HomeState } from "@/lib/pivot/home";
import { PivotHomeScreen } from "./home-screen";

const loading: HomeState = { status: "loading" };

export function HomeController({ source, onRestart }: { source: HomeDataSource; onRestart?: () => void }) {
  const [request, setRequest] = useState<{ source: HomeDataSource; state: HomeState }>({ source, state: loading });
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  useEffect(() => {
    const controller = createHomeRequestController(source, (state) => setRequest({ source, state }));
    void controller.load();
    return () => controller.cancel();
  }, [source, attempt]);
  // Never flash the previous source's data before the new effect starts.
  const state = request.source === source ? request.state : loading;
  return <PivotHomeScreen state={state} demo={source.kind === "demo"} query={query} onQueryChange={setQuery}
    onRetry={() => setAttempt((value) => value + 1)} onRestart={onRestart} />;
}
