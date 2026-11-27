"use client";

import Link from "next/link";
import { useCallback, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import { dashboardHref, parseDashboardQuery, parseSupportTarget, type SupportTarget } from "@/lib/admin-attention-navigation";
import { memberSupportFailure } from "@/lib/admin-read-contract";
import { loadAdminReport } from "@/lib/admin-reports";
import { subscribeSupportChanges, supportCompletedVersion } from "@/lib/admin-support-flow";
import { AdminStatus } from "./admin-status";
import { ReportDetailPanel } from "./report-detail-panel";
import { PendingSupportNotice } from "./support-action-panel";
import { useAdminRead } from "./use-admin-read";
import shared from "./admin.module.css";
import styles from "./reports.module.css";

export function SupportDetailRoute({ recordType, recordId }: { recordType: string; recordId: string }) {
  const params = useSearchParams();
  const back = dashboardHref(parseDashboardQuery(params));
  const target = parseSupportTarget(recordType, recordId);
  return <div className={styles.detailPage}>
    <Link href={back} className={shared.backLink}>← 대시보드로</Link>
    <div className={shared.heading}><h1>{recordType === "inquiry" ? "문의 상세" : "신고 상세"}</h1>
      <p>선택한 접수 항목의 내용과 처리 상태를 확인하세요.</p></div>
    {target ? <ConnectedSupportDetail key={target.id} target={target} /> :
      <AdminStatus subject="신고·문의" state={{ status: "not-found", message: "신고·문의 주소를 확인해 주세요." }} />}
  </div>;
}

function ConnectedSupportDetail({ target }: { target: SupportTarget }) {
  const revision = useSyncExternalStore(subscribeSupportChanges, supportCompletedVersion, supportCompletedVersion);
  // A direct detail GET has no list period/filter dependency. Opening this
  // route never executes an action or replays a pending write.
  const read = useCallback((signal: AbortSignal) => loadAdminReport(target, signal), [target.id, target.recordType, target.recordId]);
  const { state, retry } = useAdminRead(`${target.id}:${revision}`, read, memberSupportFailure);
  return <>
    {state.status === "ready" && <PendingSupportNotice />}
    <ReportDetailPanel key={`${target.id}:${revision}:${state.status}`}
      report={state.status === "ready" ? state.data : null} state={state} retry={retry} />
  </>;
}
