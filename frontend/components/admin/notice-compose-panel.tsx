"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { emptyNoticeDraft, noticeScheduleDate, type NoticeAction, type NoticeCapability, type NoticeDraft, type NoticeRecord } from "@/lib/admin-notices";
import { noticeFlowFor } from "@/lib/admin-notice-flow";
import { noticeActionLabels, prepareNoticeRequest } from "@/lib/admin-notice-write";
import { NoticeActionStatus } from "./notice-action-status";
import shared from "./admin.module.css";
import panels from "./reports.module.css";
import styles from "./notices.module.css";

// Unsaved form state survives a list refresh, without browser storage. Actor and
// exact draft version partition it so another account cannot see these inputs.
const drafts = new Map<string, NoticeDraft>();

export function NoticeComposePanel({ initialDraft = emptyNoticeDraft, notice = null, capabilities, onNew }: {
  initialDraft?: NoticeDraft; notice?: NoticeRecord | null; capabilities?: NoticeCapability; onNew?: () => void;
}) {
  const permission = notice?.actions ?? capabilities, actor = permission?.scope?.actorUserId;
  const flow = noticeFlowFor(notice?.id ?? null, actor);
  const state = useSyncExternalStore(flow.subscribe, flow.state, flow.state);
  const draftKey = `${actor ?? "preview"}:${notice?.id ?? "new"}:${notice?.updatedAt ?? ""}`;
  const [draft, setForm] = useState<NoticeDraft>(() => (actor ? drafts.get(draftKey) : undefined) ?? (notice ? { ...emptyNoticeDraft, title: notice.title, body: notice.body ?? "" } : initialDraft));
  const setDraft = (value: NoticeDraft) => { if (actor) drafts.set(draftKey, value); setForm(value); };
  const [action, setAction] = useState<NoticeAction>("save_draft"), [issue, setIssue] = useState<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const busy = state.phase === "submitting" || state.phase === "uncertain";
  const finished = state.phase === "succeeded";
  const reopenRequired = Boolean(notice && state.phase === "stopped" && state.failure.reload);
  useEffect(() => { if (finished) { drafts.delete(draftKey); setForm(emptyNoticeDraft); onNew?.(); } }, [finished, onNew, draftKey]);
  useEffect(() => { if (reopenRequired) onNew?.(); }, [reopenRequired, onNew]);
  const allowed = (next: NoticeAction) => !busy && !finished && !reopenRequired && permission?.disabledReason === null && Boolean(permission.scope) && permission.allowedActions.includes(notice ? next : "create");
  function confirm(next: NoticeAction) {
    if (!allowed(next)) return;
    try { prepareNoticeRequest(notice, next, draft, capabilities, crypto.randomUUID()); setIssue(null); setAction(next); dialog.current?.showModal(); }
    catch (error) { setIssue(error instanceof Error ? error.message : "작성 내용을 확인해 주세요."); }
  }
  return <section className={`${shared.panel} ${panels.detail} ${styles.compose}`} aria-labelledby="notice-compose-title" data-notice-compose>
    <div className={panels.detailHeading}><h2 id="notice-compose-title">{notice ? "임시 공지 편집" : "새 공지 작성"}</h2><p>게시된 공지는 회원 알림함에 전달됩니다.</p></div>
    {notice && <button type="button" className={shared.secondaryButton} disabled={busy} onClick={onNew}>새 공지 작성으로 돌아가기</button>}
    <label className={styles.field}>공지 제목<input value={draft.title} disabled={busy} onChange={event => { setDraft({ ...draft, title: event.target.value }); setIssue(null); }} placeholder="제목을 입력해주세요" autoComplete="off" /></label>
    <label className={styles.field}>공지 내용<textarea value={draft.body} disabled={busy} onChange={event => { setDraft({ ...draft, body: event.target.value }); setIssue(null); }} placeholder="회원에게 전달할 내용을 작성해주세요" /></label>
    <p className={styles.availability}>제목 100자 · 본문 5,000자 이내</p>
    <div className={styles.schedule} data-scheduled={draft.scheduled}>
      <div className={styles.switchRow}><span id="notice-schedule-label">예약 발송</span><button type="button" role="switch" disabled={busy} aria-checked={draft.scheduled} aria-labelledby="notice-schedule-label" aria-controls="notice-schedule-fields" onClick={() => setDraft({ ...draft, scheduled: !draft.scheduled })}>
        <img src={`/admin/notice-switch-${draft.scheduled ? "on" : "off"}.svg`} alt="" /></button></div>
      {draft.scheduled && <div className={styles.scheduleFields} id="notice-schedule-fields">
        <label className={styles.picker}><span className={shared.srOnly}>예약 날짜 (한국 시간)</span><span aria-hidden="true"><img src="/admin/notice-calendar.svg" alt="" />{noticeScheduleDate(draft.date)}<img src="/admin/sort-down.svg" alt="" /></span>
          <input type="date" value={draft.date} disabled={busy} onChange={event => setDraft({ ...draft, date: event.target.value })} /></label>
        <label className={styles.picker}><span className={shared.srOnly}>예약 시간 (한국 시간)</span><span aria-hidden="true"><img src="/admin/notice-clock.svg" alt="" />{draft.time || "시간 선택"}<img src="/admin/sort-down.svg" alt="" /></span>
          <input type="time" value={draft.time} disabled={busy} onChange={event => setDraft({ ...draft, time: event.target.value })} /></label>
      </div>}
      {draft.scheduled && <p className={styles.availability}>한국 시간(UTC+09:00) 기준입니다. 예약 시각부터 자동 게시·알림 전달이 시작됩니다.</p>}
    </div>
    {issue && <p role="alert" className={shared.fieldError}>{issue}</p>}
    <div className={panels.actions}>
      <button type="button" className={shared.secondaryButton} disabled={!allowed("save_draft")} onClick={() => confirm("save_draft")}>임시 저장</button>
      <button type="button" className={shared.primaryButton} disabled={!allowed(draft.scheduled ? "schedule" : "publish")} onClick={() => confirm(draft.scheduled ? "schedule" : "publish")}>{draft.scheduled ? "예약 하기" : "게시하기"}</button>
    </div>
    {!permission || permission.disabledReason ? <p className={styles.availability}>작성 권한이 확인된 관리자만 공지를 저장·게시·예약할 수 있습니다.</p> : <p className={styles.availability}>임시 저장한 공지는 목록에서 다시 열 수 있습니다. 게시 후에는 수정할 수 없습니다.</p>}
    {state.phase === "blocked" && <NoticeActionStatus flow={flow} state={state} />}
    <dialog ref={dialog} className={shared.reviewDialog} aria-labelledby="notice-confirm-title" onCancel={event => { event.preventDefault(); dialog.current?.close(); }}>
      <h2 id="notice-confirm-title">공지 {noticeActionLabels[action]} 확인</h2>
      <p>{action === "save_draft" ? "임시 저장하며 회원에게 전달하지 않습니다." : action === "schedule" ? `${noticeScheduleDate(draft.date)} ${draft.time} (한국 시간)에 게시하고 회원 알림함에 전달합니다.` : "지금 게시하고 회원 알림함에 전달합니다. 게시 후에는 수정할 수 없습니다."}</p>
      <div className={panels.confirmation}><strong>{draft.title.trim()}</strong><p>{draft.body.trim()}</p></div>
      <div className={panels.writeButtons}><button type="button" className={shared.secondaryButton} onClick={() => dialog.current?.close()}>취소</button>
        <button type="button" className={shared.primaryButton} disabled={!allowed(action)} onClick={() => { dialog.current?.close(); void flow.execute(notice, action, draft, capabilities); }}>{noticeActionLabels[action]} 확정</button></div>
    </dialog>
  </section>;
}
