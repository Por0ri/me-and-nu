"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { adminQueryString, loadAdminContent, loadAdminContents, parseAdminQuery, type AdminContentDetail } from "@/lib/admin-content";
import { AdminStatus } from "./admin-status";
import { ContentForm } from "./content-form";
import { useAdminRead } from "./use-admin-read";
import styles from "./admin.module.css";

type EditorSeed = { detail?: AdminContentDetail };

export function ContentEditorScreen({ contentId }: { contentId?: number }) {
  const params = useSearchParams();
  let query = "";
  try { query = `?${adminQueryString(parseAdminQuery(params))}`; } catch { /* Fixed local return path. */ }
  const back = contentId ? `/admin/contents/${contentId}${query}` : `/admin/contents${query}`;
  const read = useCallback(async (signal: AbortSignal): Promise<EditorSeed> => {
    if (contentId) return { detail: await loadAdminContent(contentId, signal) };
    // Reuse the protected read boundary. A successful read never grants writes.
    await loadAdminContents({ q: "", status: "", judgmentStatus: "", page: 1, pageSize: 1 }, signal);
    return {};
  }, [contentId]);
  const { state, retry } = useAdminRead(contentId ? `edit-${contentId}` : "create", read);
  const [seed, setSeed] = useState<EditorSeed | null>(null);
  const revoked = state.status === "login-required" || state.status === "forbidden" || state.status === "not-found";
  useEffect(() => {
    if (revoked) setSeed(null);
    else if (state.status === "ready") setSeed(current => current ?? state.data);
  }, [state, revoked]);
  return <>
    <a className={styles.backLink} href={back}>{contentId ? "← 콘텐츠 상세" : "← 콘텐츠 목록"}</a>
    <div className={styles.heading}><h1>{contentId ? "콘텐츠 수정" : "콘텐츠 등록"}</h1><p>제목, 요약, 본문과 출처를 입력하세요.</p></div>
    <section className={styles.panel} data-admin-editor={state.status}>
      {state.status !== "ready" && <AdminStatus state={state} retry={retry} />}
      {/* Rechecks hide/disable but do not discard dirty input. Revocation clears it. */}
      {seed && !revoked && <div hidden={state.status !== "ready"}><ContentForm detail={seed.detail} active={state.status === "ready"} /></div>}
    </section>
  </>;
}
