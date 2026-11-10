"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import styles from "./my-page.module.css";

export function WithdrawalDialog({ withdraw, onClose }: { withdraw: () => Promise<void>; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const element = dialog.current;
    const trigger = document.activeElement as HTMLElement | null;
    element?.showModal();
    return () => {
      element?.close();
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, []);

  function close() {
    if (!pending.current) onClose();
  }

  async function confirm() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await withdraw();
      onClose();
    } catch (failure) {
      setError(failure instanceof ApiError && failure.code === "WITHDRAWAL_BLOCKED"
        ? "연결된 크리에이터 채널이 있어 탈퇴할 수 없어요."
        : "탈퇴 신청을 확인하지 못했어요. 잠시 후 다시 시도해 주세요.");
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  return <dialog ref={dialog} className={`${styles.dialog} ${styles.withdrawalDialog}`} aria-modal="true"
    aria-labelledby="my-withdrawal-title" aria-describedby="my-withdrawal-description my-withdrawal-question"
    onCancel={(event) => { event.preventDefault(); close(); }}>
    <h2 id="my-withdrawal-title">회원탈퇴</h2>
    <p id="my-withdrawal-description">계정은 즉시 비활성화되고 <strong>30일 후 삭제될 예정</strong>이에요. 30일 안에 같은 소셜 계정으로 로그인하면 복구할 수 있어요.</p>
    <p id="my-withdrawal-question" className={styles.withdrawalQuestion}>지금 바로 탈퇴하시겠어요?</p>
    {error && <p role="alert" className={styles.withdrawalError}>{error}</p>}
    <div className={styles.dialogActions}>
      <button type="button" autoFocus disabled={busy} onClick={close}>취소</button>
      <button type="button" aria-disabled={busy || undefined} aria-busy={busy || undefined} onClick={() => void confirm()}>
        {busy ? "탈퇴 신청 중…" : "탈퇴"}
      </button>
    </div>
    <p className={styles.withdrawalProgress} role="status">{busy ? "탈퇴 신청을 처리하고 있어요." : ""}</p>
  </dialog>;
}
