"use client";

import { useState } from "react";
import type { AdminReadState } from "@/lib/admin-content";
import { initialNoticeQuery, noticeDate, noticePageSize, noticeStatuses, noticeUnavailable, type NoticeDraft, type NoticePage, type NoticeQuery, type NoticeStatistics } from "@/lib/admin-notices";
import { AdminStatus } from "./admin-status";
import { NoticeComposePanel } from "./notice-compose-panel";
import { NoticeDetailPanel } from "./notice-detail-panel";
import shared from "./admin.module.css";
import panels from "./reports.module.css";
import styles from "./notices.module.css";

export function NoticesRoute() {
  const [query, setQuery] = useState(initialNoticeQuery);
  return <NoticesScreen query={query} navigate={setQuery} state={noticeUnavailable} />;
}

export function NoticesScreen({ query, navigate, state, statistics, initialDraft }: {
  query: NoticeQuery; navigate: (query: NoticeQuery) => void; state: AdminReadState<NoticePage>; statistics?: NoticeStatistics; initialDraft?: NoticeDraft;
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
          <article key={key}><h2>{label}</h2><strong>{stats ? `${stats[key].count.toLocaleString("ko-KR")}건` : "—"}</strong><p>{stats?.[key].change != null ? <><b>{stats[key].change === 0 ? "변동 없음" : `${stats[key].change > 0 ? "▲" : "▼"} ${Math.abs(stats[key].change)}건`}</b> • 지난 7일 대비</> : "집계 준비 중"}</p></article>)}</section>
        <section className={`${shared.panel} ${shared.contentsPanel} ${styles.list}`} aria-label="알림 및 공지 목록" data-notice-list={state.status}>
          <div className={shared.modes} role="group" aria-label="공지 상태 필터">{([["", "전체 공지"], ["published", "게시 중"], ["scheduled", "예약 발송"]] as const).map(([status, label]) =>
            <button key={status} type="button" aria-pressed={query.status === status} onClick={() => changeQuery({ ...query, status, page: 1 })}>{label}</button>)}</div>
          <form role="search" className={shared.searchForm} onSubmit={event => { event.preventDefault(); changeQuery({ ...query, q: search.trim(), page: 1 }); }}>
            <button type="submit" aria-label="검색"><img src="/admin/search.svg" alt="" /></button><label className={shared.srOnly} htmlFor="notice-search">공지 제목으로 검색</label>
            <input id="notice-search" value={search} onChange={event => setSearch(event.target.value)} maxLength={300} placeholder="공지 제목으로 검색" autoComplete="off" /></form>
          <div className={shared.resultBar}><p>공지 발송 목록 <strong>{data ? data.total.toLocaleString("ko-KR") : "—"}</strong></p></div>
          <div className={`${shared.tableScroll} ${styles.table}`} role="region" aria-label="공지 목록 표, 가로 스크롤 가능" tabIndex={0}><table>
            <caption className={shared.srOnly}>제목 또는 공지 내용상세를 선택하면 공지 내용을 확인할 수 있습니다.</caption><colgroup>{Array.from({ length: 5 }, (_, i) => <col key={i} />)}</colgroup>
            <thead><tr>{["제목", "상태", "게시•발송 예정일", "조회수"].map(label => <th scope="col" key={label}>{label}</th>)}<th scope="col"><span className={shared.srOnly}>상세</span></th></tr></thead>
            <tbody>{data?.items.map(item => <tr key={item.id} data-notice-row={item.id} data-selected={selected?.id === item.id}>
              <td><button type="button" aria-pressed={selected?.id === item.id} aria-haspopup="dialog" aria-controls="notice-detail" title={item.title} onClick={() => showDetail(item.id)}><span className={panels.selection} aria-hidden="true" /><span>{item.title}</span></button></td>
              <td>{item.status ? <span className={`${shared.statusChip} ${styles.badge}`} data-notice-status={item.status}>{noticeStatuses[item.status]}</span> : "—"}</td>
              <td><time dateTime={item.publishedAt ?? undefined}>{noticeDate(item.publishedAt)}</time></td><td>{item.views?.toLocaleString("ko-KR") ?? "—"}</td>
              <td><button type="button" aria-label={`${item.title} 공지 내용상세`} aria-haspopup="dialog" aria-controls="notice-detail" onClick={() => showDetail(item.id)}>공지 내용상세<img src="/admin/notice-forward.svg" alt="" /></button></td>
            </tr>)}</tbody>
          </table></div>
          {state.status !== "ready" ? <AdminStatus subject="알림·공지" state={state} /> : !data?.items.length ? <div className={shared.feedback} role="status" data-notice-empty><h2>{data?.total ? "이 페이지에 공지가 없습니다" : "조회된 공지가 없습니다"}</h2><p>검색어나 상태 필터를 확인해 주세요.</p><button type="button" className={shared.secondaryButton} onClick={() => { setSearch(""); changeQuery(initialNoticeQuery); }}>조건 초기화</button></div> : null}
          <nav className={shared.pagination} aria-label="공지 페이지"><button type="button" aria-label="이전 페이지" disabled={!data || query.page <= 1} onClick={() => changeQuery({ ...query, page: query.page - 1 })}><img src="/admin/previous.svg" alt="" /></button>
            {start > 1 && <><button type="button" aria-label="1페이지" onClick={() => changeQuery({ ...query, page: 1 })}>1</button>{start > 2 && <span>…</span>}</>}
            {Array.from({ length: Math.min(5, pages) }, (_, i) => start + i).map(page => <button type="button" key={page} disabled={!data} aria-label={`${page}페이지`} aria-current={page === query.page ? "page" : undefined} onClick={() => changeQuery({ ...query, page })}>{page}</button>)}
            {start + 4 < pages && <span>…</span>}<button type="button" aria-label="다음 페이지" disabled={!data || query.page >= pages} onClick={() => changeQuery({ ...query, page: query.page + 1 })}><img src="/admin/next.svg" alt="" /></button>
          </nav>
        </section>
      </div>
      <NoticeComposePanel initialDraft={initialDraft} />
    </div>
    <p className={styles.availability}>조회·통계 연결 준비 중 · 공지 발행·예약·수정·삭제·알림 발송은 사용할 수 없습니다.</p>
    {open && selected && <NoticeDetailPanel key={selected.id} notice={selected} onClose={() => setOpen(false)} />}
  </div>;
}
