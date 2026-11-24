"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { AdminReadState } from "@/lib/admin-content";
import { emptyNoticeDraft, noticeDate, noticeStatuses, type NoticeRecord } from "@/lib/admin-notices";
import { noticeFlowFor } from "@/lib/admin-notice-flow";
import { NoticeActionStatus } from "./notice-action-status";
import { AdminStatus } from "./admin-status";
import shared from "./admin.module.css";
import panels from "./reports.module.css";
import styles from "./notices.module.css";

export function NoticeDetailPanel({ notice, onClose, onEdit, state, retry }: { notice: NoticeRecord | null; onClose: () => void; onEdit?: (notice: NoticeRecord) => void; state?: AdminReadState<NoticeRecord>; retry?: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const panel = dialog.current, opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    panel?.showModal(); close.current?.focus(); document.body.style.overflow = "hidden";
    return () => { panel?.close(); document.body.style.overflow = overflow; if (opener?.isConnected) opener.focus(); };
  }, []);
  return <dialog ref={dialog} id="notice-detail" className={`${panels.drawer} ${styles.drawer}`} aria-labelledby="notice-detail-title" aria-describedby="notice-detail-description"
    onCancel={event => { event.preventDefault(); onClose(); }} onKeyDown={event => {
      if (event.key !== "Tab") return;
      const targets = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), [tabindex]:not([tabindex="-1"])')).filter(node => node.getClientRects().length > 0);
      const first = targets[0], last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}>
    <div className={panels.drawerHeading}><div><h2 id="notice-detail-title">공지 내용 상세</h2><p id="notice-detail-description">선택한 공지에 대한 정보</p></div>
      <button ref={close} type="button" aria-label="공지 내용 상세 닫기" onClick={onClose}><img src="/admin/history-close.svg" alt="" /></button></div>
    <div className={`${panels.drawerScroll} ${styles.detailBody}`} role="region" aria-label="공지 상세 본문, 세로 스크롤 가능" tabIndex={0}>
      {!notice ? state && state.status !== "ready" ? <AdminStatus subject="알림·공지" state={state} retry={retry} /> : <p>공지를 불러오고 있습니다.</p> : <>
      <section><h3>공지 제목</h3><p className={styles.detailTitle}>{notice.title}</p></section>
      <section><h3>공지 내용</h3><p>{notice.body ?? "공지 내용 연결 준비 중"}</p></section>
      <div><p>{noticeStatuses[notice.status]} · 읽은 회원 {notice.readCount.toLocaleString("ko-KR")}명</p><time className={styles.detailDate} dateTime={notice.publishedAt ?? notice.scheduledAt ?? undefined}>{noticeDate(notice.publishedAt ?? notice.scheduledAt, true)}</time>
        <NoticeDetailActions notice={notice} onEdit={onEdit} />
        {notice.status === "published" && <p className={styles.availability}>게시된 공지는 수정하거나 삭제할 수 없습니다.</p>}</div>
      </>}
    </div>
  </dialog>;
}

function NoticeDetailActions({ notice, onEdit }: { notice: NoticeRecord; onEdit?: (notice: NoticeRecord) => void }) {
  const flow = noticeFlowFor(notice.id, notice.actions?.scope?.actorUserId);
  const state = useSyncExternalStore(flow.subscribe, flow.state, flow.state);
  const [confirm, setConfirm] = useState(false);
  const busy = ["submitting", "uncertain", "succeeded"].includes(state.phase);
  const allowed = notice.actions?.disabledReason === null;
  return <div className={styles.detailActions}>
    {notice.status === "draft" && <button type="button" className={shared.primaryButton} disabled={busy || !onEdit || !allowed || !notice.actions?.allowedActions.includes("save_draft")} onClick={() => onEdit?.(notice)}>임시 공지 편집</button>}
    {notice.status === "scheduled" && <><button type="button" className={shared.secondaryButton} disabled={busy || !allowed || !notice.actions?.allowedActions.includes("cancel_schedule")} onClick={() => setConfirm(true)}>예약 취소</button>
      {confirm && !busy && <div className={panels.confirmation}><p>예약을 취소하고 임시 저장 상태로 되돌립니다. 이미 게시된 경우 취소할 수 없습니다.</p>
        <div className={panels.writeButtons}><button type="button" className={shared.secondaryButton} onClick={() => setConfirm(false)}>돌아가기</button>
          <button type="button" className={shared.primaryButton} onClick={() => { setConfirm(false); void flow.execute(notice, "cancel_schedule", emptyNoticeDraft); }}>예약 취소 확정</button></div></div>}</>}
    {state.phase === "blocked" && <NoticeActionStatus flow={flow} state={state} />}
    {notice.actions?.disabledReason === "permission_denied" && <p className={styles.availability}>현재 계정은 조회 전용입니다.</p>}
  </div>;
}
