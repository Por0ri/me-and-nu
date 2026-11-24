"use client";
import { useSyncExternalStore } from "react";
import { MAX_NOTICE_RECHECKS, noticeFlows, serverNoticeFlows, subscribeNoticeChanges, type NoticeFlow, type NoticeFlowState } from "@/lib/admin-notice-flow";
import { noticeActionLabels } from "@/lib/admin-notice-write";
import { noticeStatuses } from "@/lib/admin-notices";
import shared from "./admin.module.css";
import styles from "./reports.module.css";

export function NoticeActionStatus({ flow, state }: { flow: NoticeFlow; state: NoticeFlowState }) {
  if (state.phase === "idle") return null;
  if (state.phase === "blocked") return <p role="alert" className={styles.writeStatus}>{state.message}</p>;
  return <div role="status" className={styles.writeStatus} data-notice-phase={state.phase} aria-busy={state.phase === "submitting"}>
    {state.phase === "succeeded" ? <><strong>{noticeActionLabels[state.request.action]} 완료</strong><p>공지 {state.result.noticeId} · {noticeStatuses[state.result.status]}</p>
      {state.result.replayed && <p>이미 처리된 요청을 확인했습니다. 중복 생성하거나 발송하지 않았습니다.</p>}
      <button type="button" className={shared.secondaryButton} onClick={flow.release}>확인</button></>
      : state.phase === "submitting" ? <p>{state.rechecks ? "같은 요청의 결과를 확인하고 있습니다." : "처리하고 있습니다."}</p>
      : state.phase === "uncertain" ? <><p>{state.message}</p><p>결과가 확인될 때까지 새 요청을 만들지 않습니다.</p>
        <button type="button" className={shared.primaryButton} disabled={state.rechecks >= MAX_NOTICE_RECHECKS} onClick={() => void flow.recheck()}>같은 요청으로 다시 확인 ({state.rechecks}/{MAX_NOTICE_RECHECKS})</button>
        {state.rechecks >= MAX_NOTICE_RECHECKS && <p>아래 요청 번호로 처리 기록을 확인해 주세요.</p>}</>
      : <><p>{state.failure.message}</p>{state.failure.reload && <p>공지 상세를 다시 열어 최신 내용을 확인한 뒤 진행해 주세요.</p>}
        <button type="button" className={shared.secondaryButton} onClick={flow.release}>확인</button></>}
    <p className={styles.requestNumber}>요청 번호: {state.request.requestId}</p>
  </div>;
}
export function PendingNoticeActions({ actor }: { actor?: string }) {
  const all = useSyncExternalStore(subscribeNoticeChanges, noticeFlows, serverNoticeFlows);
  const visible = all.filter(flow => { const state = flow.state(); return "scope" in state && state.scope.actorUserId === actor; });
  if (!visible.length) return null;
  return <aside className={styles.pending} aria-label="공지 처리 결과"><h2>공지 처리 결과</h2>{visible.map((flow, index) => <NoticeActionStatus key={index} flow={flow} state={flow.state()} />)}</aside>;
}
