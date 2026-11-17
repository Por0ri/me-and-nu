"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { adminDate, adminQueryString, contentStatuses, judgmentStatuses, loadAdminContents, parseAdminQuery,
  type AdminContentPage, type AdminContentQuery, type AdminReadState } from "@/lib/admin-content";
import { useAdminRead } from "./use-admin-read";
import { AdminStatus } from "./admin-status";
import { ContentStatistics } from "./content-statistics";
import styles from "./admin.module.css";

export function ContentsRoute() {
  const params = useSearchParams();
  let query: AdminContentQuery;
  try { query = parseAdminQuery(params); }
  catch { return <div className={styles.feedback} role="alert"><h2>조회 조건을 확인해 주세요</h2><Link href="/admin/contents">조건 초기화</Link></div>; }
  return <ConnectedContents key={adminQueryString(query)} query={query} />;
}

function ConnectedContents({ query }: { query: AdminContentQuery }) {
  const router = useRouter();
  const read = useCallback((signal: AbortSignal) => loadAdminContents(query, signal), [query]);
  const { state, retry } = useAdminRead(adminQueryString(query), read);
  return <ContentsScreen query={query} state={state} retry={retry}
    navigate={next => router.push(`/admin/contents?${adminQueryString(next)}`, { scroll: false })} />;
}

export function ContentsScreen({ query, state, retry, navigate }: {
  query: AdminContentQuery; state: AdminReadState<AdminContentPage>; retry: () => void; navigate: (query: AdminContentQuery) => void;
}) {
  const [search, setSearch] = useState(query.q);
  const review = query.mode === "review";
  const [filtersOpen, setFiltersOpen] = useState(Boolean(query.judgmentStatus || query.status || query.pageSize !== 20));
  const pages = state.status === "ready" ? Math.max(1, Math.ceil(state.data.total / query.pageSize)) : 1;
  const start = Math.max(1, Math.min(query.page - 2, pages - 4));
  return <>
    <div className={styles.heading}><h1>콘텐츠 관리</h1><p>카드뉴스의 등록부터 검수, 발행까지 관리합니다.</p></div>
    <ContentStatistics />
    <section className={`${styles.panel} ${styles.contentsPanel}`} aria-label="콘텐츠 목록" data-admin-state={state.status}>
      <div className={styles.modes} role="group" aria-label="콘텐츠 조회 모드">
        <button type="button" aria-pressed={!review}
          onClick={() => navigate({ ...query, mode: "all", page: 1 })}>전체 콘텐츠</button>
        <button type="button" disabled title="실제 게시 상태의 조회 기준을 준비 중입니다" aria-describedby="admin-unavailable-tabs">게시 중</button>
        <button type="button" aria-pressed={review}
          onClick={() => navigate({ ...query, mode: "review", page: 1 })}>검수 대기</button>
        <button type="button" disabled title="예약 발행 조회를 준비 중입니다" aria-describedby="admin-unavailable-tabs">예약 발행</button>
        <button type="button" disabled title="신고 접수 조회를 준비 중입니다" aria-describedby="admin-unavailable-tabs">신고 접수</button>
      </div>
      <div className={styles.listToolbar}>
        <form className={styles.searchForm} role="search" onSubmit={event => { event.preventDefault(); navigate({ ...query, q: search.trim(), page: 1 }); }}>
          <button type="submit" aria-label="검색"><img src="/admin/search.svg" alt="" /></button>
          <label className={styles.srOnly} htmlFor="admin-content-search">콘텐츠명 검색</label>
          <input id="admin-content-search" value={search} onChange={event => setSearch(event.target.value)} maxLength={300} placeholder="콘텐츠명으로 검색" />
        </form>
        <button className={styles.filterToggle} type="button" aria-expanded={filtersOpen} aria-controls="admin-content-filters" onClick={() => setFiltersOpen(!filtersOpen)}>
          상세 필터{query.judgmentStatus || query.status ? " · 적용 중" : ""}
        </button>
        {state.status === "ready" && <Link className={styles.registerLink} href={`/admin/contents/new?${adminQueryString(query)}`}>콘텐츠 등록</Link>}
      </div>
      <div id="admin-content-filters" className={styles.filters} hidden={!filtersOpen}>
        <label>검수 상태<select aria-label="검수 상태" value={query.judgmentStatus} onChange={event => navigate({ ...query, judgmentStatus: event.target.value as AdminContentQuery["judgmentStatus"], page: 1 })}>
          <option value="">전체</option>{judgmentStatuses.map(value => <option key={value} value={value}>{value}</option>)}
        </select></label>
        <label>콘텐츠 상태<select aria-label="콘텐츠 상태" value={query.status} onChange={event => navigate({ ...query, status: event.target.value as AdminContentQuery["status"], page: 1 })}>
          <option value="">전체</option>{contentStatuses.map(value => <option key={value} value={value}>{value}</option>)}
        </select></label>
        <label>페이지당<select aria-label="페이지당" value={query.pageSize} onChange={event => navigate({ ...query, pageSize: Number(event.target.value), page: 1 })}>
          {[...new Set([20, 50, 100, query.pageSize])].sort((a, b) => a - b).map(value => <option key={value} value={value}>{value}개</option>)}
        </select></label>
      </div>
      {review && <p className={styles.note}>needs_review 콘텐츠에 검색·상태 필터를 함께 적용합니다. active는 우선 확인, pending_review는 일반 비공개 대기입니다.<br />active → pending_review → 그 외 · 같은 우선순위 내 생성 시각·ID 최신순</p>}
      {state.status !== "ready" ? <AdminStatus state={state} retry={retry} /> : <>
        <div className={styles.resultBar}><p>콘텐츠 목록 <strong>{state.data.total.toLocaleString("ko-KR")}</strong></p>
          <span>등록일 · 한국 시간</span></div>
        {state.data.items.length === 0 ? <div className={styles.feedback} role="status" data-admin-empty>
          <h2>{state.data.total ? "이 페이지에 콘텐츠가 없습니다" : "조회된 콘텐츠가 없습니다"}</h2>
          <p>검색어나 상태 조건을 확인해 주세요.</p>
          {query.page > 1 && <button className={styles.secondaryButton} type="button" onClick={() => navigate({ ...query, page: 1 })}>첫 페이지로</button>}
        </div> : <div className={`${styles.tableScroll} ${styles.contentTable}`} tabIndex={0} role="region" aria-label="콘텐츠 목록 표, 가로 스크롤 가능"><table><caption className={styles.srOnly}>콘텐츠 ID, 제목, 분류, 분야, 등록일, 검수·콘텐츠 상태, 조회수. 미연결 정보는 —로 표시합니다.</caption>
          <colgroup><col /><col /><col /><col /><col /><col /><col /></colgroup>
          <thead><tr><th scope="col"><span className={styles.srOnly}>ID</span></th><th scope="col">콘텐츠</th><th scope="col">분류</th><th scope="col">분야</th><th scope="col">등록일</th><th scope="col">상태</th><th scope="col">조회수</th></tr></thead>
          <tbody>{state.data.items.map(item => <tr key={item.contentId} data-admin-content={item.contentId}>
            <td>{item.contentId}</td><td><Link href={`/admin/contents/${item.contentId}?${adminQueryString(query)}`}>{item.title || "제목 없음"}</Link>
              {review && <span className={item.status === "active" ? styles.reviewPriority : styles.reviewHint}>
                {item.status === "active" ? "우선 확인" : item.status === "pending_review" ? "일반 비공개 대기" : "상태 확인 필요"}
              </span>}</td>
            <td><span aria-label="분류 연결 전">—</span></td><td><span aria-label="분야 연결 전">—</span></td>
            <td><time dateTime={item.createdAt} title={adminDate(item.createdAt)}>{new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(item.createdAt)).replaceAll("-", ".")}</time></td>
            <td><div className={styles.storedStatuses}>
              <span className={styles.statusChip} data-judgment={item.judgmentStatus} title={`검수 상태: ${item.judgmentStatus}`}>
                {item.judgmentStatus === "needs_review" ? <>검수 대기<span className={styles.srOnly}> needs_review</span></> : item.judgmentStatus}
              </span>
              <span className={styles.storedStatus} title={`콘텐츠 상태: ${item.status}`}>{item.status}</span>
            </div></td>
            <td><span aria-label="조회수 연결 전">—</span></td>
          </tr>)}</tbody>
        </table></div>}
        <nav className={styles.pagination} aria-label="콘텐츠 페이지">
          <button aria-label="이전 페이지" disabled={query.page <= 1} onClick={() => navigate({ ...query, page: query.page - 1 })}><img src="/admin/previous.svg" alt="" /></button>
          {start > 1 && <><button aria-label="1페이지" onClick={() => navigate({ ...query, page: 1 })}>1</button>{start > 2 && <span aria-hidden="true">…</span>}</>}
          {Array.from({ length: Math.min(5, pages) }, (_, index) => start + index).map(page => <button key={page} aria-label={`${page}페이지`} aria-current={page === query.page ? "page" : undefined}
            onClick={() => navigate({ ...query, page })}>{page}</button>)}
          {start + 4 < pages && <>{start + 5 < pages && <span aria-hidden="true">…</span>}<button aria-label={`${pages}페이지`} onClick={() => navigate({ ...query, page: pages })}>{pages}</button></>}
          <button aria-label="다음 페이지" disabled={query.page >= pages} onClick={() => navigate({ ...query, page: query.page + 1 })}><img src="/admin/next.svg" alt="" /></button>
        </nav>
      </>}
      <details className={styles.availabilityNote}>
        <summary>표시 정보 안내 · 일부 항목 준비 중</summary>
        <p id="admin-unavailable-tabs">게시 중·예약 발행·신고 접수 조회는 준비 중입니다. 통계와 분류·분야·조회수의 — 표시는 확인할 수 없는 정보이며, 0을 의미하지 않습니다.</p>
        <p>검수 상태와 콘텐츠 상태는 별개입니다. active만으로 실제 미리보기·피드 노출 여부를 판단할 수 없습니다.</p>
      </details>
    </section>
  </>;
}
