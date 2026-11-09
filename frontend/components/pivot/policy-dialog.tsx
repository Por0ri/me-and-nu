"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import type { PolicyContent } from "@/lib/pivot/policy-document";
import { PrimaryButton } from "./pivot-ui";
import styles from "./pivot.module.css";

/** Shared read-only shell: opening/closing never changes consent selection. */
export function PolicyDialog({ title, children, notice, onClose }: { title: string; children: ReactNode; notice?: ReactNode; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    const trigger = document.activeElement as HTMLElement | null;
    element?.showModal();
    // React's autoFocus runs while a client-mounted dialog is still closed.
    element?.querySelector<HTMLButtonElement>("[data-policy-close]")?.focus({ preventScroll: true });
    return () => {
      element?.close();
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, []);
  return <dialog ref={dialog} className={styles.dialog} aria-modal="true" aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClose={(event) => { if (!event.currentTarget.open) onClose(); }}>
    <h2 id={titleId}>{title}</h2>
    {notice}
    <div className={styles.policyBody}>{children}</div>
    <PrimaryButton autoFocus data-policy-close onClick={onClose}>닫기</PrimaryButton>
  </dialog>;
}

export function PolicyDocumentContent({ content, title }: { content: PolicyContent; title: string }) {
  if (content.kind === "unavailable") return <p role="status">정책 원문을 확인할 수 없어요.</p>;
  if (content.kind === "url") return <div className={styles.policyText}>
    <a href={content.url} target="_blank" rel="noopener noreferrer">{title} 원문 열기 (새 탭)</a>
    <p>{content.url}</p>
  </div>;
  return <div className={styles.policyText} tabIndex={0} role="region" aria-label={`${title} 본문`}>{content.text}</div>;
}
