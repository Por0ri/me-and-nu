"use client";

import { useEffect, useRef, useState } from "react";
import { rawReportStatuses, reportDate, reportKind, reportSourceUrl, reportStatuses, type ReportContent, type ReportRecord, type ReportStatus } from "@/lib/admin-reports";
import type { AdminReadState } from "@/lib/admin-content";
import { AdminStatus } from "./admin-status";
import { SupportActionPanel } from "./support-action-panel";
import shared from "./admin.module.css";
import styles from "./reports.module.css";

export function ReportBadge({ status }: { status: ReportStatus | null }) {
  return status ? <span className={`${shared.statusChip} ${styles.badge}`} data-report-status={status}>{reportStatuses[status]}</span> : <span className={styles.muted}>상태 미연결</span>;
}

function ReportContentDrawer({ content, onClose }: { content: ReportContent; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const panel = dialog.current, opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    panel?.showModal(); close.current?.focus(); document.body.style.overflow = "hidden";
    return () => { panel?.close(); document.body.style.overflow = overflow; if (opener?.isConnected) opener.focus(); };
  }, []);
  const sourceUrl = content.source && reportSourceUrl(content.source.url);
  return <dialog ref={dialog} id="report-content" className={styles.drawer} aria-labelledby="report-content-title" aria-describedby="report-content-description"
    onCancel={event => { event.preventDefault(); onClose(); }}
    onKeyDown={event => {
      if (event.key !== "Tab") return;
      const targets = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])')).filter(node => node.getClientRects().length > 0);
      const first = targets[0], last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}>
    <div className={styles.drawerHeading}><div><h2 id="report-content-title">신고 상세 검토</h2><p id="report-content-description">선택한 신고에 대한 정보와 처리 내역</p></div>
      <button ref={close} type="button" aria-label="신고 상세 검토 닫기" onClick={onClose}><img src="/admin/history-close.svg" alt="" /></button></div>
    <div className={styles.drawerScroll} role="region" aria-label="신고 콘텐츠 본문, 세로 스크롤 가능" tabIndex={0}>
      <article className={styles.content}>
        <div className={styles.cover}>{content.coverUrl && <img src={content.coverUrl} alt="" />}<div className={styles.coverCopy}>
          {content.category && <span>{content.category}</span>}<h3>{content.title}</h3>{content.subtitle && <p>{content.subtitle}</p>}
        </div></div>
        <div className={styles.contentBody}><p>{content.body ?? "저장된 본문이 없습니다."}</p>{content.source && <p>출처: {sourceUrl ? <a href={sourceUrl} target="_blank" rel="noopener noreferrer">{content.source.title}</a> : content.source.title}</p>}
          {content.productionType !== "human" && <p className={styles.aiNotice}>{content.productionType === "hybrid" ? "AI와 사람이 함께 제작한 콘텐츠입니다." : "AI로 생성된 콘텐츠입니다."}</p>}</div>
      </article>
      <div className={`${styles.actions} ${styles.drawerActions}`}><button type="button" className={shared.secondaryButton} disabled>콘텐츠 삭제</button><button type="button" className={shared.primaryButton} disabled>콘텐츠 수정</button></div>
      <p className={styles.availability}>읽기 전용 · 콘텐츠 삭제·수정은 사용할 수 없습니다.</p>
    </div>
  </dialog>;
}

export function ReportDetailPanel({ report, state, retry }: { report: ReportRecord | null; state?: AdminReadState<ReportRecord>; retry?: () => void }) {
  const [open, setOpen] = useState(false);
  return <section className={`${shared.panel} ${styles.detail}`} aria-labelledby="report-detail-title" data-report-detail={report?.id ?? "unavailable"}>
    <div className={styles.detailHeading}><h2 id="report-detail-title">신고 상세 검토</h2><p>선택한 신고에 대한 정보와 처리 내역</p></div>
    {report ? <>
      <div className={styles.identity}><h3>{report.number}</h3><ReportBadge status={report.status} /></div>
      <dl><div><dt>신고 유형</dt><dd>{reportKind(report.kind, true)}</dd></div>
        {report.kind === "content" && <div><dt>신고 사유</dt><dd>{report.reason ?? "—"}</dd></div>}
        <div><dt>접수일</dt><dd>{reportDate(report.receivedAt, true)}</dd></div><div><dt>처리 상태</dt><dd>{rawReportStatuses[report.rawStatus]}</dd></div><div><dt>신고 대상</dt><dd>{report.target ?? "—"}</dd></div>
      </dl>
      <div className={styles.message}><h3>{report.kind === "content" ? "신고 내용" : "문의 내용"}</h3><p data-inquiry={report.kind !== "content"}>{report.message ?? "저장된 내용이 없습니다."}</p></div>
      {report.kind !== "content" && (report.answerAvailability === "available" ? <div className={styles.message} data-inquiry-answer>
        <h3>등록된 답변</h3><p>{report.answer}</p><small>답변일: {reportDate(report.answeredAt, true)}</small>
      </div> : <p className={styles.availability}>{report.answerAvailability === "none" ? "아직 등록된 답변이 없습니다." : "이전 답변 내용은 저장되어 있지 않습니다."}</p>)}
      {report.kind === "content" && <div className={styles.actions}>
        <button type="button" className={shared.secondaryButton} disabled={!report.content} aria-haspopup="dialog" aria-controls="report-content" onClick={() => setOpen(true)}>신고 콘텐츠 보기</button>
      </div>}
      <SupportActionPanel report={report} />
      <span className={shared.srOnly} role="status">{report.number} {reportKind(report.kind, true)} {report.status ? reportStatuses[report.status] : "상태 미연결"} 상세</span>
      {open && report.content && <ReportContentDrawer content={report.content} onClose={() => setOpen(false)} />}
    </> : state && state.status !== "ready" ? <AdminStatus subject="신고·문의" state={state} retry={retry} /> : <p className={styles.detailEmpty}>목록에서 신고·문의를 선택하면 상세 내용을 확인할 수 있습니다.</p>}
  </section>;
}
