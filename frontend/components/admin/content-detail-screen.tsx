"use client";

import Link from "next/link";
import { useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { adminDate, adminQueryString, loadAdminContent, parseAdminQuery, safeSourceUrl } from "@/lib/admin-content";
import { AdminStatus } from "./admin-status";
import { useAdminRead } from "./use-admin-read";
import styles from "./admin.module.css";

export function ContentDetailScreen({ contentId }: { contentId: number }) {
  const params = useSearchParams();
  let back = "/admin/contents";
  try { back += `?${adminQueryString(parseAdminQuery(params))}`; } catch { /* Invalid list context never becomes an arbitrary redirect. */ }
  const read = useCallback((signal: AbortSignal) => loadAdminContent(contentId, signal), [contentId]);
  const { state, retry } = useAdminRead(String(contentId), read);
  return <>
    <Link className={styles.backLink} href={back}>← 콘텐츠 목록</Link>
    <div className={styles.heading}><h1>콘텐츠 상세</h1><p>기본 정보와 출처를 확인하세요.</p></div>
    <section className={styles.panel} data-admin-detail={state.status}>
      {state.status !== "ready" ? <AdminStatus state={state} retry={retry} /> : <article className={styles.detail}>
        <p className={styles.note}>콘텐츠 #{state.data.contentId}</p><h2>{state.data.title || "제목 없음"}</h2>
        <dl><div><dt>검수 상태</dt><dd>{state.data.judgmentStatus}</dd></div><div><dt>콘텐츠 상태</dt><dd>{state.data.status}</dd></div>
          <div><dt>생성 시각</dt><dd>{adminDate(state.data.createdAt)}</dd></div><div><dt>발행 시각</dt><dd>{adminDate(state.data.publishedAt)}</dd></div></dl>
        <h3>요약</h3><p className={styles.body}>{state.data.summary || "등록된 요약이 없습니다."}</p>
        <h3>본문</h3><p className={styles.body}>{state.data.body || "등록된 본문이 없습니다."}</p>
        <h3>출처</h3>{state.data.sources.length ? <ul className={styles.sources}>{state.data.sources.map((source, index) => {
          const href = safeSourceUrl(source.url);
          return <li key={`${source.url}-${index}`}>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{source.title || source.url}</a> : <span>{source.title || "출처 링크를 표시할 수 없습니다."}</span>}
            {source.publisher && <p>{source.publisher}</p>}</li>;
        })}</ul> : <p>등록된 출처가 없습니다.</p>}
      </article>}
    </section>
  </>;
}
