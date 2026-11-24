"use client";

import { useSyncExternalStore } from "react";
import { MAX_RECHECKS, pendingReviews, serverPendingReviews, subscribePendingReviews } from "@/lib/admin-review-flow";

// Root-layout sibling of every route. A permission error, detail unmount or SPA
// navigation cannot hide an unresolved request. No rationale or account data is shown.
export function PendingReviewNotice() {
  const pending = useSyncExternalStore(subscribePendingReviews, pendingReviews, serverPendingReviews);
  if (!pending.length) return null;
  return <aside aria-label="처리 확인 중인 검수 요청" role="status" data-pending-reviews
    style={{ position: "sticky", bottom: 0, zIndex: 100, padding: "16px 24px", background: "#fff4d6", borderTop: "1px solid #b88b34", color: "#252525" }}>
    <strong>확인이 끝나지 않은 검수 요청이 있습니다.</strong>
    <p>새로고침하거나 창을 닫으면 요청 번호를 잃을 수 있습니다. 확인을 끝내거나 요청 번호를 기록해 주세요.</p>
    {pending.map(({ flow, state }) => <div key={`${flow.contentId}-${state.request.requestId}`}>
      <p>콘텐츠 #{flow.contentId} · {state.phase === "submitting" ? "처리 확인 중" : "처리 여부 미확인"}</p>
      <p style={{ overflowWrap: "anywhere" }}>요청 번호: <code>{state.request.requestId}</code></p>
      {state.phase === "uncertain" && <>
        <p>{state.message}</p>
        <button type="button" disabled={state.rechecks >= MAX_RECHECKS || !flow.writeGate().enabled} onClick={() => void flow.recheck()}>
          원래 요청 다시 확인 ({state.rechecks}/{MAX_RECHECKS})</button>{" "}
        <button type="button" onClick={() => {
          if (window.confirm(`처리 여부가 아직 확인되지 않았습니다. 요청 번호 ${state.request.requestId}를 기록했으며 확인을 중단할까요?`)) flow.release();
        }}>요청 번호를 기록하고 확인 종료</button>
      </>}
    </div>)}
  </aside>;
}
