"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, useReducer, type Dispatch, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { API_UNAUTHORIZED_EVENT, type Session } from "@/lib/api";
import { canUsePivotApi, isActiveSession, resolvePivotSessionRoute, restorePivotSession } from "@/lib/pivot/services";
import type { ConsentDraft } from "@/lib/pivot/consent";
import type { OnboardingProfile } from "@/lib/pivot/onboarding-service";
import { initialOnboardingState, onboardingReducer, type OnboardingState, type OnboardingAction } from "@/lib/pivot/onboarding";

type SessionState = { status: "loading" | "ready" | "error" | "disconnected"; session: Session | null; message?: string };
type ContextValue = { state: SessionState; refresh: () => void; draft: ConsentDraft | null; rememberDraft: (draft: ConsentDraft) => void; onboarding: OnboardingState; dispatchOnboarding: Dispatch<OnboardingAction>; profile: OnboardingProfile; rememberProfile: (profile: OnboardingProfile) => void; completeOnboarding: (session: Session) => void };
const Context = createContext<ContextValue | null>(null);
const isolatedSession: ContextValue = {
  state: { status: "disconnected", session: null }, draft: null,
  refresh: () => {}, rememberDraft: () => {},
  onboarding: initialOnboardingState(), dispatchOnboarding: () => {},
  profile: { nickname: "", birthDate: "" }, rememberProfile: () => {}, completeOnboarding: () => {},
};

export function PivotSessionProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isolated = ["/pivot/preview", "/pivot/demo"].some((path) => pathname === path || pathname.startsWith(`${path}/`));
  // Design previews and demos never read or change the real browser session.
  return isolated
    ? <Context.Provider value={isolatedSession}>{children}</Context.Provider>
    : <ConnectedPivotSessionProvider>{children}</ConnectedPivotSessionProvider>;
}
function ConnectedPivotSessionProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [state, setState] = useState<SessionState>({ status: "loading", session: null });
  const [attempt, setAttempt] = useState(0);
  const [draft, setDraft] = useState<ConsentDraft | null>(null);
  // Personal onboarding inputs stay in memory only and are cleared at session boundaries.
  const [profile, setProfile] = useState<OnboardingProfile>({ nickname: "", birthDate: "" });
  const [onboarding, dispatchOnboarding] = useReducer(onboardingReducer, undefined, initialOnboardingState);
  const epoch = useRef(0);
  const refresh = useCallback(() => { setDraft(null); setProfile({ nickname: "", birthDate: "" }); dispatchOnboarding({ type: "reset" }); setState({ status: "loading", session: null }); setAttempt((value) => value + 1); }, []);
  const completeOnboarding = useCallback((session: Session) => {
    if (!isActiveSession(session)) throw new Error("가입 완료 상태를 확인하지 못했어요.");
    epoch.current++;
    setDraft(null); setProfile({ nickname: "", birthDate: "" }); dispatchOnboarding({ type: "reset" });
    setState({ status: "ready", session });
  }, []);
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
      setProfile({ nickname: "", birthDate: "" });
      setState({ status: "ready", session: null, message: "로그인이 만료되었어요. 다시 로그인해 주세요." });
    };
    window.addEventListener(API_UNAUTHORIZED_EVENT, clear);
    return () => window.removeEventListener(API_UNAUTHORIZED_EVENT, clear);
  }, []);
  useEffect(() => {
    if (state.status !== "ready") return;
    const target = resolvePivotSessionRoute(state.session, pathname);
    if (target) router.replace(target);
  }, [state.status, state.session, pathname, router]);
  useEffect(() => {
    // Back/forward restoration after provider navigation must re-read rotated cookies.
    const restored = (event: PageTransitionEvent) => { if (event.persisted) refresh(); };
    window.addEventListener("pageshow", restored);
    return () => window.removeEventListener("pageshow", restored);
  }, [refresh]);
  return <Context.Provider value={{ state, refresh, draft, rememberDraft: setDraft, onboarding, dispatchOnboarding, profile, rememberProfile: setProfile, completeOnboarding }}>{children}</Context.Provider>;
}
export function usePivotSession() {
  const value = useContext(Context);
  if (!value) throw new Error("PivotSessionProvider is required.");
  return value;
}
