"use client";

import { useState } from "react";
import { emptyNoticeDraft, noticeScheduleDate, type NoticeDraft } from "@/lib/admin-notices";
import shared from "./admin.module.css";
import panels from "./reports.module.css";
import styles from "./notices.module.css";

export function NoticeComposePanel({ initialDraft = emptyNoticeDraft }: { initialDraft?: NoticeDraft }) {
  const [draft, setDraft] = useState(initialDraft);
  return <form className={`${shared.panel} ${panels.detail} ${styles.compose}`} aria-labelledby="notice-compose-title" aria-describedby="notice-compose-availability" data-notice-compose onSubmit={event => event.preventDefault()}>
    <div className={panels.detailHeading}><h2 id="notice-compose-title">새 공지 작성</h2><p>관리자가 직접 공지와 알림을 등록할 수 있어요.</p></div>
    <label className={styles.field}>공지 제목<input value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} placeholder="제목을 입력해주세요" autoComplete="off" /></label>
    <label className={styles.field}>공지 내용<textarea value={draft.body} onChange={event => setDraft({ ...draft, body: event.target.value })} placeholder="회원에게 전달할 내용을 작성해주세요" /></label>
    <div className={styles.schedule} data-scheduled={draft.scheduled}>
      <div className={styles.switchRow}><span id="notice-schedule-label">예약 발송</span><button type="button" role="switch" aria-checked={draft.scheduled} aria-labelledby="notice-schedule-label" aria-controls="notice-schedule-fields" onClick={() => setDraft({ ...draft, scheduled: !draft.scheduled })}>
        <img src={`/admin/notice-switch-${draft.scheduled ? "on" : "off"}.svg`} alt="" /></button></div>
      {draft.scheduled && <div className={styles.scheduleFields} id="notice-schedule-fields">
        <label className={styles.picker}><span className={shared.srOnly}>예약 날짜 (한국 시간)</span><span aria-hidden="true"><img src="/admin/notice-calendar.svg" alt="" />{noticeScheduleDate(draft.date)}<img src="/admin/sort-down.svg" alt="" /></span>
          <input type="date" value={draft.date} onChange={event => setDraft({ ...draft, date: event.target.value })} /></label>
        <label className={styles.picker}><span className={shared.srOnly}>예약 시간 (한국 시간)</span><span aria-hidden="true"><img src="/admin/notice-clock.svg" alt="" />{draft.time || "시간 선택"}<img src="/admin/sort-down.svg" alt="" /></span>
          <input type="time" value={draft.time} onChange={event => setDraft({ ...draft, time: event.target.value })} /></label>
      </div>}
    </div>
    <div className={`${panels.actions} ${draft.scheduled ? styles.scheduledActions : ""}`}>
      <button type="button" className={shared.secondaryButton} disabled>임시 저장</button>
      <button type="submit" className={shared.primaryButton} disabled>{draft.scheduled ? "예약 하기" : "게시하기"}</button>
    </div>
    <p id="notice-compose-availability" className={styles.availability}>미연결 · 작성 내용은 저장되지 않으며, 임시 저장·게시·예약·알림 발송은 사용할 수 없습니다.</p>
  </form>;
}
