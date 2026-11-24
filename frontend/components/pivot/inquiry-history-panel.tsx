"use client";

import type { InquiryHistoryState } from "@/lib/pivot/inquiry-history";
import styles from "./inquiry.module.css";

const statusLabel = { received: "답변 대기", answered: "답변 완료", closed: "종료" } as const;
const formatTime = (value: string) => new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", timeZone: "Asia/Seoul" }).format(new Date(value));

export function InquiryHistoryPanel({ state, onLoad, onSelect }: {
  state: InquiryHistoryState; onLoad: (page: number) => void; onSelect: (id: string) => void;
}) {
  return <section className={styles.history} aria-label="내 문의 내역" aria-busy={state.phase === "loading"}>
    <div className={styles.historyHeading}><h2>내 문의 내역</h2>
      <button type="button" disabled={state.phase === "loading" || state.phase === "unavailable"} onClick={() => onLoad(state.page)}>새로고침</button></div>
    {state.phase === "idle" || state.phase === "loading" ? <p role="status">문의 내역을 불러오는 중…</p> : null}
    {state.message ? <p role="alert">{state.message}</p> : null}
    {state.phase === "error" ? <button type="button" onClick={() => onLoad(state.page)}>내역 다시 불러오기</button> : null}
    {state.phase === "ready" && !state.items.length ? <p role="status">접수한 문의가 없습니다.</p> : null}
    {state.phase === "ready" ? <>
      <ul className={styles.historyList}>{state.items.map(row => <li key={row.inquiryId}>
        <button type="button" aria-pressed={state.selectedId === row.inquiryId} aria-label={`문의 ${row.inquiryId} 상세`}
          onClick={() => onSelect(row.inquiryId)}>
          <span className={styles.historyMeta}><strong>{row.inquiryType === "bug" ? "버그/오류 문의" : "서비스 문의"}</strong>
            <span data-answered={row.answerAvailability === "available" || undefined}>{statusLabel[row.status]}</span></span>
          <span className={styles.historyPreview}>{row.preview}</span><time dateTime={row.createdAt}>{formatTime(row.createdAt)}</time>
        </button>
      </li>)}</ul>
      {state.total > 10 || state.page > 1 ? <nav className={styles.historyPages} aria-label="내 문의 페이지">
        <button type="button" disabled={state.page === 1} onClick={() => onLoad(state.page - 1)}>이전</button>
        <span>{state.page}페이지</span><button type="button" disabled={state.page * 10 >= state.total} onClick={() => onLoad(state.page + 1)}>다음</button>
      </nav> : null}
    </> : null}
    {state.selectedId ? <section className={styles.historyDetail} aria-label="문의 및 답변 상세" aria-busy={state.detailPhase === "loading"}>
      <h3>문의와 답변</h3>
      {state.detailPhase === "loading" ? <p role="status">문의와 답변을 불러오는 중…</p> : null}
      {state.detailMessage ? <p role="alert">{state.detailMessage}</p> : null}
      {state.detailPhase === "error" ? <button type="button" onClick={() => onSelect(state.selectedId!)}>상세 다시 불러오기</button> : null}
      {state.detail ? <>
        <h4>보낸 문의</h4><p className={styles.historyBody}>{state.detail.detail}</p>
        <h4>운영자 답변</h4>
        {state.detail.answerAvailability === "available" ? <><p className={styles.historyAnswer}>{state.detail.answer}</p>
          <time dateTime={state.detail.answeredAt!}>{formatTime(state.detail.answeredAt!)}</time></>
          : <p>{state.detail.answerAvailability === "unavailable" ? "이 문의의 이전 답변 내용은 저장되어 있지 않습니다."
            : state.detail.status === "closed" ? "답변 없이 종료된 문의입니다." : "아직 답변을 기다리고 있어요."}</p>}
      </> : null}
    </section> : null}
  </section>;
}
