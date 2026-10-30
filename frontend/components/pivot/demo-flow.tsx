"use client";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { demoPolicies, type DemoState } from "@/lib/pivot/demo";
import { initialOnboardingState, isStateComplete, onboardingReducer } from "@/lib/pivot/onboarding";
import { mockHomeSource } from "@/lib/pivot/home-mock";
import { mockDetailSourceForPreview } from "@/lib/pivot/detail-mock";
import { mockMyPageSource } from "@/lib/pivot/my-page-mock";
import { createDemoSavedSource } from "@/lib/pivot/saved-mock";
import type { ConsentDraft } from "@/lib/pivot/consent";
import { SplashScreen } from "./splash-screen";
import { LoginScreen } from "./login-screen";
import { ConsentScreen } from "./consent-screen";
import { OnboardingFlow } from "./onboarding-screens";
import { HomeDetailExperience } from "./home-detail-experience";
import { DemoLoadingScreen } from "./demo-loading-screen";
import { useConsent } from "./use-consent";
import styles from "./pivot.module.css";

export function DemoFlow({ initialHome = false, detailPreview }: { initialHome?: boolean; detailPreview?: string }) {
  const [state, setState] = useState<DemoState>({ step: initialHome ? "home" : "splash", consent: null });
  const [savedSource] = useState(createDemoSavedSource);
  const [onboarding, dispatch] = useReducer(onboardingReducer, undefined, initialOnboardingState);
  const consent = useConsent(demoPolicies);
  const region = useRef<HTMLDivElement>(null);
  const showLogin = useCallback(() => setState((previous) => previous.step === "splash" ? { ...previous, step: "login" } : previous), []);
  function acceptConsent(draft: ConsentDraft) {
    setState((previous) => previous.step === "consent" ? { step: "onboarding", consent: draft } : previous);
  }
  function restart() {
    savedSource.reset();
    window.history.replaceState(null, "", window.location.pathname);
    consent.toggleAll(false); dispatch({ type: "reset" });
    setState({ step: "splash", consent: null });
  }
  function showLoading() {
    if (onboarding.step !== "result" || !isStateComplete(onboarding)) return;
    setState((previous) => previous.step === "onboarding" ? { ...previous, step: "loading" } : previous);
  }
  const showHome = useCallback(() => setState((previous) => previous.step === "loading" ? { ...previous, step: "home" } : previous), []);
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
    region.current?.querySelector<HTMLHeadingElement>("h1")?.focus({ preventScroll: true });
  }, [state.step]);
  return <div className={styles.demoRoot}>
    <aside className={styles.demoBanner} aria-label="시연용 데모 안내"><strong>시연용 데모</strong><span>{state.step === "home" ? "실제 추천 아님 · 가상 프로필 · 서버 저장 없음" : "실제 로그인·가입·서버 저장 없음"}</span></aside>
    <div ref={region}>
      {state.step === "onboarding" ? [1, 2, 3].map((phase) => <link key={phase} rel="preload" as="image" href={`/pivot/loading/phase-${phase}.png`} fetchPriority={phase === 1 ? "high" : "low"} />) : null}
      {state.step === "splash" ? <SplashScreen onContinue={showLogin} />
        : state.step === "login" ? <LoginScreen onDemoContinue={() => setState((previous) => previous.step === "login" ? { ...previous, step: "consent" } : previous)} />
        : state.step === "consent" ? <ConsentScreen model={consent} onContinue={acceptConsent} />
        : state.step === "home" ? <HomeDetailExperience homeSource={mockHomeSource} detailSource={mockDetailSourceForPreview(detailPreview)} mySource={mockMyPageSource} savedSource={savedSource} demoBookmarks={savedSource} onRestart={restart} />
        : state.step === "loading" ? <DemoLoadingScreen onComplete={showHome} onRestart={restart} />
        : <OnboardingFlow state={onboarding} dispatch={dispatch} mode="demo" onExit={() => setState((previous) => ({ ...previous, step: "consent" }))} onRestart={restart} onDemoHome={showLoading} />}
    </div>
  </div>;
}
