"use client";
import { useEffect, useRef, useState, type Dispatch } from "react";
import { canAdvance, type OnboardingAction, type OnboardingState } from "@/lib/pivot/onboarding";

export function useOnboardingNavigation(state: OnboardingState, dispatch: Dispatch<OnboardingAction>, onExit: () => void) {
  const lock = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  function navigate(action: "advance" | "back" | "reselect") {
    if (lock.current || (action === "advance" && !canAdvance(state)) || (action === "reselect" && state.step !== "result")) return;
    lock.current = true; setBusy(true);
    timer.current = setTimeout(() => { lock.current = false; setBusy(false); }, 200);
    if (action === "back" && state.step === "interest") onExit();
    else if (action === "reselect") dispatch({ type: "reselect", from: "result" });
    else dispatch({ type: action, from: state.step });
  }
  return { busy, advance: () => navigate("advance"), back: () => navigate("back"), reselect: () => navigate("reselect") };
}
