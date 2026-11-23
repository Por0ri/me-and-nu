"use client";

import { useRef, useState, useSyncExternalStore } from "react";
import { rawReportStatuses, type ReportRecord, type SupportAction } from "@/lib/admin-reports";
import { supportAvailability, supportLabels, supportTextError } from "@/lib/admin-support-write";
import { MAX_SUPPORT_RECHECKS, supportNotices, serverPendingSupportFlows, subscribeSupportChanges, supportFlowFor, type SupportFlow, type SupportFlowState } from "@/lib/admin-support-flow";
import shared from "./admin.module.css";
import styles from "./reports.module.css";

export function SupportActionPanel({ report, flow: provided }: { report: ReportRecord; flow?: SupportFlow }) {
  const flow = provided ?? supportFlowFor(report);
  const state = useSyncExternalStore(flow.subscribe, flow.state, flow.state);
  const availability = supportAvailability(report);
  const [action, setAction] = useState<SupportAction>(report.kind === "content" ? "start_review" : "answer");
  const [text, setText] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [issue, setIssue] = useState<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const busy = state.phase === "submitting" || state.phase === "uncertain";
  const allowed = (next: SupportAction) => !busy && availability.allowed.includes(next);
  const close = () => { dialog.current?.close(); setText(""); setConfirmed(false); setIssue(null); };
  const open = (next: SupportAction) => {
    if (!allowed(next)) return;
    setAction(next); setText(""); setConfirmed(false); setIssue(null); dialog.current?.showModal();
  };
  return <section className={styles.writePanel} aria-label={report.kind === "content" ? "신고 처리" : "문의 답변"}>
    <h3>{report.kind === "content" ? "신고 처리" : "문의 답변"}</h3>
    {availability.message && <p className={styles.availability}>{availability.message}</p>}
    <div className={styles.writeButtons}>{(report.kind === "content" ? ["start_review", "resolve", "reject"] as const : ["answer"] as const).map(next =>
      <button key={next} type="button" className={next === "reject" ? shared.secondaryButton : shared.primaryButton} disabled={!allowed(next)} onClick={() => open(next)}>
        {next === "answer" ? "답변 작성" : supportLabels[next]}</button>)}</div>
    {busy && <p className={styles.availability}>처리 결과를 확인하고 있습니다. 상단의 결과 확인 안내를 확인해 주세요.</p>}
    {state.phase === "blocked" && <SupportActionStatus state={state} flow={flow} />}
    <dialog ref={dialog} className={`${shared.reviewDialog} ${styles.writeDialog}`} aria-labelledby="support-action-title" aria-describedby="support-action-effect"
      onCancel={event => { event.preventDefault(); close(); }}>
      <h2 id="support-action-title">{report.number} · {supportLabels[action]} 확인</h2>
      <p id="support-action-effect">{action === "answer" ? "등록한 답변은 문의한 회원의 문의 내역에 표시됩니다. 등록 후에는 수정할 수 없습니다."
        : action === "start_review" ? "이 신고를 검토 중으로 변경합니다."
        : action === "resolve" ? "신고를 해결 완료로 기록합니다. 콘텐츠 공개 상태는 별도의 콘텐츠 관리에서 변경해 주세요."
        : "이 신고를 반려하고 처리를 종료합니다."}</p>
      <form onSubmit={event => { event.preventDefault(); const error = supportTextError(text, action); setIssue(error); setConfirmed(!error); }}>
        <label htmlFor="support-action-text">{action === "answer" ? "답변 내용" : "처리 사유"} (필수)</label>
        <textarea id="support-action-text" autoFocus rows={action === "answer" ? 8 : 5} value={text} aria-invalid={Boolean(issue)}
          aria-describedby="support-action-help support-action-error" onChange={event => { setText(event.target.value); setConfirmed(false); setIssue(null); }} />
        <p id="support-action-help" className={styles.availability}>{action === "answer" ? "최대 5,000자. 문의한 회원에게 보여줄 내용만 작성해 주세요." : "최대 2,000자. 처리 근거를 기록해 주세요."}</p>
        <p id="support-action-error" className={shared.fieldError} role="alert">{issue}</p>
        {confirmed && <div className={styles.confirmation} role="status"><strong>{action === "answer" ? "등록할 답변" : "기록할 처리 사유"}</strong><p>{text.trim()}</p></div>}
        <div className={styles.writeButtons}>
          <button type="button" className={shared.secondaryButton} onClick={close}>취소</button>
          <button type="submit" className={shared.secondaryButton}>내용 확인</button>
          <button type="button" className={shared.primaryButton} disabled={!confirmed || !allowed(action)} onClick={() => {
            if (!confirmed || !allowed(action)) return;
            const value = text; close(); void flow.execute(report, action, value);
          }}>{supportLabels[action]} 확정</button>
        </div>
      </form>
    </dialog>
  </section>;
}

export function SupportActionStatus({ state, flow }: { state: SupportFlowState; flow: SupportFlow }) {
  if (state.phase === "idle") return null;
  if (state.phase === "blocked") return <div className={styles.writeStatus} role="status" data-support-phase="blocked">{state.message}</div>;
  if (state.phase === "succeeded") return <div className={styles.writeStatus} role="status" data-support-phase="succeeded">
    <strong>{supportLabels[state.result.action]} 완료</strong><p>현재 상태: {rawReportStatuses[state.result.rawStatus]}</p>
    {state.result.replayed && <p>이미 처리된 요청을 확인했습니다. 중복 처리하지 않았습니다.</p>}
    <button type="button" className={shared.secondaryButton} onClick={flow.release}>닫기</button>
  </div>;
  return <div className={styles.writeStatus} role="status" aria-live="polite" aria-busy={state.phase === "submitting"} data-support-phase={state.phase}>
    {state.phase === "submitting" ? <p>{state.rechecks ? "같은 요청의 처리 결과를 확인하고 있습니다." : "처리하고 있습니다."}</p>
      : state.phase === "uncertain" ? <><p>{state.message}</p><p>저장 여부가 확인될 때까지 새 요청을 만들지 않습니다.</p>
        <button type="button" className={shared.primaryButton} disabled={state.rechecks >= MAX_SUPPORT_RECHECKS} onClick={() => void flow.recheck()}>같은 요청으로 다시 확인 ({state.rechecks}/{MAX_SUPPORT_RECHECKS})</button>
        {state.rechecks >= MAX_SUPPORT_RECHECKS && <p>다시 확인 한도에 도달했습니다. 아래 요청 번호로 관리자 처리 기록을 확인해 주세요.</p>}</>
      : <><p>{state.failure.message}</p>{state.failure.reload && <p>최신 상태를 다시 조회합니다. 내용을 확인한 뒤 다시 판단해 주세요.</p>}
        <button type="button" className={shared.secondaryButton} onClick={flow.release}>닫기</button></>}
    <p className={styles.requestNumber}>요청 번호: {state.request.requestId}</p>
  </div>;
}

export function PendingSupportNotice() {
  const pending = useSyncExternalStore(subscribeSupportChanges, supportNotices, serverPendingSupportFlows);
  if (!pending.length) return null;
  return <aside className={styles.pending} aria-label="처리 결과 확인 중인 신고·문의">
    <h2>처리 결과</h2>{pending.some(flow => ["submitting", "uncertain"].includes(flow.state().phase)) && <p>화면이나 조회 조건을 바꿔도 같은 요청으로 결과를 확인할 수 있습니다.</p>}
    {pending.map(flow => <div key={flow.item.id}><strong>{flow.item.id}</strong><SupportActionStatus flow={flow} state={flow.state()} /></div>)}
  </aside>;
}
