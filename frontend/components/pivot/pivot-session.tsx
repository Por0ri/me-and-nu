"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, useReducer, type Dispatch, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { API_UNAUTHORIZED_EVENT } from "@/lib/api";
import type { PivotSession } from "@/lib/pivot/session";
import { canUsePivotApi, restorePivotSession } from "@/lib/pivot/services";
import type { ConsentDraft } from "@/lib/pivot/consent";
import { initialOnboardingState, onboardingReducer, type OnboardingState, type OnboardingAction } from "@/lib/pivot/onboarding";

type SessionState = { status: "loading" | "ready" | "error" | "disconnected"; session: PivotSession | null; message?: string };
type ContextValue = { state: SessionState; refresh: () => void; draft: ConsentDraft | null; rememberDraft: (draft: ConsentDraft) => void; onboarding: OnboardingState; dispatchOnboarding: Dispatch<OnboardingAction> };
const Context = createContext<ContextValue | null>(null);
const isolatedSession: ContextValue = {
  state: { status: "disconnected", session: null }, draft: null,
  refresh: () => {}, rememberDraft: () => {},
  onboarding: initialOnboardingState(), dispatchOnboarding: () => {},
};

export function PivotSessionProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isolated = ["/pivot/preview", "/pivot/demo", "/pivot/home"].some((path) => pathname === path || pathname.startsWith(`${path}/`));
  // Preview, demo and the disconnected H1 Home never mount API session listeners.
  return isolated
    ? <Context.Provider value={isolatedSession}>{children}</Context.Provider>
    : <ConnectedPivotSessionProvider>{children}</ConnectedPivotSessionProvider>;
}
function ConnectedPivotSessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: "loading", session: null });
  const [attempt, setAttempt] = useState(0);
  const [draft, setDraft] = useState<ConsentDraft | null>(null);
  const [onboarding, dispatchOnboarding] = useReducer(onboardingReducer, undefined, initialOnboardingState);
  const epoch = useRef(0);
  const refresh = useCallback(() => { setDraft(null); dispatchOnboarding({ type: "reset" }); setState({ status: "loading", session: null }); setAttempt((value) => value + 1); }, []);
  useEffect(() => {
    const request = ++epoch.current;
    if (!canUsePivotApi()) {
      setState({ status: "disconnected", session: null });
      return;
    }
    restorePivotSession().then(
      (session) => { if (request === epoch.current) setState({ status: "ready", session }); },
      () => { if (request === epoch.current) setState({ status: "error", session: null, message: "로그인 상태를 확인하지 못했어요. 다시 시도해 주세요." }); },
    );
    return () => { epoch.current++; };
  }, [attempt]);
  useEffect(() => {
    const clear = () => {
      epoch.current++;
      setDraft(null); dispatchOnboarding({ type: "reset" });
      setState({ status: "ready", session: null, message: "로그인이 만료되었어요. 다시 로그인해 주세요." });
    };
    window.addEventListener(API_UNAUTHORIZED_EVENT, clear);
    return () => window.removeEventListener(API_UNAUTHORIZED_EVENT, clear);
  }, []);
  return <Context.Provider value={{ state, refresh, draft, rememberDraft: setDraft, onboarding, dispatchOnboarding }}>{children}</Context.Provider>;
}
export function usePivotSession() {
  const value = useContext(Context);
  if (!value) throw new Error("PivotSessionProvider is required.");
  return value;
}
