"use client";
import { useReducer } from "react";
import { initialOnboardingState, onboardingReducer, type OnboardingStep } from "@/lib/pivot/onboarding";
import { OnboardingFlow } from "./onboarding-screens";

export function OnboardingPreview({ screen, selected }: { screen: OnboardingStep; selected: boolean }) {
  // Design-only fixtures. Never passed to the authenticated service route.
  const [state, dispatch] = useReducer(onboardingReducer, {
    ...initialOnboardingState(), step: screen,
    interest: screen === "interest" && !selected ? null : "animation",
    q1: screen === "result" || screen === "q2" || selected && screen === "q1" ? "ost" : null,
    q2: screen === "result" || selected && screen === "q2" ? "events" : null,
  });
  return <OnboardingFlow state={state} dispatch={dispatch} mode="preview" chrome onExit={() => dispatch({ type: "reset" })} />;
}
