"use client";

import { useCallback, useEffect, useState } from "react";
import type { Policy } from "@/lib/api";
import { consentSummary, createConsentDraft, policyKey, reconcileSelection, selectAll, type ConsentSelection, type ConsentDraft } from "@/lib/pivot/consent";
import { loadPivotPolicies } from "@/lib/pivot/services";

export function useConsent(fixture?: Policy[], initialDraft?: ConsentDraft) {
  const [policies, setPolicies] = useState<Policy[]>(fixture ?? []);
  const [selection, setSelection] = useState<ConsentSelection>(() => Object.fromEntries((initialDraft?.consents ?? []).map((item) => [policyKey(item), item.agreed === true])));
  const [status, setStatus] = useState<"loading" | "ready" | "error">(fixture ? "ready" : "loading");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [reachedBoundary, setReachedBoundary] = useState(false);
  useEffect(() => {
    if (fixture) return;
    let current = true;
    loadPivotPolicies().then(
      (items) => { if (current) { setPolicies(items); setSelection((previous) => reconcileSelection(items, previous)); setStatus("ready"); } },
      (reason) => { if (current) { setStatus("error"); setError(reason instanceof Error ? reason.message : "약관을 불러오지 못했어요."); } },
    );
    return () => { current = false; };
  }, [fixture, attempt]);
  const reload = useCallback(() => {
    setStatus("loading"); setError(""); setReachedBoundary(false); setAttempt((value) => value + 1);
  }, []);
  const summary = consentSummary(policies, selection);
  function toggle(policy: Policy, agreed: boolean) {
    setReachedBoundary(false);
    setSelection((previous) => ({ ...previous, [policyKey(policy)]: agreed }));
  }
  function toggleAll(agreed: boolean) {
    setReachedBoundary(false); setSelection(selectAll(policies, agreed));
  }
  function continueDraft() {
    if (status !== "ready") throw new Error("약관을 확인해 주세요.");
    const draft = createConsentDraft(policies, selection);
    setReachedBoundary(true);
    return draft;
  }
  return { policies, selection, status, error, reload, summary, toggle, toggleAll, continueDraft, reachedBoundary };
}
