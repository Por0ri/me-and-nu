"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { adminDate, type AdminFailure, type AdminReadState } from "@/lib/admin-content";
import { generationAge, generationCount, generationUsd, loadAdminGeneration, type GenerationSnapshot } from "@/lib/admin-generation";
import { useAdminRead } from "./use-admin-read";
import shared from "./admin.module.css";
import styles from "./generation-panel.module.css";

const POLL_MS = 30_000;
const countWithUnit = (value: number | null | undefined) => value == null ? "확인 불가" : `${generationCount(value)}건`;
const stateLabels: Record<string, string> = {
  active: "작동 중", activating: "시작 중", inactive: "중지", failed: "오류", unknown: "확인 불가",
  running: "실행 중", stopped: "중지", missing: "연결 없음",
};
const jobLabels = [
  ["queued", "대기"], ["running", "실행 중"], ["succeeded", "실행 완료"], ["failed", "실행 실패"],
  ["needsAttention", "확인 필요"], ["cancelled", "취소"], ["held", "보류"], ["rejected", "AI 검수 반려"],
  ["skipped", "생략"], ["readyForStaging", "AI 검수 통과"],
] as const;
const reasonLabels: Record<string, string> = {
  not_configured: "생성 현황 연결을 준비하고 있습니다.", snapshot_missing: "아직 수집된 현황이 없습니다.",
  snapshot_unreadable: "수집된 현황을 읽지 못했습니다.", snapshot_invalid: "수집된 현황을 확인할 수 없습니다.",
  snapshot_future: "수집 시각이 맞지 않아 현황을 표시하지 않습니다.",
  snapshot_stale: "갱신이 지연되고 있습니다. 아래 숫자는 마지막 수집 당시의 기록입니다.",
  policy_day_mismatch: "오늘(KST)의 기록이 아직 없습니다. 이전 날짜의 시도·작업 수를 오늘 수치로 표시하지 않습니다.",
  source_partial: "일부 기록을 확인하지 못했습니다. 확인된 값만 표시합니다.",
  source_unavailable: "생성 기록에 연결하지 못했습니다. 확인되지 않은 값은 0으로 표시하지 않습니다.",
};

export function ConnectedGeneration({ onAccessFailure }: { onAccessFailure: (failure: AdminFailure) => void }) {
  const [poll, setPoll] = useState(0);
  const read = useCallback((signal: AbortSignal) => loadAdminGeneration(signal), []);
  const { state, retry } = useAdminRead(`live-generation:${poll}`, read);
  useEffect(() => {
    if (state.status === "login-required" || state.status === "forbidden") onAccessFailure(state);
  }, [state, onAccessFailure]);
  useEffect(() => {
    if (state.status === "loading" || state.status === "login-required" || state.status === "forbidden") return;
    // Only this inexpensive endpoint is polled. Existing dashboard SQL is not.
    const timer = window.setTimeout(() => {
      if (document.visibilityState === "visible") setPoll(value => value + 1);
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [poll, state.status]);
  return <GenerationPanel state={state} refresh={retry} />;
}

function Counter({ label, value, limit, remaining }: { label: string; value: number | null | undefined; limit: number; remaining: number | null | undefined }) {
  return <div className={styles.counter}>
    <h3>{label}</h3><p><strong>{generationCount(value)}</strong><span> / {limit}건</span></p>
    {value != null && <progress value={Math.min(value, limit)} max={limit} aria-label={`${label} ${value} / ${limit}건`} />}
    <small>남은 {label === "생성 시도" ? "시도" : "목표"} {remaining == null ? "확인 불가" : `${generationCount(remaining)}건`}</small>
  </div>;
}

export function GenerationPanel({ state, refresh }: { state: AdminReadState<GenerationSnapshot>; refresh: () => void }) {
  const data = state.status === "ready" ? state.data : null;
  const stale = data?.availability === "stale" || (data?.ageSeconds != null && data.ageSeconds > data.staleAfterSeconds);
  const runtimeIssue = data?.runtime && Object.values(data.runtime).some(value => value === "failed" || value === "missing");
  const status = stale ? "갱신 지연" : runtimeIssue ? "실행 상태 확인 필요" : data?.availability === "available" ? "현황 확인됨" : data?.availability === "partial" ? "일부 확인" : "확인 필요";
  const daily = data?.daily;
  return <section className={styles.panel} aria-label="자동 콘텐츠 생성 현황" data-admin-generation data-generation-state={state.status} data-generation-availability={data?.availability}>
    <div className={styles.heading}>
      <div><h2>자동 콘텐츠 생성</h2><p>오늘(KST)의 기사·정보사전 생성 현황 · 이 패널만 30초마다 갱신</p></div>
      <button type="button" className={shared.secondaryButton} onClick={refresh} disabled={state.status === "loading"}>생성 현황 새로고침</button>
    </div>
    {!data ? <div className={styles.feedback} role={state.status === "error" ? "alert" : "status"}>
      {state.status === "loading" ? "생성 현황을 불러오는 중입니다." : state.status === "forbidden" || state.status === "login-required" ? "조회 권한을 다시 확인하고 있습니다." : "생성 현황을 불러오지 못했습니다. 확인되지 않은 값은 0으로 표시하지 않습니다."}
    </div> : <>
      <div className={styles.meta}><span className={`${styles.badge} ${stale || runtimeIssue || data.availability !== "available" ? styles.warning : ""}`}>{status}</span>
        <span>집계일 {data.policyDate ?? "확인 불가"} (KST)</span>
        <span>{generationAge(data.ageSeconds)}</span>
        {data.generatedAt && <time dateTime={data.generatedAt}>{adminDate(data.generatedAt)} (KST)</time>}
      </div>
      {(data.reason || stale) && <p className={styles.notice} role="status">{stale ? reasonLabels.snapshot_stale : reasonLabels[data.reason ?? ""] ?? "일부 현황을 확인하지 못했습니다."}</p>}
      {runtimeIssue && <p className={styles.notice} role="status">생성 제어·Worker 상태에 오류 또는 연결 누락이 기록되어 있습니다. 아래 실행 상태를 확인해 주세요.</p>}
      <div className={styles.metrics}>
        <Counter label="생성 시도" value={daily?.attempts} limit={40} remaining={daily?.remainingAttempts} />
        <Counter label="AI 검수 통과" value={daily?.passes} limit={20} remaining={daily?.remainingPasses} />
        <div className={styles.cost}><h3>오늘 기록된 추정 비용</h3><strong>{generationUsd(daily?.knownEstimatedCostUsd)}</strong><span>USD · 확인된 사용량 소계</span>
          <p>비용 미확정 {countWithUnit(daily?.unknownCostJobs)} · 사용량 집계 대기 {countWithUnit(daily?.inflightCostJobs)}</p>
          <small>최종 청구액이 아닙니다. 미확정·집계 대기 비용이 더해질 수 있습니다.</small>
        </div>
      </div>
      <p className={styles.approvalNote}>AI 검수 통과는 공개 승인이 아닙니다. 운영자 검수·승인 후 공개합니다. <Link href="/admin/contents?mode=review">검수 대기 목록 →</Link></p>
      <div className={styles.details}>
        <div className={styles.runtime}><h3>실행 상태</h3><dl>
          {([["controller", "생성 제어"], ["timer", "자동 실행 일정"], ["articleWorker", "기사 Worker"], ["ragWorker", "정보사전 Worker"]] as const).map(([key, label]) =>
            <div key={key}><dt>{label}</dt><dd>{stateLabels[data.runtime?.[key] ?? "unknown"]}</dd></div>)}
        </dl><p>시도 구분: 기사 {countWithUnit(daily?.articleAttempts)} · 정보사전 {countWithUnit(daily?.ragAttempts)}</p></div>
        <details className={styles.queueDetails}><summary>기사·정보사전 작업 상태 보기</summary>
          <p>집계일에 등록된 작업 기준입니다. 완료 작업의 보류·반려·생략·검수 통과는 중복 집계될 수 있습니다.</p>
          <table><caption>오늘 작업 상태</caption><thead><tr><th scope="col">작업 상태</th><th scope="col">기사 기반</th><th scope="col">정보사전(RAG)</th></tr></thead>
            <tbody>{jobLabels.map(([key, label]) => <tr key={key}><th scope="row">{label}</th><td>{generationCount(data.jobs?.article[key])}</td><td>{generationCount(data.jobs?.rag[key])}</td></tr>)}</tbody>
          </table>
          <p>브로커 대기: 기사 {countWithUnit(data.broker?.articleQueued)} · 정보사전 {countWithUnit(data.broker?.ragQueued)}<br />
            처리 확인 대기 {countWithUnit(data.broker?.unacked)} · 확인 목록 {countWithUnit(data.broker?.unackedIndex)}</p>
        </details>
        <details className={styles.costDetails}><summary>비용·기록 집계 기준 보기</summary><dl>
          <div><dt>오늘 합계 추정</dt><dd>{generationUsd(daily?.totalEstimatedCostUsd)}</dd></div>
          <div><dt>예약 금액 (사용액 아님)</dt><dd>{generationUsd(daily?.reservedCostUsd)}</dd></div>
          <div><dt>대기 기록 / 중복 기록</dt><dd>{generationCount(daily?.pendingRecords)} / {generationCount(daily?.duplicateRecords)}</dd></div>
          <div><dt>전체 기록된 추정 비용 (오늘 포함)</dt><dd>{generationUsd(data.history?.knownEstimatedCostUsd)}</dd></div>
          <div><dt>그중 이월 기준 비용</dt><dd>{generationUsd(data.history?.baselineEstimatedCostUsd)}</dd></div>
          <div><dt>전체 합계 추정</dt><dd>{generationUsd(data.history?.totalEstimatedCostUsd)}</dd></div>
          <div><dt>전체 미확정 비용 작업</dt><dd>{countWithUnit(data.history?.unknownCostJobs)}</dd></div>
        </dl><p>미확정 비용은 0원이 아닙니다. 집계 대기는 사용량 정산이 끝나지 않은 작업으로, 현재 생성 중이라는 뜻은 아닙니다. 예약 금액은 확정 사용액이 아닙니다. 전체 누적에는 오늘과 이월 비용이 포함되어 있어 별도로 더하지 않습니다.</p></details>
      </div>
    </>}
  </section>;
}
