"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, useReducer, type Dispatch, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { API_UNAUTHORIZED_EVENT, ApiError, getSession, logout as logoutApi, requestWithdrawal, restoreWithdrawal, type Session } from "@/lib/api";
import { canUsePivotApi, isActiveSession, isRestorationSession, requirePivotApi, resolvePivotSessionRoute, restorePivotSession } from "@/lib/pivot/services";
import type { ConsentDraft } from "@/lib/pivot/consent";
import type { OnboardingProfile } from "@/lib/pivot/onboarding-service";
import { initialOnboardingState, onboardingReducer, type OnboardingState, type OnboardingAction } from "@/lib/pivot/onboarding";

type SessionState = { status: "loading" | "ready" | "error" | "disconnected"; session: Session | null; message?: string };
type ContextValue = { state: SessionState; refresh: () => void; logout: () => Promise<void>; withdraw?: () => Promise<void>; restoreAccount?: () => Promise<void>; returnToLogin?: () => void; draft: ConsentDraft | null; rememberDraft: (draft: ConsentDraft) => void; onboarding: OnboardingState; dispatchOnboarding: Dispatch<OnboardingAction>; profile: OnboardingProfile; rememberProfile: (profile: OnboardingProfile) => void; completeOnboarding: (session: Session) => void };
const Context = createContext<ContextValue | null>(null);
const isolatedSession: ContextValue = {
  state: { status: "disconnected", session: null }, draft: null,
  refresh: () => {}, logout: async () => {}, rememberDraft: () => {},
  onboarding: initialOnboardingState(), dispatchOnboarding: () => {},
  profile: { nickname: "" }, rememberProfile: () => {}, completeOnboarding: () => {},
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
  const [profile, setProfile] = useState<OnboardingProfile>({ nickname: "" });
  const [onboarding, dispatchOnboarding] = useReducer(onboardingReducer, undefined, initialOnboardingState);
  const epoch = useRef(0);
  const logoutRequest = useRef<Promise<void> | null>(null);
  const changingAccount = useRef(false);
  const restoredAccount = useRef<{ owner: Session; id: number } | null>(null);
  const endSession = useCallback((message?: string) => {
    epoch.current++;
    restoredAccount.current = null;
    setDraft(null); setProfile({ nickname: "" }); dispatchOnboarding({ type: "reset" });
    setState({ status: "ready", session: null, message });
  }, []);
  const expireSession = useCallback(() => endSession("로그인이 만료되었어요. 다시 로그인해 주세요."), [endSession]);
  const refresh = useCallback(() => {
    if (changingAccount.current || restoredAccount.current) return;
    endSession();
    setState({ status: "loading", session: null });
    setAttempt((value) => value + 1);
  }, [endSession]);
  const completeOnboarding = useCallback((session: Session) => {
    if (!isActiveSession(session)) throw new Error("가입 완료 상태를 확인하지 못했어요.");
    epoch.current++;
    setDraft(null); setProfile({ nickname: "" }); dispatchOnboarding({ type: "reset" });
    setState({ status: "ready", session });
  }, []);
  const logout = useCallback(() => {
    if (changingAccount.current) return Promise.reject(new Error("계정 요청을 처리하고 있어요."));
    if (!logoutRequest.current) {
      logoutRequest.current = logoutApi().then(() => {
        endSession();
        router.replace("/pivot/login");
      }).finally(() => { logoutRequest.current = null; });
    }
    return logoutRequest.current;
  }, [endSession, router]);
  async function verifyAccount(owner: Session, request: number) {
    const current = await getSession({ fresh: true });
    // The token binds the confirmation even when a pending session exposes no user ID.
    const sameAccount = isActiveSession(owner)
      ? isActiveSession(current) && current.user?.id === owner.user?.id
      : isRestorationSession(current);
    if (request !== epoch.current || !sameAccount ||
        typeof owner.csrfToken !== "string" || !owner.csrfToken || current.csrfToken !== owner.csrfToken) {
      throw new ApiError("로그인 상태가 바뀌었어요. 다시 로그인해 주세요.", 403, "ACCOUNT_SESSION_CHANGED");
    }
  }
  async function withdraw() {
    requirePivotApi();
    const owner = state.session;
    if (changingAccount.current || logoutRequest.current || !owner || !isActiveSession(owner)) return;
    const request = epoch.current;
    changingAccount.current = true;
    try {
      await verifyAccount(owner, request);
      await requestWithdrawal(owner.csrfToken);
      if (request !== epoch.current) return;
      endSession("탈퇴 신청이 완료됐어요. 30일 이내에 같은 소셜 계정으로 로그인해 계정을 복구할 수 있어요.");
      router.replace("/pivot/login");
    } catch (error) {
      if (request === epoch.current && error instanceof ApiError) {
        if (error.status === 401) expireSession();
        else if (["ACCOUNT_SESSION_CHANGED", "CSRF_INVALID"].includes(error.code)) {
          endSession("로그인 상태가 바뀌었어요. 다시 로그인해 주세요.");
        }
      }
      throw error;
    } finally {
      changingAccount.current = false;
    }
  }
  async function restoreAccount() {
    requirePivotApi();
    const owner = state.session;
    if (changingAccount.current || !owner || !isRestorationSession(owner)) return;
    const request = epoch.current;
    changingAccount.current = true;
    try {
      if (restoredAccount.current?.owner !== owner) {
        await verifyAccount(owner, request);
        const result = await restoreWithdrawal(owner.csrfToken);
        if (request !== epoch.current) return;
        if (result?.status !== "active" || !result.user || !isActiveSession({
          authenticated: true, sessionState: "active", user: result.user,
          onboardingCompleted: result.onboardingCompleted, csrfToken: "",
        })) throw new ApiError("복구 결과를 확인하지 못했어요. 다시 로그인해 주세요.", 200, "RESTORATION_UNCONFIRMED");
        restoredAccount.current = { owner, id: result.user.id };
      }
      // A failed read can be retried without resending the already successful POST.
      const current = await getSession({ fresh: true });
      if (request !== epoch.current) return;
      if (!isActiveSession(current) || current.user?.id !== restoredAccount.current.id ||
          typeof current.csrfToken !== "string" || !current.csrfToken || current.csrfToken === owner.csrfToken) {
        throw new ApiError("복구한 계정의 로그인 상태를 확인하지 못했어요. 다시 로그인해 주세요.", 403, "ACCOUNT_SESSION_CHANGED");
      }
      endSession();
      setState({ status: "ready", session: current });
    } catch (error) {
      if (request === epoch.current && error instanceof ApiError && error.status === 401) expireSession();
      throw error;
    } finally {
      changingAccount.current = false;
    }
  }
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
    // During a transition the account request handles its own 401. Background
    // reads may be using the session that the transition is about to replace.
    const clear = () => { if (!changingAccount.current) expireSession(); };
    window.addEventListener(API_UNAUTHORIZED_EVENT, clear);
    return () => window.removeEventListener(API_UNAUTHORIZED_EVENT, clear);
  }, [expireSession]);
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
  return <Context.Provider value={{ state, refresh, logout, withdraw, restoreAccount, returnToLogin: () => endSession(), draft, rememberDraft: setDraft, onboarding, dispatchOnboarding, profile, rememberProfile: setProfile, completeOnboarding }}>{children}</Context.Provider>;
}
export function usePivotSession() {
  const value = useContext(Context);
  if (!value) throw new Error("PivotSessionProvider is required.");
  return value;
}
