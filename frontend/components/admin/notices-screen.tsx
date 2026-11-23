"use client";

import { useCallback, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import type { AdminFailure, AdminReadState } from "@/lib/admin-content";
import { initialNoticeQuery, loadAdminNotice, loadAdminNotices, noticeDate, noticePageSize, noticeQueryString, noticeStatuses, type NoticeDraft, type NoticePage, type NoticeQuery, type NoticeRecord, type NoticeStatistics } from "@/lib/admin-notices";
import { memberSupportFailure } from "@/lib/admin-read-contract";
import { noticeCompletedVersion, noticeFlowFor, subscribeNoticeChanges } from "@/lib/admin-notice-flow";
import { PendingNoticeActions } from "./notice-action-status";
import { useAdminRead } from "./use-admin-read";
import { AdminStatus } from "./admin-status";
import { NoticeComposePanel } from "./notice-compose-panel";
import { NoticeDetailPanel } from "./notice-detail-panel";
import shared from "./admin.module.css";
import panels from "./reports.module.css";
import styles from "./notices.module.css";

export function NoticesRoute() {
  const [query, setQuery] = useState(initialNoticeQuery);
  const revision = useSyncExternalStore(subscribeNoticeChanges, noticeCompletedVersion, noticeCompletedVersion);
  const read = useCallback((signal: AbortSignal) => loadAdminNotices(query, signal), [query]);
  const { state, retry } = useAdminRead(`${noticeQueryString(query)}:${query.q}:${revision}`, read, memberSupportFailure);
  const [denied, setDenied] = useState<AdminFailure | null>(null);
  const [editing, setEditing] = useState<NoticeRecord | null>(null);
  const newNotice = useCallback(() => setEditing(null), []);
  const effective = denied ?? state;
  const data = effective.status === "ready" ? effective.data : null;
  const editingInScope = editing && data?.capabilities?.disabledReason === null && data.capabilities.scope &&
    editing.actions?.scope?.actorUserId === data.capabilities.scope.actorUserId &&
    editing.actions.scope.adminAccountId === data.capabilities.scope.adminAccountId ? editing : null;
  const statistics = data?.statistics ? Object.fromEntries(Object.entries(data.statistics).map(([key, count]) => [key, { count, change: null }])) as NoticeStatistics : undefined;
  return <><PendingNoticeActions actor={data?.capabilities?.scope?.actorUserId} /><NoticesScreen query={query} navigate={setQuery} state={effective} statistics={statistics}
    retry={() => { setDenied(null); retry(); }}
    compose={data ? <NoticeComposePanel key={`${data.capabilities?.scope?.actorUserId}:${editingInScope?.id ?? "new"}:${editingInScope?.updatedAt ?? ""}`} notice={editingInScope} capabilities={data.capabilities} onNew={newNotice} /> : null}
    renderDetail={(id, close) => <ConnectedNoticeDetail key={`${id}:${revision}`} id={id} onClose={close} denied={setDenied} onEdit={notice => {
      const flow = noticeFlowFor(notice.id, notice.actions?.scope?.actorUserId);
      if (flow.state().phase === "stopped") flow.release();
      setEditing(notice); close();
    }} />} /></>;
}

function ConnectedNoticeDetail({ id, onClose, onEdit, denied }: { id: string; onClose: () => void; onEdit: (notice: NoticeRecord) => void; denied: (failure: AdminFailure) => void }) {
  const read = useCallback((signal: AbortSignal) => loadAdminNotice(id, signal), [id]);
  const { state, retry } = useAdminRead(id, read, memberSupportFailure);
  useEffect(() => { if (state.status === "forbidden" || state.status === "login-required") denied(state); }, [state, denied]);
  return <NoticeDetailPanel notice={state.status === "ready" ? state.data : null} state={state} retry={retry} onClose={onClose} onEdit={onEdit} />;
}

export function NoticesScreen({ query, navigate, state, statistics, initialDraft, retry, compose, renderDetail }: {
  query: NoticeQuery; navigate: (query: NoticeQuery) => void; state: AdminReadState<NoticePage>; statistics?: NoticeStatistics; initialDraft?: NoticeDraft;
  retry?: () => void; compose?: ReactNode; renderDetail?: (id: string, close: () => void) => ReactNode;
}) {
  const [search, setSearch] = useState(query.q);
  const [selection, setSelection] = useState<{ query: string; id: string } | null>(null);
  const [open, setOpen] = useState(false);
  const data = state.status === "ready" ? state.data : null, stats = data ? statistics : undefined;
  const queryKey = JSON.stringify(query);
  const selected = selection?.query === queryKey ? data?.items.find(item => item.id === selection.id) ?? null : null;
  const pages = data ? Math.max(1, Math.ceil(data.total / noticePageSize)) : 1;
  const start = Math.max(1, Math.min(query.page - 2, pages - 4));
  function changeQuery(next: NoticeQuery) { setOpen(false); setSelection(null); navigate(next); }
  function showDetail(id: string) { setSelection({ query: queryKey, id }); setOpen(true); }
  return <div className={styles.screen}>
    <div className={`${shared.heading} ${styles.heading}`}><h1>알림 및 공지</h1><p>서비스 공지와 사용자 대상 알림의 등록•발송을 관리하세요.</p></div>
    <div className={styles.workspace}>
      <div className={styles.listColumn}>
        <section className={`${panels.statistics} ${styles.statistics}`} aria-label="알림 및 공지 통계" data-notice-statistics>{([["all", "전체 공지"], ["published", "게시 중"], ["scheduled", "예약 발송"]] as const).map(([key, label]) =>
          <article key={key}><h2>{label}</h2><strong>{stats ? `${stats[key].count.toLocaleString("ko-KR")}건` : "—"}</strong><p>{stats ? "검색 조건 기준 · 상태 필터 적용 전" : "집계 준비 중"}</p></article>)}</section>
        <section className={`${shared.panel} ${shared.contentsPanel} ${styles.list}`} aria-label="알림 및 공지 목록" data-notice-list={state.status}>
          <div className={shared.modes} role="group" aria-label="공지 상태 필터">{([["", "전체 공지"], ["published", "게시 중"], ["scheduled", "예약 발송"], ["draft", "임시 저장"]] as const).map(([status, label]) =>
            <button key={status} type="button" aria-pressed={query.status === status} onClick={() => changeQuery({ ...query, status, page: 1 })}>{label}</button>)}</div>
          <form role="search" className={shared.searchForm} onSubmit={event => { event.preventDefault(); changeQuery({ ...query, q: search.trim(), page: 1 }); }}>
            <button type="submit" aria-label="검색"><img src="/admin/search.svg" alt="" /></button><label className={shared.srOnly} htmlFor="notice-search">공지 제목으로 검색</label>
            <input id="notice-search" value={search} onChange={event => setSearch(event.target.value)} maxLength={300} placeholder="공지 제목으로 검색" autoComplete="off" /></form>
          <div className={shared.resultBar}><p>공지 발송 목록 <strong>{data ? data.total.toLocaleString("ko-KR") : "—"}</strong></p></div>
          <div className={`${shared.tableScroll} ${styles.table}`} role="region" aria-label="공지 목록 표, 가로 스크롤 가능" tabIndex={0}><table>
            <caption className={shared.srOnly}>제목 또는 공지 내용상세를 선택하면 공지 내용을 확인할 수 있습니다.</caption><colgroup>{Array.from({ length: 5 }, (_, i) => <col key={i} />)}</colgroup>
            <thead><tr>{["제목", "상태", "게시•발송 예정일", "읽은 회원"].map(label => <th scope="col" key={label}>{label}</th>)}<th scope="col"><span className={shared.srOnly}>상세</span></th></tr></thead>
            <tbody>{data?.items.map(item => <tr key={item.id} data-notice-row={item.id} data-selected={selected?.id === item.id}>
              <td><button type="button" aria-pressed={selected?.id === item.id} aria-haspopup="dialog" aria-controls="notice-detail" title={item.title} onClick={() => showDetail(item.id)}><span className={panels.selection} aria-hidden="true" /><span>{item.title}</span></button></td>
              <td>{item.status ? <span className={`${shared.statusChip} ${styles.badge}`} data-notice-status={item.status}>{noticeStatuses[item.status]}</span> : "—"}</td>
              <td><time dateTime={item.publishedAt ?? item.scheduledAt ?? undefined}>{noticeDate(item.publishedAt ?? item.scheduledAt)}</time></td><td>{item.status === "published" ? `${item.readCount.toLocaleString("ko-KR")}명` : "—"}</td>
              <td><button type="button" aria-label={`${item.title} 공지 내용상세`} aria-haspopup="dialog" aria-controls="notice-detail" onClick={() => showDetail(item.id)}>공지 내용상세<img src="/admin/notice-forward.svg" alt="" /></button></td>
            </tr>)}</tbody>
          </table></div>
          {state.status !== "ready" ? <AdminStatus subject="알림·공지" state={state} retry={retry} /> : !data?.items.length ? <div className={shared.feedback} role="status" data-notice-empty><h2>{data?.total ? "이 페이지에 공지가 없습니다" : "조회된 공지가 없습니다"}</h2><p>검색어나 상태 필터를 확인해 주세요.</p><button type="button" className={shared.secondaryButton} onClick={() => { setSearch(""); changeQuery(initialNoticeQuery); }}>조건 초기화</button></div> : null}
          <nav className={shared.pagination} aria-label="공지 페이지"><button type="button" aria-label="이전 페이지" disabled={!data || query.page <= 1} onClick={() => changeQuery({ ...query, page: query.page - 1 })}><img src="/admin/previous.svg" alt="" /></button>
            {start > 1 && <><button type="button" aria-label="1페이지" onClick={() => changeQuery({ ...query, page: 1 })}>1</button>{start > 2 && <span>…</span>}</>}
            {Array.from({ length: Math.min(5, pages) }, (_, i) => start + i).map(page => <button type="button" key={page} disabled={!data} aria-label={`${page}페이지`} aria-current={page === query.page ? "page" : undefined} onClick={() => changeQuery({ ...query, page })}>{page}</button>)}
            {start + 4 < pages && <span>…</span>}<button type="button" aria-label="다음 페이지" disabled={!data || query.page >= pages} onClick={() => changeQuery({ ...query, page: query.page + 1 })}><img src="/admin/next.svg" alt="" /></button>
          </nav>
        </section>
      </div>
      {compose === undefined ? <NoticeComposePanel initialDraft={initialDraft} /> : compose}
    </div>
    <p className={styles.availability}>읽은 회원은 해당 공지 알림을 읽음 처리한 회원 수입니다. 예약은 한국 시간 기준이며, 게시 후에는 수정할 수 없습니다.</p>
    {open && selected && (renderDetail ? renderDetail(selected.id, () => setOpen(false)) : <NoticeDetailPanel key={selected.id} notice={selected as NoticeRecord} onClose={() => setOpen(false)} />)}
  </div>;
}
