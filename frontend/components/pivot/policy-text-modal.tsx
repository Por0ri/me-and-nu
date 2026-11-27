"use client";

import { Fragment, useEffect, useId, useMemo, useRef, type KeyboardEvent } from "react";
import { parseReviewBlocks, type ReviewBlock } from "@/lib/pivot/policy-review";
import { policyTexts } from "@/lib/pivot/policy-texts";
import styles from "./policy-text-modal.module.css";

export type PolicyTextModalKind = "terms" | "privacy";

// Copied from the mock as is.
export const POLICY_EFFECTIVE_DATE = "시행일: 2026. 11. 02.";
export const policyTextModals = {
  terms: {
    title: "이용약관 및 운영정책",
    note: "본 약관은 2026년 11월 2일부터 시행되며, 서비스 이용에 적용됩니다.",
    documents: [{ label: "이용약관 전문", text: policyTexts.terms }, { label: "운영정책 전문", text: policyTexts.operations }],
  },
  privacy: {
    title: "개인정보 처리방침",
    note: "본 방침은 2026년 11월 2일부터 시행되며, 서비스 이용에 적용됩니다.",
    documents: [{ label: "개인정보 처리방침 전문", text: policyTexts.privacy }],
  },
} as const;

/** Consent item types that open a bundled full text; anything else keeps the server text dialog. */
export function policyTextModalKind(type: string): PolicyTextModalKind | null {
  return type === "terms" || type === "privacy" ? type : null;
}

/** Read-only modal: opening/closing never changes consent selection. */
export function PolicyTextModal({ kind, onClose }: { kind: PolicyTextModalKind; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const id = useId();
  const modal = policyTextModals[kind];
  useEffect(() => {
    const element = dialog.current;
    const trigger = document.activeElement as HTMLElement | null;
    if (element && !element.open) element.showModal();
    // React's autoFocus runs while a client-mounted dialog is still closed.
    closeButton.current?.focus({ preventScroll: true });
    return () => {
      element?.close();
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, []);
  return <dialog ref={dialog} className={styles.dialog} aria-modal="true" aria-labelledby={`${id}-title`} data-policy-modal={kind}
    onKeyDown={keepTabInside}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClose={(event) => { if (!event.currentTarget.open) onClose(); }}>
    <div className={styles.header}>
      <h2 id={`${id}-title`} className={styles.title}>{modal.title}</h2>
      <button ref={closeButton} type="button" className={styles.close} aria-label="닫기" onClick={onClose}><CloseIcon /></button>
    </div>
    <div className={styles.effective}>
      <p className={styles.effectiveDate}>{POLICY_EFFECTIVE_DATE}</p>
      <p className={styles.effectiveNote}>{modal.note}</p>
    </div>
    {modal.documents.map((document, index) => <Fragment key={document.label}>
      <p id={`${id}-label-${index}`} className={styles.label}>{document.label}</p>
      <section className={styles.document} tabIndex={0} aria-labelledby={`${id}-label-${index}`}>
        <PolicyMarkdown title={document.text.title} markdown={document.text.markdown} />
      </section>
    </Fragment>)}
  </dialog>;
}

function keepTabInside(event: KeyboardEvent<HTMLDialogElement>) {
  if (event.key !== "Tab") return;
  const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), [tabindex="0"]'));
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1], active = document.activeElement;
  if (event.shiftKey ? active === first || !event.currentTarget.contains(active) : active === last) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  }
}

function CloseIcon() {
  return <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" focusable="false">
    <path d="M1 1l12 12M13 1 1 13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </svg>;
}

/** Same text, with a line-break opportunity after each "@" so e-mail addresses wrap cleanly in narrow cells. */
function breakAfterAt(text: string) {
  const parts = text.split("@");
  return parts.length === 1 ? text : parts.map((part, index) => <Fragment key={index}>{index ? <wbr /> : null}{index < parts.length - 1 ? `${part}@` : part}</Fragment>);
}

/** Headings, paragraphs, numbered/'-' lists and pipe tables as plain React text. Never HTML. */
export function PolicyMarkdown({ title, markdown }: { title: string; markdown: string }) {
  const blocks = useMemo(() => parseReviewBlocks(markdown), [markdown]);
  return <>
    <h3 className={styles.heading}>{title}</h3>
    <PolicyBlocks blocks={blocks} context={title} />
  </>;
}

function PolicyBlocks({ blocks, context, nested = false }: { blocks: ReviewBlock[]; context: string; nested?: boolean }) {
  let heading = context;
  return blocks.map((block, index) => {
    if (block.kind === "heading") {
      heading = block.text;
      return block.level === 4 ? <h4 key={index} className={styles.heading}>{block.text}</h4> : <h5 key={index} className={styles.heading}>{block.text}</h5>;
    }
    if (block.kind === "paragraph") return <p key={index} className={styles.paragraph}>{block.text}</p>;
    if (block.kind === "table") return <div key={index} className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.srOnly}>{heading}</caption>
        <thead><tr>{block.header.map((cell, column) => <th key={column} scope="col">{cell}</th>)}</tr></thead>
        <tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, column) => <td key={column}>{breakAfterAt(cell)}</td>)}</tr>)}</tbody>
      </table>
    </div>;
    const items = block.items.map((item, itemIndex) => <li key={itemIndex} className={styles.item}>
      {block.ordered ? <span className={styles.marker}>{item.number}.</span> : <span className={styles.marker} aria-hidden="true">-</span>}
      <div>{item.text}{item.children.length ? <PolicyBlocks blocks={item.children} context={heading} nested /> : null}</div>
    </li>);
    const className = nested ? styles.nestedList : styles.list;
    return block.ordered
      ? <ol key={index} role="list" className={className} start={block.items[0]?.number}>{items}</ol>
      : <ul key={index} role="list" className={className}>{items}</ul>;
  });
}
