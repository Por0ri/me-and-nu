"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { AdminReadState } from "@/lib/admin-content";
import { loadAdminMembers, memberDate, memberPageSize, memberProviders, memberQueryString, memberStatuses, parseMemberQuery, privateEmail,
  type MemberPage, type MemberQuery, type MemberStatus } from "@/lib/admin-members";
import { AdminStatus } from "./admin-status";
import { MemberStatistics, type MemberStatisticsData } from "./member-statistics";
import { useMemberSearch } from "./member-search-context";
import { useAdminRead } from "./use-admin-read";
import { memberSupportFailure } from "@/lib/admin-read-contract";
import shared from "./admin.module.css";
import styles from "./members.module.css";

export function MemberBadge({ status }: { status: MemberStatus | null }) {
  return status ? <span className={styles.badge} data-member-status={status}>{memberStatuses[status]}</span> : <span className={styles.unknown}>상태 미연결</span>;
}

export function MembersRoute({ state, statistics }: { state?: AdminReadState<MemberPage>; statistics?: MemberStatisticsData }) {
  const params = useSearchParams(), router = useRouter();
  const { search, setSearch } = useMemberSearch();
  let query: MemberQuery;
  try { query = parseMemberQuery(params, search); }
  catch { return <div className={shared.feedback} role="alert"><h2>조회 조건을 확인해 주세요</h2><Link href="/admin/users">조건 초기화</Link></div>; }
  const navigate = (next: MemberQuery) => { setSearch(next.q); router.push(`/admin/users?${memberQueryString(next)}`, { scroll: false }); };
  return state ? <MembersScreen key={`${memberQueryString(query)}:${query.q}`} query={query} state={state} statistics={statistics} navigate={navigate} />
    : <ConnectedMembers query={query} navigate={navigate} />;
}

function ConnectedMembers({ query, navigate }: { query: MemberQuery; navigate: (query: MemberQuery) => void }) {
  const key = `${memberQueryString(query)}:${query.q}`;
  const read = useCallback((signal: AbortSignal) => loadAdminMembers(query, signal), [key]); // Search stays in component memory.
  const { state, retry } = useAdminRead(key, read, memberSupportFailure);
  return <MembersScreen key={key} query={query} state={state} navigate={navigate} retry={retry} />;
}

export function MembersScreen({ query, state, navigate, statistics, retry }: {
  query: MemberQuery; state: AdminReadState<MemberPage>; navigate: (query: MemberQuery) => void; statistics?: MemberStatisticsData; retry?: () => void;
}) {
  const [search, setSearch] = useState(query.q);
  const data = state.status === "ready" ? state.data : null;
  const pages = data ? Math.max(1, Math.ceil(data.total / memberPageSize)) : 1;
  const start = Math.max(1, Math.min(query.page - 2, pages - 4));
  return <div className={styles.list}>
    <div className={shared.heading}><h1>회원 관리</h1><p>가입한 회원의 상태와 이용 정보를 관리합니다.</p></div>
    <MemberStatistics data={state.status === "ready" ? statistics : undefined} />
    <section className={`${shared.panel} ${shared.contentsPanel} ${styles.listPanel}`} aria-label="회원 목록" data-member-list={state.status}>
      <div className={shared.modes} role="group" aria-label="회원 상태 필터">{[["", "전체 회원"], ...Object.entries(memberStatuses)].map(([status, label]) =>
        <button key={status} type="button" aria-pressed={query.status === status} onClick={() => navigate({ ...query, status: status as MemberQuery["status"], page: 1 })}>{label}</button>)}</div>
      <div className={shared.listToolbar}>
        <form role="search" className={shared.searchForm} onSubmit={event => { event.preventDefault(); navigate({ ...query, q: search.trim(), page: 1 }); }}>
          <button type="submit" aria-label="검색"><img src="/admin/search.svg" alt="" /></button><label className={shared.srOnly} htmlFor="member-search">회원 검색</label>
          <input id="member-search" value={search} onChange={event => setSearch(event.target.value)} maxLength={300} placeholder="닉네임, 이메일로 검색" autoComplete="off" />
        </form>
        <label className={styles.sort}><span className={shared.srOnly}>회원 정렬</span><select value={query.sort} onChange={event => navigate({ ...query, sort: event.target.value as MemberQuery["sort"], page: 1 })}>
          <option value="recent">최근 가입순</option><option value="oldest">오래된 가입순</option></select><img src="/admin/sort-down.svg" alt="" /></label>
        <div className={styles.providers} role="group" aria-label="가입 경로 필터">{[["", "전체"], ...Object.entries(memberProviders)].map(([provider, label]) =>
          <button type="button" key={provider} aria-pressed={query.provider === provider} onClick={() => navigate({ ...query, provider: provider as MemberQuery["provider"], page: 1 })}>{label}</button>)}</div>
        <button type="button" className={`${shared.secondaryButton} ${styles.export}`} disabled title="회원 내보내기 준비 중"><img src="/admin/download.svg" alt="" />CSV 내보내기</button>
      </div>
      <div className={shared.resultBar}><p>회원 목록 <strong>{data ? data.total.toLocaleString("ko-KR") : "—"}</strong></p></div>
      <div className={`${shared.tableScroll} ${styles.table}`} tabIndex={0} role="region" aria-label="회원 목록 표, 가로 스크롤 가능"><table>
        <caption className={shared.srOnly}>닉네임, 마스킹된 이메일, 가입 경로, 관심 분야, 최근 로그인, 상태 및 회원 상세</caption>
        <colgroup>{Array.from({ length: 8 }, (_, index) => <col key={index} />)}</colgroup>
        <thead><tr>{["선택", "닉네임", "이메일", "가입 경로", "관심 분야", "최근 로그인", "상태", "상세"].map((label, index) => <th key={label} scope="col">{index === 0 || index === 7 ? <span className={shared.srOnly}>{label}</span> : label}</th>)}</tr></thead>
        <tbody>{data?.items.map(item => <tr key={item.id} data-member-row={item.id}>
          <td><input type="checkbox" disabled aria-label={`${item.nickname} 선택 준비 중`} /></td><td>{item.nickname}</td><td>{privateEmail(item.maskedEmail)}</td><td>{item.provider ? memberProviders[item.provider] : "—"}</td><td>{item.domain ?? "—"}</td>
          <td>{memberDate(item.lastLoginAt)}</td><td><MemberBadge status={item.status} /></td><td><Link href={`/admin/users/${encodeURIComponent(item.id)}?${memberQueryString(query)}`} aria-label={`${item.nickname} 회원 상세`}>회원 상세 <img src="/admin/member-chevron.svg" alt="" /></Link></td>
        </tr>)}</tbody>
      </table></div>
      {state.status !== "ready" ? <AdminStatus subject="회원" state={state} retry={retry} /> : data?.items.length === 0 ? <div className={shared.feedback} role="status" data-member-empty>
        <h2>{data.total ? "이 페이지에 회원이 없습니다" : "조회된 회원이 없습니다"}</h2><p>검색어나 필터를 확인해 주세요.</p>
        <button type="button" className={shared.secondaryButton} onClick={() => navigate({ q: "", status: "", provider: "", sort: "recent", page: 1 })}>조건 초기화</button>
      </div> : null}
      <nav className={shared.pagination} aria-label="회원 페이지">
        <button type="button" aria-label="이전 페이지" disabled={!data || query.page <= 1} onClick={() => navigate({ ...query, page: query.page - 1 })}><img src="/admin/previous.svg" alt="" /></button>
        {start > 1 && <><button type="button" aria-label="1페이지" onClick={() => navigate({ ...query, page: 1 })}>1</button>{start > 2 && <span>…</span>}</>}
        {Array.from({ length: Math.min(5, pages) }, (_, index) => start + index).map(page => <button type="button" key={page} disabled={!data} aria-label={`${page}페이지`} aria-current={page === query.page ? "page" : undefined} onClick={() => navigate({ ...query, page })}>{page}</button>)}
        {start + 4 < pages && <><span>…</span><button type="button" aria-label={`${pages}페이지`} onClick={() => navigate({ ...query, page: pages })}>{pages}</button></>}
        <button type="button" aria-label="다음 페이지" disabled={!data || query.page >= pages} onClick={() => navigate({ ...query, page: query.page + 1 })}><img src="/admin/next.svg" alt="" /></button>
      </nav>
      <p className={styles.availability}>회원 조회 전용 · 최근 로그인은 서비스 이용 시각과 다릅니다. 휴면 상태·통계·내보내기는 준비 중이며 회원 정보 변경 및 제재 기능은 사용할 수 없습니다.</p>
    </section>
  </div>;
}
