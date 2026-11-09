"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createReportController, initialReportState, REPORT_LIMIT, reportLength, reportLocked, reportReasons, reportValidation, type ReportSource, type ReportState } from "@/lib/pivot/report";
import { useReportViewport } from "./report-viewport";
import styles from "./report.module.css";

export function ReportDialog({ contentId, source, demo, onClose }: { contentId: string; source?: ReportSource; demo: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), title = useRef<HTMLHeadingElement>(null);
  const id = useId();
  const [step, setStep] = useState<"reason" | "detail">("reason");
  const [request, setRequest] = useState<{ source?: ReportSource; demo: boolean; contentId: string; state: ReportState; control: ReturnType<typeof createReportController> }>();
  useEffect(() => {
    const control = createReportController(source, demo, Number(contentId), state => setRequest({ source, demo, contentId, state, control }));
    setRequest({ source, demo, contentId, state: initialReportState(), control });
    return () => control.cancel();
  }, [source, demo, contentId]);
  const current = request?.source === source && request?.demo === demo && request?.contentId === contentId ? request : undefined;
  const state = current?.state ?? initialReportState();
  const done = state.status === "received", busy = state.status === "submitting", locked = reportLocked(state.status);
  const length = reportLength(state.detail);
  useEffect(() => {
    const element = dialog.current, trigger = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element?.showModal(); title.current?.focus({ preventScroll: true });
    return () => {
      element?.close(); document.body.style.overflow = previousOverflow;
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => { dialog.current?.scrollTo(0, 0); title.current?.focus({ preventScroll: true }); }, [step, done]);
  useReportViewport(dialog);
  return <dialog ref={dialog} className={styles.dialog} aria-modal="true" aria-labelledby={`${id}-title`} data-report-state={state.status}
    onCancel={event => { event.preventDefault(); onClose(); }}
    onKeyDown={event => {
      if (event.key !== "Tab") return;
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled)'))
        .filter(element => !element.closest("fieldset:disabled"))
        .filter(element => !(element instanceof HTMLInputElement) || element.type !== "radio" ||
          element === (event.currentTarget.querySelector('input[type="radio"]:checked') ?? event.currentTarget.querySelector('input[type="radio"]')));
      const index = controls.indexOf(document.activeElement as HTMLElement);
      event.preventDefault();
      const next = index < 0 ? (event.shiftKey ? controls.length - 1 : 0) : (index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
      controls[next]?.focus();
    }}>
    <form className={styles.form} onSubmit={event => { event.preventDefault(); if (step === "reason") { if (state.reasonCode) setStep("detail"); } else void current?.control.submit(); }} aria-busy={busy}>
      <header className={styles.header}>
        <h2 ref={title} id={`${id}-title`} tabIndex={-1}>{done ? "신고 접수 완료" : step === "reason" ? "콘텐츠 신고" : "상세 신고 내용"}</h2>
        {!done ? <p>{step === "reason" ? "신고 사유를 선택해주세요." : <>자세한 내용을 알려주세요.<span className={styles.optional}>선택</span></>}</p> : null}
        {!done && step === "detail" && !locked ? <button type="button" className={styles.back} aria-label="신고 사유로 돌아가기" onClick={() => setStep("reason")}><img src="/pivot/report/back.svg" width={24} height={24} alt="" /></button> : null}
        {!done ? <button type="button" className={styles.close} aria-label="신고 닫기" onClick={onClose}><img src="/pivot/report/close.svg" width={24} height={24} alt="" /></button> : null}
      </header>
      {done ? <div className={styles.receipt} role="status"><p>신고가 정상적으로 접수되었어요!</p><p>보내주신 신고 내용은 확인 후<br />필요한 조치를 진행할게요.</p></div>
        : step === "reason" ? <fieldset className={styles.reasons} disabled={!current || locked}>
          <legend className={styles.srOnly}>신고 사유</legend>
          {reportReasons.map(reason => <label className={styles.option} key={reason.value}>
            <input type="radio" name="reportReason" value={reason.value} checked={state.reasonCode === reason.value} onChange={() => current?.control.change({ reasonCode: reason.value })} />
            <span>{reason.label}<i aria-hidden="true" /></span>
          </label>)}
        </fieldset> : <>
          <p className={styles.selected}>{reportReasons.find(reason => reason.value === state.reasonCode)?.label}</p>
          <div className={styles.details}>
            <label htmlFor={`${id}-detail`} className={styles.srOnly}>상세 신고 내용 (선택)</label>
            <div className={styles.textbox}>
              <textarea id={`${id}-detail`} value={state.detail} disabled={locked || !current} placeholder="신고 내용을 자세히 적어주세요."
                aria-describedby={`${id}-count ${id}-help ${id}-feedback`} aria-invalid={length > REPORT_LIMIT || undefined}
                onChange={event => current?.control.change({ detail: event.target.value })} />
              <span id={`${id}-count`} className={styles.count}>{length}/{REPORT_LIMIT}</span>
            </div>
            <p id={`${id}-help`} className={styles.help}>개인정보는 입력하지 않도록 주의해주세요</p>
          </div>
        </>}
      <div id={`${id}-feedback`} className={styles.feedback}>
        {demo ? <p role="status">Demo에서는 실제 신고를 접수하지 않아요. 로그인 후 이용해주세요.</p> : null}
        {length > REPORT_LIMIT ? <p role="alert">상세 내용은 500자까지 입력할 수 있어요.</p> : null}
        {state.message ? <p role="alert">{state.message}</p> : null}
        {busy ? <p role="status">접수 중이에요. 창을 닫아도 이미 전송된 신고는 취소되지 않아요.</p> : null}
      </div>
      {done || locked && !busy ? <button type="button" className={styles.submit} onClick={onClose}>확인</button>
        : <button type="submit" className={styles.submit} disabled={!current || busy || (step === "reason" ? !state.reasonCode : demo || !source || Boolean(reportValidation(state)))}>
          {step === "reason" ? "다음" : busy ? "접수 중…" : state.status === "error" ? "다시 시도" : "신고하기"}
        </button>}
    </form>
  </dialog>;
}
