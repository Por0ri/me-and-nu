"use client";
// My Page taste edit (temporary, 2026-11 user test): the onboarding Q1~Q3 screens opened with the saved answers.
import Link from "next/link";
import { useEffect, useReducer, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api";
import { buildSelectionDraft, initialOnboardingState, isStateComplete, selectionSummary, type OnboardingState } from "@/lib/pivot/onboarding";
import { isActiveSession } from "@/lib/pivot/services";
import { loadMyTaste, saveMyTaste, tasteCatalog, tasteEditReducer, tasteEditState } from "@/lib/pivot/taste-edit";
import { usePivotSession } from "./pivot-session";
import { OnboardingFlow } from "./onboarding-screens";
import { PivotBrand, PivotFrame, PrimaryButton } from "./pivot-ui";
import pivotStyles from "./pivot.module.css";
import styles from "./onboarding.module.css";

const MY_PAGE = "/pivot/home?view=my";

function TasteEditResult({ state, saving, error, onSave, onEdit }: { state: OnboardingState; saving: boolean; error: string; onSave: () => void; onEdit: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { window.scrollTo({ top: 0, behavior: "instant" }); heading.current?.focus({ preventScroll: true }); }, []);
  const summary = selectionSummary(state);
  return <PivotFrame decorated={false} className={styles.frame}>
    <section className={styles.resultBody} aria-labelledby="taste-result-heading">
      <p className={styles.resultMode}>바꾼 취향</p>
      <h1 id="taste-result-heading" ref={heading} tabIndex={-1}>이대로<br />저장할까요?</h1>
      <div className={styles.summary} aria-label="바꾼 취향 요약">
        <div className={styles.summaryRows}>
          <div className={styles.summaryRow}><span className={styles.summaryLabel}>좋아하는 작품</span><strong className={styles.answerTag}>{summary.works}</strong></div>
          <div className={styles.summaryRow}><span className={styles.summaryLabel}>장르·소재</span><strong className={styles.answerTag}>{summary.tags}</strong></div>
          <div className={styles.summaryRow}><span className={styles.summaryLabel}>소비 유형</span><strong className={styles.answerTag}>{summary.consumption}</strong></div>
        </div>
      </div>
      <p className={styles.connectionNotice}>저장하면 바뀐 취향으로 오늘 피드를 다시 만들어 드려요.</p>
    </section>
    <div className={styles.actions}>
      <button type="button" className={styles.reselect} onClick={onEdit} disabled={saving}>다시 고르기</button>
      <PrimaryButton disabled={!isStateComplete(state) || saving} onClick={onSave}>{saving ? "저장하고 있어요…" : "저장하고 피드 보기"}</PrimaryButton>
      <p className={styles.homeFeedback} role={error ? "alert" : "status"} aria-live="polite">{error}</p>
    </div>
  </PivotFrame>;
}

export function TasteEditRoute() {
  const router = useRouter();
  const { state: session, refresh } = usePivotSession();
  const [state, dispatch] = useReducer(tasteEditReducer, undefined, initialOnboardingState);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
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
    try {
      await saveMyTaste(buildSelectionDraft(state));
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
  if (!active || phase !== "ready" || leaving) return <PivotFrame>
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
  return <OnboardingFlow state={state} dispatch={dispatch} mode="service" catalog={tasteCatalog} onExit={() => router.replace(MY_PAGE)}
    submission={{ submitting: saving, error, onSubmit: () => void save(), onEditProfile: () => router.replace(MY_PAGE) }} />;
}
