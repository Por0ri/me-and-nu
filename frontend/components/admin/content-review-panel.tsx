"use client";

import { useRef, useState } from "react";
import type { AdminContentDetail } from "@/lib/admin-content";
import { canPrepareReview, reviewRationaleError, type ReviewAction } from "@/lib/admin-content-review";
import styles from "./admin.module.css";

export function ContentReviewPanel({ content }: { content: AdminContentDetail }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [action, setAction] = useState<ReviewAction>("approve");
  const [rationale, setRationale] = useState("");
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eligible = canPrepareReview(content);
  const label = action === "approve" ? "승인" : "반려";
  const clear = () => { setRationale(""); setChecked(false); setError(null); };
  const open = (next: ReviewAction) => { clear(); setAction(next); dialog.current?.showModal(); };

  return <section className={styles.reviewPanel} aria-label="콘텐츠 검수 조치">
    <h3>콘텐츠 검수</h3>
    <p className={styles.note}>승인·반려 실행은 아직 연결되지 않았습니다. 조치 내용과 사유만 확인할 수 있으며 저장되지 않습니다.</p>
    {!eligible && <p role="status">현재 저장된 상태에서는 검수 조치를 준비할 수 없습니다.</p>}
    <div className={styles.formActions}>
      <button className={styles.primaryButton} type="button" disabled={!eligible} onClick={() => open("approve")}>승인 내용 확인</button>
      <button className={styles.secondaryButton} type="button" disabled={!eligible} onClick={() => open("reject")}>반려 내용 확인</button>
    </div>
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
        <p className={styles.inputNotice} role="status">{checked ? "입력 확인 완료 · 미저장. 콘텐츠 상태는 변경되지 않았습니다." : "확인 단계 · 미저장. 실행 기능 연결 전에는 저장할 수 없습니다."}</p>
        <div className={styles.formActions}>
          <button type="button" className={styles.secondaryButton} onClick={() => dialog.current?.close()}>취소</button>
          <button type="submit" className={styles.secondaryButton}>입력 확인</button>
          <button type="button" className={styles.primaryButton} disabled>{label} 실행 (미연결)</button>
        </div>
      </form>
    </dialog>
  </section>;
}
