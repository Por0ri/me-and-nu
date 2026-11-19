"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { AdminContentDetail } from "@/lib/admin-content";
import { reviewAvailability, reviewRationaleError, type ReviewAction } from "@/lib/admin-content-review";
import { MAX_RECHECKS, reviewFlowFor, type ReviewFlow, type ReviewFlowState } from "@/lib/admin-review-flow";
import styles from "./admin.module.css";

const labels: Record<ReviewAction, string> = { approve: "승인", reject: "반려" };

// `flow` is a test seam only; production renders use the registry flow and its code-only gate.
export function ContentReviewPanel({ content, onReload, flow: injected }: { content: AdminContentDetail; onReload?: () => void; flow?: ReviewFlow }) {
  const flow = injected ?? reviewFlowFor(content.contentId);
  const state = useSyncExternalStore(flow.subscribe, flow.state, flow.state);
  const gate = flow.writeGate();
  const dialog = useRef<HTMLDialogElement>(null);
  const [action, setAction] = useState<ReviewAction>("approve");
  const [rationale, setRationale] = useState("");
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Server review metadata gates the confirmations; an unresolved request blocks a new one.
  const availability = reviewAvailability(content);
  const busy = state.phase === "submitting" || state.phase === "uncertain";
  const allowed = (next: ReviewAction) => !busy && availability.allowedActions.includes(next);
  const label = labels[action];
  const clear = () => { setRationale(""); setChecked(false); setError(null); };
  const open = (next: ReviewAction) => { if (!allowed(next)) return; clear(); setAction(next); dialog.current?.showModal(); };
  const canExecute = gate.enabled && checked && allowed(action);
  const execute = () => {
    if (!canExecute) return;
    const value = rationale;
    dialog.current?.close(); // Progress and the result show in the panel; closing never cancels the request.
    void flow.execute(content, action, value);
  };

  useEffect(() => { flow.setReload(onReload ?? null); return () => flow.setReload(null); }, [flow, onReload]);
  useEffect(() => {
    if (state.phase !== "submitting") return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault(); // Leaving now would hide the result, not cancel it.
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [state.phase]);

  return <section className={styles.reviewPanel} aria-label="콘텐츠 검수 조치">
    <h3>콘텐츠 검수</h3>
    <p className={styles.note}>{gate.enabled
      ? "승인·반려는 서버가 HTTP 200으로 확인한 경우에만 완료로 표시합니다. 최종 권한과 상태 검사는 서버가 합니다."
      : "승인·반려 실행은 아직 연결되지 않았습니다. 조치 내용과 사유만 확인할 수 있으며 저장되지 않습니다."}</p>
    {availability.message && <p role="status">{availability.message}</p>}
    <div className={styles.formActions}>
      <button className={styles.primaryButton} type="button" disabled={!allowed("approve")} onClick={() => open("approve")}>승인 내용 확인</button>
      <button className={styles.secondaryButton} type="button" disabled={!allowed("reject")} onClick={() => open("reject")}>반려 내용 확인</button>
    </div>
    <ReviewStatus state={state} flow={flow} rechecksAllowed={gate.enabled} />
    <dialog ref={dialog} className={styles.reviewDialog} aria-labelledby="review-title" aria-describedby="review-effect" onClose={clear}>
      <h2 id="review-title">콘텐츠 {label} 확인</h2>
      <p>#{content.contentId} · {content.title || "제목 없음"}</p>
      <p id="review-effect">{action === "approve"
        ? "승인 시 검수 상태는 confirmed, 콘텐츠 상태는 active로 변경되며 연결된 초안은 published로 처리됩니다. 미리보기 표시를 제거하며, 기존 발행 시각이 있으면 유지합니다. 실제 노출은 발행 시각과 기존 피드 조건에 따라 결정됩니다."
        : "반려 시 검수 상태는 failed, 콘텐츠 상태는 hidden으로 변경되며 연결된 초안은 discarded로 처리됩니다."}</p>
      <form onSubmit={event => { event.preventDefault(); const issue = reviewRationaleError(rationale); setError(issue); setChecked(!issue); }}>
        <div className={styles.formField}>
          <label htmlFor="review-rationale">조치 사유 (필수)</label>
          <textarea autoFocus id="review-rationale" rows={5} value={rationale} aria-invalid={Boolean(error)} aria-describedby="review-rationale-help review-rationale-error"
            onChange={event => { setRationale(event.target.value); setChecked(false); setError(null); }} />
          <p id="review-rationale-help" className={styles.fieldHint}>공백을 제외한 빈 사유는 사용할 수 없습니다. 최대 2,000자.</p>
          <p id="review-rationale-error" className={styles.fieldError} role="alert">{error}</p>
        </div>
        <p className={styles.inputNotice} role="status">{!gate.enabled
          ? (checked ? "입력 확인 완료 · 미저장. 콘텐츠 상태는 변경되지 않았습니다." : "확인 단계 · 미저장. 실행 기능 연결 전에는 저장할 수 없습니다.")
          : (checked ? `입력 확인 완료. '${label} 실행'을 누르면 서버에 한 번 요청합니다.` : "사유를 입력한 뒤 '입력 확인'을 눌러 주세요.")}</p>
        <div className={styles.formActions}>
          <button type="button" className={styles.secondaryButton} onClick={() => dialog.current?.close()}>취소</button>
          <button type="submit" className={styles.secondaryButton}>입력 확인</button>
          <button type="button" className={styles.primaryButton} disabled={!canExecute} onClick={execute}>{label} 실행{gate.enabled ? "" : " (미연결)"}</button>
        </div>
      </form>
    </dialog>
  </section>;
}

function ReviewStatus({ state, flow, rechecksAllowed }: { state: ReviewFlowState; flow: ReviewFlow; rechecksAllowed: boolean }) {
  if (state.phase === "idle") return null;
  if (state.phase === "blocked") {
    return <div className={styles.inputNotice} role="status" data-review-phase="blocked"><p>{state.message}</p>
      <button type="button" className={styles.secondaryButton} onClick={flow.release}>닫기</button></div>;
  }
  const label = labels[state.request.action];
  const requestNumber = <p>요청 번호: <code>{state.request.requestId}</code></p>;
  if (state.phase === "submitting") {
    return <div className={styles.inputNotice} role="status" aria-live="polite" aria-busy="true" data-review-phase="submitting">
      <p>{state.recheck ? `같은 요청으로 ${label} 결과를 다시 확인하는 중입니다.` : `${label} 요청을 처리하는 중입니다.`} 서버 응답을 받기 전에는 완료로 표시하지 않습니다.</p>
      {requestNumber}</div>;
  }
  if (state.phase === "succeeded") {
    const { result } = state;
    return <div className={styles.inputNotice} role="status" aria-live="polite" data-review-phase="succeeded">
      <p><strong>{label} 완료</strong> · 감사 기록 #{result.auditLogId}</p>
      {result.replayed && <p>이미 처리된 같은 요청의 결과를 다시 받았습니다. 새로 변경하지 않았습니다.</p>}
      <p>처리 결과: 콘텐츠 {result.status} · 검수 {result.judgmentStatus} · 초안 {result.draftStatus}</p>
      <p>현재 상태는 상세를 다시 조회해 표시합니다.</p>
      {requestNumber}
      <button type="button" className={styles.secondaryButton} onClick={flow.release}>닫기</button></div>;
  }
  if (state.phase === "uncertain") {
    const exhausted = state.rechecks >= MAX_RECHECKS;
    return <div className={styles.inputNotice} role="status" aria-live="polite" data-review-phase="uncertain">
      <p>{state.message}</p>
      <p>{label} 요청의 처리 여부를 아직 알 수 없습니다. 다시 확인할 때는 같은 요청 번호와 같은 내용으로만 보냅니다.</p>
      {exhausted && <p>다시 확인 한도({MAX_RECHECKS}회)에 도달했습니다. 상세 상태만으로는 이 요청의 처리 여부를 확정할 수 없으니, 필요하면 아래 요청 번호로 BE 감사 기록을 확인해 주세요.</p>}
      {requestNumber}
      <div className={styles.formActions}>
        <button type="button" className={styles.primaryButton} disabled={exhausted || !rechecksAllowed} onClick={() => void flow.recheck()}>
          같은 요청으로 다시 확인 ({state.rechecks}/{MAX_RECHECKS})</button>
        <button type="button" className={styles.secondaryButton} onClick={flow.release}>확인 중단하고 상세 다시 조회</button>
      </div></div>;
  }
  const { failure } = state;
  return <div className={styles.inputNotice} role="status" aria-live="polite" data-review-phase="stopped">
    <p>{failure.message}</p>
    {failure.status === "conflict" && <p>자동으로 다시 실행하지 않습니다. 최신 상태와 권한을 다시 조회했으니 확인한 뒤 새로 판단해 주세요.</p>}
    {requestNumber}
    <button type="button" className={styles.secondaryButton} onClick={flow.release}>닫기</button></div>;
}
