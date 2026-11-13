"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { adminDate, adminQueryString, contentStatuses, judgmentStatuses, loadAdminContents, parseAdminQuery,
  type AdminContentPage, type AdminContentQuery, type AdminReadState } from "@/lib/admin-content";
import { useAdminRead } from "./use-admin-read";
import { AdminStatus } from "./admin-status";
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
  const title = review ? "검수 대기" : "전체 콘텐츠";
  const pages = state.status === "ready" ? Math.max(1, Math.ceil(state.data.total / query.pageSize)) : 1;
  const start = Math.max(1, Math.min(query.page - 2, pages - 4));
  return <>
    <div className={styles.heading}><h1>콘텐츠 관리</h1><p>콘텐츠를 검색하고 검수 상태를 확인하세요.</p></div>
    {state.status === "ready" && <a className={styles.backLink} href={`/admin/contents/new?${adminQueryString(query)}`}>콘텐츠 등록</a>}
    <section className={styles.panel} aria-label="콘텐츠 목록" data-admin-state={state.status}>
      <div className={styles.modes} role="group" aria-label="콘텐츠 조회 모드">
        <button type="button" aria-pressed={!review} className={!review ? styles.primaryButton : styles.secondaryButton}
          onClick={() => navigate({ ...query, mode: "all", page: 1 })}>전체 콘텐츠</button>
        <button type="button" aria-pressed={review} className={review ? styles.primaryButton : styles.secondaryButton}
          onClick={() => navigate({ ...query, mode: "review", page: 1 })}>검수 대기</button>
      </div>
      <div className={styles.panelTitle}>{title}</div>
      <form className={styles.filters} onSubmit={event => { event.preventDefault(); navigate({ ...query, q: search.trim(), page: 1 }); }}>
        <label className={styles.search}><span className={styles.srOnly}>콘텐츠명 검색</span><img src="/admin/search.svg" alt="" />
          <input value={search} onChange={event => setSearch(event.target.value)} maxLength={300} placeholder="콘텐츠명으로 검색" />
        </label><button className={styles.primaryButton} type="submit">검색</button>
        <label>검수 상태<select aria-label="검수 상태" value={query.judgmentStatus} onChange={event => navigate({ ...query, judgmentStatus: event.target.value as AdminContentQuery["judgmentStatus"], page: 1 })}>
          <option value="">전체</option>{judgmentStatuses.map(value => <option key={value} value={value}>{value}</option>)}
        </select></label>
        <label>콘텐츠 상태<select aria-label="콘텐츠 상태" value={query.status} onChange={event => navigate({ ...query, status: event.target.value as AdminContentQuery["status"], page: 1 })}>
          <option value="">전체</option>{contentStatuses.map(value => <option key={value} value={value}>{value}</option>)}
        </select></label>
        <label>페이지당<select aria-label="페이지당" value={query.pageSize} onChange={event => navigate({ ...query, pageSize: Number(event.target.value), page: 1 })}>
          {[...new Set([20, 50, 100, query.pageSize])].sort((a, b) => a - b).map(value => <option key={value} value={value}>{value}개</option>)}
        </select></label>
      </form>
      <p className={styles.note}>{review && "needs_review 콘텐츠에 검색·상태 필터를 함께 적용합니다. active는 우선 확인, pending_review는 일반 비공개 대기입니다. "}
        검수 상태와 콘텐츠 상태는 별개입니다. active만으로 실제 미리보기·피드 노출 여부를 판단할 수 없습니다.</p>
      {state.status !== "ready" ? <AdminStatus state={state} retry={retry} /> : <>
        <div className={styles.resultBar}><p>{title} <strong>{state.data.total.toLocaleString("ko-KR")}</strong></p>
          <span>{review ? "active → pending_review → 그 외 · 같은 우선순위 내 생성 시각·ID 최신순" : "생성 시각 최신순"} · 한국 시간</span></div>
        {state.data.items.length === 0 ? <div className={styles.feedback} role="status" data-admin-empty>
          <h2>{state.data.total ? "이 페이지에 콘텐츠가 없습니다" : "조회된 콘텐츠가 없습니다"}</h2>
          <p>검색어나 상태 조건을 확인해 주세요.</p>
          {query.page > 1 && <button className={styles.secondaryButton} type="button" onClick={() => navigate({ ...query, page: 1 })}>첫 페이지로</button>}
        </div> : <div className={styles.tableScroll}><table><caption className={styles.srOnly}>콘텐츠 ID, 제목, 검수 상태, 콘텐츠 상태, 생성 시각</caption>
          <thead><tr><th scope="col">ID</th><th scope="col">콘텐츠</th><th scope="col">검수 상태</th><th scope="col">콘텐츠 상태</th><th scope="col">생성 시각</th></tr></thead>
          <tbody>{state.data.items.map(item => <tr key={item.contentId} data-admin-content={item.contentId}>
            <td>{item.contentId}</td><td><Link href={`/admin/contents/${item.contentId}?${adminQueryString(query)}`}>{item.title || "제목 없음"}</Link>
              {review && <span className={item.status === "active" ? styles.reviewPriority : styles.reviewHint}>
                {item.status === "active" ? "우선 확인" : item.status === "pending_review" ? "일반 비공개 대기" : "상태 확인 필요"}
              </span>}</td>
            <td><span className={styles.statusChip}>{item.judgmentStatus}</span></td><td><span className={styles.statusChip}>{item.status}</span></td>
            <td><time dateTime={item.createdAt}>{adminDate(item.createdAt)}</time></td>
          </tr>)}</tbody>
        </table></div>}
        <nav className={styles.pagination} aria-label="콘텐츠 페이지">
          <button aria-label="이전 페이지" disabled={query.page <= 1} onClick={() => navigate({ ...query, page: query.page - 1 })}><img src="/admin/previous.svg" alt="" /></button>
          {Array.from({ length: Math.min(5, pages) }, (_, index) => start + index).map(page => <button key={page} aria-label={`${page}페이지`} aria-current={page === query.page ? "page" : undefined}
            onClick={() => navigate({ ...query, page })}>{page}</button>)}
          <button aria-label="다음 페이지" disabled={query.page >= pages} onClick={() => navigate({ ...query, page: query.page + 1 })}><img src="/admin/next.svg" alt="" /></button>
        </nav>
      </>}
    </section>
  </>;
}
