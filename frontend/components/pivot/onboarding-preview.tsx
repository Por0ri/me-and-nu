"use client";
import { useReducer } from "react";
import { initialOnboardingState, onboardingReducer, type OnboardingStep } from "@/lib/pivot/onboarding";
import { OnboardingFlow } from "./onboarding-screens";
import { demoWorks, demoOptions } from "@/lib/pivot/onboarding-catalog";

export function OnboardingPreview({ screen, selected }: { screen: OnboardingStep; selected: boolean }) {
  // Design-only fixtures. Never passed to the authenticated service route.
  const [state, dispatch] = useReducer(onboardingReducer, {
    ...initialOnboardingState(), step: screen,
    interest: screen === "interest" && !selected ? null : "animation",
    topicId: 900,
    favoriteWorks: screen !== "interest" && (screen !== "q1" || selected) ? [demoWorks[0]] : [],
    preferenceOptions: demoOptions.filter(tag => tag.sourceEntityIds.includes(901)),
    preferenceTagIds: screen === "result" || screen === "q3" || selected && screen === "q2" ? [911, 913] : [],
    optionsKey: "901",
    consumptionType: screen === "result" || selected && screen === "q3" ? "ost" : null,
  });
  return <OnboardingFlow state={state} dispatch={dispatch} mode="preview" chrome onExit={() => dispatch({ type: "reset" })} />;
}
