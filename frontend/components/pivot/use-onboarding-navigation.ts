"use client";
import { useEffect, useRef, useState, type Dispatch } from "react";
import { canAdvance, type OnboardingAction, type OnboardingState } from "@/lib/pivot/onboarding";

export function useOnboardingNavigation(state: OnboardingState, dispatch: Dispatch<OnboardingAction>, onExit: () => void) {
  const lock = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  function navigate(back: boolean) {
    if (lock.current || (!back && !canAdvance(state))) return;
    lock.current = true; setBusy(true);
    timer.current = setTimeout(() => { lock.current = false; setBusy(false); }, 200);
    if (back && state.step === "interest") onExit();
    else dispatch({ type: back ? "back" : "advance", from: state.step });
  }
  return { busy, advance: () => navigate(false), back: () => navigate(true) };
}
