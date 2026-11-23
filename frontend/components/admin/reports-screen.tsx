"use client";

import { useCallback, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import type { AdminFailure, AdminReadState } from "@/lib/admin-content";
import { initialReportQuery, loadAdminReport, loadAdminReports, rawReportStatuses, reportDate, reportKind, reportKinds, reportPageSize, reportPeriods, reportQueryString, type ReportPage, type ReportQuery, type ReportStatistics, type ReportSummary } from "@/lib/admin-reports";
import { AdminStatus } from "./admin-status";
import { ReportBadge, ReportDetailPanel } from "./report-detail-panel";
import { ReportPeriodFilter } from "./report-period-filter";
import { useAdminRead } from "./use-admin-read";
import { memberSupportFailure } from "@/lib/admin-read-contract";
import { subscribeSupportChanges, supportCompletedVersion } from "@/lib/admin-support-flow";
import { PendingSupportNotice } from "./support-action-panel";
import shared from "./admin.module.css";
import styles from "./reports.module.css";

export function ReportsRoute() {
  const [query, setQuery] = useState(initialReportQuery);
  return <ConnectedReports key={`${reportQueryString(query)}:${query.q}`} query={query} navigate={setQuery} />;
}

function ConnectedReports({ query, navigate }: { query: ReportQuery; navigate: (query: ReportQuery) => void }) {
  const revision = useSyncExternalStore(subscribeSupportChanges, supportCompletedVersion, supportCompletedVersion);
  const read = useCallback((signal: AbortSignal) => loadAdminReports(query, signal), [query]);
  const { state, retry } = useAdminRead(`${reportQueryString(query)}:${revision}`, read, memberSupportFailure);
  const [accessFailure, setAccessFailure] = useState<AdminFailure | null>(null);
  const effective = accessFailure ?? state;
  const statistics = effective.status === "ready" ? Object.fromEntries(Object.entries(effective.data.statistics).map(([key, count]) => [key, { count, change: null }])) as ReportStatistics : undefined;
  return <>{effective.status === "ready" && <PendingSupportNotice />}<ReportsScreen query={query} state={effective} navigate={navigate} statistics={statistics}
    retry={() => { setAccessFailure(null); retry(); }}
    renderDetail={item => item ? <ConnectedReportDetail key={`${item.id}:${revision}`} item={item} denied={setAccessFailure} /> : <ReportDetailPanel report={null} />} /></>;
}

function ConnectedReportDetail({ item, denied }: { item: ReportSummary; denied: (failure: AdminFailure) => void }) {
  const read = useCallback((signal: AbortSignal) => loadAdminReport(item, signal), [item.id, item.recordId, item.recordType]);
  const { state, retry } = useAdminRead(item.id, read, memberSupportFailure);
  useEffect(() => { if (state.status === "forbidden" || state.status === "login-required") denied(state); }, [state, denied]);
  return <ReportDetailPanel key={state.status} report={state.status === "ready" ? state.data : null} state={state} retry={retry} />;
}

function Statistics({ data, period }: { data?: ReportStatistics; period: ReportQuery["period"] }) {
  return <section className={styles.statistics} aria-label="신고 및 문의 통계" data-report-statistics>{([
    ["all", "전체 신고 및 문의"], ["waiting", "검토 대기"], ["reviewing", "처리 중"], ["completed", "처리 완료"],
  ] as const).map(([key, label]) => <article key={key} data-stat={key}><h2>{label}</h2><strong>{data ? `${data[key].count.toLocaleString("ko-KR")}건` : "—"}</strong>
    <p>{data?.[key].change != null ? <><b>{data[key].change > 0 ? "▲" : data[key].change < 0 ? "▼" : "—"} {Math.abs(data[key].change)}건</b> • {reportPeriods[period].replace("최근", "지난")} 대비</> : data ? "조회 기간·유형·검색 조건 기준 · 상태 필터 적용 전" : "집계 준비 중"}</p>
  </article>)}</section>;
}

export function ReportsScreen({ query, state, navigate, statistics, renderDetail, retry }: {
  query: ReportQuery; state: AdminReadState<ReportPage>; navigate: (query: ReportQuery) => void; statistics?: ReportStatistics;
  renderDetail?: (item: ReportSummary | null) => ReactNode; retry?: () => void;
}) {
  const [search, setSearch] = useState(query.q);
  const [selection, setSelection] = useState<{ query: string; id: string | null } | null>(null);
  const data = state.status === "ready" ? state.data : null;
  const queryKey = JSON.stringify(query);
  const selected = selection?.query === queryKey ? data?.items.find(item => item.id === selection.id) ?? null : data?.items[0] ?? null;
  const pages = data ? Math.max(1, Math.ceil(data.total / reportPageSize)) : 1;
  const start = Math.max(1, Math.min(query.page - 2, pages - 4));
  return <div className={styles.screen}>
    <div className={`${shared.heading} ${styles.heading}`}><div><h1>신고 및 문의 관리</h1><ReportPeriodFilter value={query.period} onChange={period => navigate({ ...query, period, page: 1 })} /></div>
      <p>접수된 신고를 처리하고 문의에 답변하세요.</p></div>
    <Statistics data={state.status === "ready" ? statistics : undefined} period={query.period} />
    <div className={styles.workspace}>
      <section className={`${shared.panel} ${shared.contentsPanel} ${styles.list}`} aria-label="신고 및 문의 목록" data-report-list={state.status}>
        <div className={shared.modes} role="group" aria-label="신고 처리 상태 필터">{([["", "전체 신고"], ["waiting", "검토 대기"], ["reviewing", "처리 중"], ["completed", "처리 완료"]] as const).map(([status, label]) =>
          <button key={status} type="button" aria-pressed={query.status === status} onClick={() => navigate({ ...query, status, page: 1 })}>{label}</button>)}</div>
        <div className={shared.listToolbar}><form role="search" className={shared.searchForm} onSubmit={event => { event.preventDefault(); navigate({ ...query, q: search.trim(), page: 1 }); }}>
          <button type="submit" aria-label="검색"><img src="/admin/search.svg" alt="" /></button><label className={shared.srOnly} htmlFor="report-search">신고 및 문의 검색</label>
          <input id="report-search" value={search} onChange={event => setSearch(event.target.value)} maxLength={300} placeholder="접수 번호, 콘텐츠명, 사유로 검색" autoComplete="off" />
        </form><label>유형 <select aria-label="신고 및 문의 유형" value={query.kind} onChange={event => navigate({ ...query, kind: event.target.value as ReportQuery["kind"], page: 1 })}>{Object.entries(reportKinds).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label></div>
        <div className={shared.resultBar}><p>신고 접수 내역 <strong>{data ? data.total.toLocaleString("ko-KR") : "—"}</strong></p></div>
        <div className={`${shared.tableScroll} ${styles.table}`} role="region" aria-label="신고 및 문의 목록 표, 가로 스크롤 가능" tabIndex={0}><table>
          <caption className={shared.srOnly}>신고 번호를 선택하면 상세 정보를 확인할 수 있습니다.</caption><colgroup>{Array.from({ length: 5 }, (_, i) => <col key={i} />)}</colgroup>
          <thead><tr>{["신고번호", "유형", "사유", "상태", "접수일"].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead>
          <tbody>{data?.items.map(item => <tr key={item.id} data-report-row={item.id} data-selected={item.id === selected?.id} onClick={() => setSelection({ query: queryKey, id: item.id })}>
            <td><button type="button" aria-label={`${item.number} ${reportKind(item.kind, true)} 상세`} aria-pressed={item.id === selected?.id} aria-controls="report-detail-title" onClick={() => setSelection({ query: queryKey, id: item.id })}><span className={styles.selection} aria-hidden="true" />{item.number}</button></td>
            <td>{reportKind(item.kind)}</td><td title={item.reason ?? item.title ?? undefined}>{item.reason ?? item.title ?? "상세에서 확인"}</td><td><ReportBadge status={item.status} /><small className={styles.rawStatus}>{rawReportStatuses[item.rawStatus]}</small></td><td><time dateTime={item.receivedAt ?? undefined}>{reportDate(item.receivedAt)}</time></td>
          </tr>)}</tbody>
        </table></div>
        {state.status !== "ready" ? <AdminStatus subject="신고·문의" state={state} retry={retry} /> : !data?.items.length ? <div className={shared.feedback} role="status" data-report-empty><h2>{data?.total ? "이 페이지에 신고·문의가 없습니다" : "조회된 신고·문의가 없습니다"}</h2><p>검색어나 조회 기간, 상태 필터를 확인해 주세요.</p>
          <button type="button" className={shared.secondaryButton} onClick={() => { setSearch(""); navigate(initialReportQuery); }}>조건 초기화</button></div> : null}
        <nav className={shared.pagination} aria-label="신고 및 문의 페이지"><button type="button" aria-label="이전 페이지" disabled={!data || query.page <= 1} onClick={() => navigate({ ...query, page: query.page - 1 })}><img src="/admin/previous.svg" alt="" /></button>
          {start > 1 && <><button type="button" aria-label="1페이지" onClick={() => navigate({ ...query, page: 1 })}>1</button>{start > 2 && <span>…</span>}</>}
          {Array.from({ length: Math.min(5, pages) }, (_, i) => start + i).map(page => <button type="button" key={page} disabled={!data} aria-label={`${page}페이지`} aria-current={page === query.page ? "page" : undefined} onClick={() => navigate({ ...query, page })}>{page}</button>)}
          {start + 4 < pages && <span>…</span>}<button type="button" aria-label="다음 페이지" disabled={!data || query.page >= pages} onClick={() => navigate({ ...query, page: query.page + 1 })}><img src="/admin/next.svg" alt="" /></button>
        </nav>
      </section>
      {renderDetail ? renderDetail(selected) : <ReportDetailPanel report={null} />}
    </div>
    <p className={styles.availability}>완료 상태에는 해결·반려·답변 완료·종료가 포함됩니다. 답변은 문의한 회원의 문의 내역에 표시됩니다.</p>
  </div>;
}
