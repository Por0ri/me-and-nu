"use client";

import { useEffect, useState } from "react";
import { API_UNAUTHORIZED_EVENT, ApiError, getSession } from "@/lib/api";
import { adminFailure, type AdminReadState } from "@/lib/admin-content";

export function useAdminRead<T>(key: string, read: (signal: AbortSignal) => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; attempt: number; state: AdminReadState<T> }>();
  useEffect(() => {
    const controller = new AbortController();
    const publish = (state: AdminReadState<T>) => setResult({ key, attempt, state });
    publish({ status: "loading" });
    const unauthorized = () => {
      controller.abort();
      publish(adminFailure(new ApiError("", 401, "AUTH_REQUIRED")));
    };
    // Session authentication only controls UX. It never grants admin permission.
    getSession({ fresh: true, signal: controller.signal }).then(async session => {
      if (!session.authenticated) throw new ApiError("", 401, "AUTH_REQUIRED");
      return read(controller.signal);
    }).then(data => { if (!controller.signal.aborted) publish({ status: "ready", data }); })
      .catch(error => { if (!controller.signal.aborted) publish(adminFailure(error)); });
    const refresh = () => { controller.abort(); setAttempt(value => value + 1); };
    const visible = () => { if (document.visibilityState === "visible") refresh(); };
    const restored = (event: PageTransitionEvent) => { if (event.persisted) refresh(); };
    window.addEventListener(API_UNAUTHORIZED_EVENT, unauthorized);
    window.addEventListener("focus", refresh);
    window.addEventListener("pageshow", restored);
    document.addEventListener("visibilitychange", visible);
    return () => {
      controller.abort();
      window.removeEventListener(API_UNAUTHORIZED_EVENT, unauthorized);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("pageshow", restored);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [key, attempt, read]);
  const state: AdminReadState<T> = result?.key === key && result.attempt === attempt ? result.state : { status: "loading" };
  return { state, retry: () => setAttempt(value => value + 1) };
}
