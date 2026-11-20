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
    } catch (failure) {
      const apiError = failure instanceof ApiError ? failure : null;
      if (apiError?.code === "WITHDRAWAL_BLOCKED") {
        setError(apiError.message.trim() || "현재 이 계정은 탈퇴할 수 없어요.");
      } else if (apiError?.code === "ACCOUNT_REQUEST_IN_PROGRESS") {
        setError("다른 계정 요청을 처리하고 있어요. 잠시 후 다시 시도해 주세요.");
      } else if (apiError?.code === "ACCOUNT_SESSION_CHANGED") {
        setError("로그인 상태가 바뀌었어요. 다시 로그인해 주세요.");
      } else {
        setError("탈퇴 신청을 확인하지 못했어요. 잠시 후 다시 시도해 주세요.");
      }
      return;
    } finally {
      pending.current = false;
      setBusy(false);
    }
    onClose();
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
