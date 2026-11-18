"use client";

import { useEffect, useRef } from "react";
import { noticeDate, type NoticeRecord } from "@/lib/admin-notices";
import shared from "./admin.module.css";
import panels from "./reports.module.css";
import styles from "./notices.module.css";

export function NoticeDetailPanel({ notice, onClose }: { notice: NoticeRecord; onClose: () => void }) {
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
      <section><h3>공지 제목</h3><p className={styles.detailTitle}>{notice.title}</p></section>
      <section><h3>공지 내용</h3><p>{notice.body ?? "공지 내용 연결 준비 중"}</p></section>
      <div><time className={styles.detailDate} dateTime={notice.publishedAt ?? undefined}>{noticeDate(notice.publishedAt, true)}</time>
        <div className={`${panels.actions} ${styles.detailActions}`}><button type="button" className={shared.secondaryButton} disabled>삭제하기</button><button type="button" className={shared.primaryButton} disabled>수정하기</button></div>
        <p className={styles.availability}>읽기 전용 · 공지 수정·삭제는 사용할 수 없습니다.</p></div>
    </div>
  </dialog>;
}
