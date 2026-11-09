"use client";
// My Page taste edit (temporary, 2026-11 user test): the onboarding Q1~Q3 screens opened with the saved answers.
import Link from "next/link";
import { useEffect, useReducer, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api";
import { buildSelectionDraft, initialOnboardingState, isStateComplete, type OnboardingState } from "@/lib/pivot/onboarding";
import { isActiveSession } from "@/lib/pivot/services";
import { loadMyTaste, loadTasteHints, saveMyTaste, tasteCatalog, tasteEditReducer, tasteEditState, type TasteHints } from "@/lib/pivot/taste-edit";
import { usePivotSession } from "./pivot-session";
import { OnboardingFlow, ResultCriteria, ResultDecorations } from "./onboarding-screens";
import { warmUpFeed } from "@/lib/pivot/feed-warmup";
import { FeedLoadingScreen } from "./feed-loading-screen";
import { tasteQuestionCopy } from "./taste-hints";
import { PivotBrand, PivotFrame, PrimaryButton } from "./pivot-ui";
import pivotStyles from "./pivot.module.css";
import styles from "./onboarding.module.css";

const MY_PAGE = "/pivot/home?view=my";

function TasteEditResult({ state, saving, error, onSave, onEdit }: { state: OnboardingState; saving: boolean; error: string; onSave: () => void; onEdit: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { window.scrollTo({ top: 0, behavior: "instant" }); heading.current?.focus({ preventScroll: true }); }, []);
  return <PivotFrame decorated={false} className={styles.frame}>
    <ResultDecorations />
    <section className={styles.resultBody} aria-labelledby="taste-result-heading">
      <h1 id="taste-result-heading" ref={heading} tabIndex={-1}>피드를 새롭게<br />구성했어요!</h1>
      <ResultCriteria state={state} title="수정된 추천 기준" label="바꾼 취향 요약" />
    </section>
    <div className={styles.actions}>
      <button type="button" className={styles.reselect} onClick={onEdit} disabled={saving}>다시 선택하기</button>
      <PrimaryButton disabled={!isStateComplete(state) || saving} onClick={onSave}>{saving ? "저장하고 있어요…" : "새로운 피드 보러가기"}</PrimaryButton>
      <p className={styles.homeFeedback} role={error ? "alert" : "status"} aria-live="polite">{error}</p>
    </div>
  </PivotFrame>;
}

export function TasteEditRoute() {
  const router = useRouter();
  const { state: session, refresh } = usePivotSession();
  const [state, dispatch] = useReducer(tasteEditReducer, undefined, initialOnboardingState);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  // Hints from what the member reacted to; they arrive after the saved answers and never hold up the edit.
  const [hints, setHints] = useState<TasteHints | null>(null);
  const [saving, setSaving] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState("");
  // A quick load shows only the background; the waiting page appears when it takes longer than 0.5 s.
  const [slow, setSlow] = useState(false);
  useEffect(() => { const timer = setTimeout(() => setSlow(true), 500); return () => clearTimeout(timer); }, []);
  const lock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const active = isActiveSession(session.session);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    loadMyTaste().then((taste) => {
      if (cancelled) return;
      dispatch({ type: "load", state: tasteEditState(taste) }); setPhase("ready");
      void loadTasteHints(taste.topicId).then((found) => { if (!cancelled) setHints(found); });
    }).catch((reason) => {
      if (cancelled) return;
      if (reason instanceof ApiError && reason.status === 401) { refresh(); router.replace("/pivot/login"); return; }
      setPhase("error");
    });
    return () => { cancelled = true; };
  }, [active, refresh, router]);
  // Back from Q1 leaves without saving.
  const leaving = phase === "ready" && state.step === "interest";
  useEffect(() => { if (leaving) router.replace(MY_PAGE); }, [leaving, router]);

  async function save() {
    if (lock.current || !isStateComplete(state)) return;
    lock.current = true; setSaving(true); setError("");
    const since = Date.now();
    try {
      await saveMyTaste(buildSelectionDraft(state));
      // Saving drops the recent rounds, so the next feed waits for a new one: the loading screen covers it.
      if (mounted.current) setPreparing(true);
      await warmUpFeed(state.topicId, since);
      if (mounted.current) router.replace("/pivot/home");
    } catch (reason) {
      if (!mounted.current) return;
      if (reason instanceof ApiError && reason.status === 401) { refresh(); router.replace("/pivot/login"); return; }
      if (reason instanceof ApiError && reason.code === "VALIDATION_ERROR") {
        setError("선택한 작품과 장르·소재를 다시 확인해주세요."); dispatch({ type: "edit-again" });
      } else {
        setError("취향을 저장하지 못했어요. 고른 내용은 그대로 있어요. 잠시 후 다시 시도해주세요.");
      }
    } finally {
      lock.current = false;
      if (mounted.current) setSaving(false);
    }
  }
  if (preparing) return <FeedLoadingScreen />;
  if (leaving || (!slow && phase === "loading" && (active || session.status === "loading"))) return <PivotFrame decorated={false}>{null}</PivotFrame>;
  if (!active || phase !== "ready") return <PivotFrame>
    <PivotBrand screen="consent" className={pivotStyles.consentBrand} />
    <section className={pivotStyles.gate}><h1>취향 수정하기</h1>
      <p role={phase === "error" ? "alert" : "status"}>{session.status === "loading" ? "로그인 상태를 확인하고 있어요."
        : !active ? "로그인한 뒤 취향을 수정할 수 있어요." : leaving ? "마이페이지로 돌아가고 있어요."
        : phase === "error" ? "내 취향을 불러오지 못했어요. 잠시 후 다시 시도해주세요." : "내 취향을 불러오고 있어요."}</p>
      <Link href={MY_PAGE}>마이페이지로 돌아가기</Link>
    </section>
  </PivotFrame>;
  if (state.step === "result") return <TasteEditResult state={state} saving={saving} error={error} onSave={() => void save()}
    onEdit={() => { setError(""); dispatch({ type: "edit-again" }); }} />;
  return <OnboardingFlow state={state} dispatch={dispatch} mode="service" catalog={tasteCatalog} copy={tasteQuestionCopy(hints)} onExit={() => router.replace(MY_PAGE)}
    submission={{ submitting: saving, error, onSubmit: () => void save() }} />;
}
