"use client";

import { useEffect, useRef, useState } from "react";
import { parseTeamPolicyReview, type ReviewBlock, type ReviewDocument } from "@/lib/pivot/policy-review";
import type { MyPagePolicyType } from "@/lib/pivot/policy-document";
import { PolicyDialog } from "./policy-dialog";
import styles from "./pivot.module.css";

export function ReviewBlocks({ blocks, title }: { blocks: ReviewBlock[]; title: string }) {
  let heading = title;
  return blocks.map((block, index) => {
    if (block.kind === "heading") {
      heading = block.text;
      return block.level === 5 ? <h5 key={index}>{block.text}</h5> : <h4 key={index}>{block.text}</h4>;
    }
    if (block.kind === "paragraph") return <p key={index}>{block.text}</p>;
    if (block.kind === "table") return <div key={index} className={styles.policyReviewTable} role="region" aria-label={`${heading} 표`} tabIndex={0}>
      <table><caption className={styles.srOnly}>{heading}</caption>
        <thead><tr>{block.header.map((cell, column) => <th key={column} scope="col">{cell}</th>)}</tr></thead>
        <tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, column) => <td key={column}>{cell}</td>)}</tr>)}</tbody>
      </table>
    </div>;
    const items = block.items.map((item, itemIndex) => <li key={itemIndex} value={item.number}>{item.text}
      <ReviewBlocks blocks={item.children} title={heading} />
    </li>);
    return block.ordered ? <ol key={index} start={block.items[0]?.number}>{items}</ol> : <ul key={index}>{items}</ul>;
  });
}

type ReviewState = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; documents: ReviewDocument[] };

export function LocalPolicyReview({ type, onClose }: { type: MyPagePolicyType; onClose: () => void }) {
  const [state, setState] = useState<ReviewState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const articles = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch("/api/dev/policy-review", { method: "GET", credentials: "omit", cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(response.status === 404 ? "로컬 정책 검토가 활성화되지 않았어요." : "검토 문서를 불러오지 못했어요.");
        const result = await response.json();
        if (result?.kind !== "development-review") throw new Error("검토 문서를 확인할 수 없어요.");
        const documents = parseTeamPolicyReview(result.markdown);
        if (!controller.signal.aborted) setState({ status: "ready", documents });
      } catch (reason) {
        if (!controller.signal.aborted) setState({ status: "error", message: reason instanceof Error && reason.message === "로컬 정책 검토가 활성화되지 않았어요."
          ? reason.message : "검토 문서를 불러오지 못했어요. 다시 시도해주세요." });
      }
    }
    void load();
    return () => controller.abort();
  }, [attempt]);
  const documents = state.status === "ready" ? state.documents.filter((document) => type === "terms" ? document.id !== "privacy" : document.id === "privacy") : [];
  return <PolicyDialog title={type === "terms" ? "이용약관 및 운영정책" : "개인정보처리방침"} onClose={onClose}
    notice={<p className={styles.policyReviewNotice}><strong>개발용 검토 문서 · 정식 게시 미승인</strong><br />가입 시 동의하는 정책과 별개이며, 동의 기록을 변경하지 않아요.</p>}>
    <div data-policy-review-state={state.status} aria-busy={state.status === "loading"}>
      {state.status === "loading" ? <p role="status">검토 문서를 불러오고 있어요.</p>
        : state.status === "error" ? <><p role="alert">{state.message}</p><button type="button" className={styles.textButton}
          onClick={() => { setState({ status: "loading" }); setAttempt((value) => value + 1); }}>다시 시도</button></>
          : <>
            {documents.length > 1 ? <nav className={styles.policyReviewNavigation} aria-label="검토 문서 바로가기">{documents.map((document) => <button key={document.id} type="button"
              onClick={() => { const target = articles.current?.querySelector<HTMLElement>(`[data-review-heading="${document.id}"]`); target?.focus({ preventScroll: true }); target?.scrollIntoView({ block: "start" }); }}>{document.title}</button>)}</nav> : null}
            <div ref={articles}>{documents.map((document) => <article key={document.id} className={styles.policyReviewDocument} aria-label={`${document.title} 검토 원문`}>
              <h3 tabIndex={-1} data-review-heading={document.id}>{document.title}</h3>
              <p className={styles.policyReviewMetadata}>버전 {document.version} · 시행일 <time dateTime={document.effectiveDate}>{document.effectiveDate}</time></p>
              <ReviewBlocks title={document.title} blocks={document.blocks} />
            </article>)}</div>
          </>}
    </div>
  </PolicyDialog>;
}
