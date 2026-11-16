"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { PivotBrand, PivotFrame, PrimaryButton } from "./pivot-ui";
import styles from "./pivot.module.css";

function deletionDeadline(value?: string | null) {
  // A server deadline must include its timezone. Reject invalid calendar days
  // too: Date.parse alone silently normalizes dates such as February 30.
  const parts = typeof value === "string"
    ? /^(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value) : null;
  if (!parts) return null;
  const day = new Date(`${parts[1]}-${parts[2]}-${parts[3]}T00:00:00Z`);
  const deadline = Date.parse(value!);
  if (!Number.isFinite(deadline) || day.getUTCDate() !== Number(parts[3])) return null;
  return {
    date: new Intl.DateTimeFormat("ko-KR", { dateStyle: "long", timeZone: "Asia/Seoul" }).format(deadline),
    time: new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZone: "Asia/Seoul" }).format(deadline),
  };
}

export function AccountRestorationScreen({ scheduledDeleteAt, restore, returnToLogin }: {
  scheduledDeleteAt?: string | null;
  restore: () => Promise<void>;
  returnToLogin: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const loginButton = useRef<HTMLButtonElement>(null);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [terminal, setTerminal] = useState(false);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, []);
  useEffect(() => { if (terminal) loginButton.current?.focus(); }, [terminal]);

  const deadline = deletionDeadline(scheduledDeleteAt);

  async function confirm() {
    if (pending.current || terminal) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await restore();
    } catch (failure) {
      const code = failure instanceof ApiError ? failure.code : null;
      if (code === "RESTORATION_EXPIRED") {
        setError("계정 복구 기한이 지났어요.");
        setTerminal(true);
      } else if (["RESTORATION_SESSION_REQUIRED", "CSRF_INVALID", "ACCOUNT_SESSION_CHANGED", "RESTORATION_UNCONFIRMED"].includes(code ?? "")) {
        setError("복구할 계정의 로그인 상태를 확인하지 못했어요. 다시 로그인해 주세요.");
        setTerminal(true);
      } else {
        setError("계정 복구를 확인하지 못했어요. 잠시 후 다시 시도해 주세요.");
      }
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  return <PivotFrame>
    <section className={`${styles.loginIntro} ${styles.restorationIntro}`} aria-labelledby="restoration-heading">
      <PivotBrand screen="login" />
      <h1 ref={heading} id="restoration-heading" tabIndex={-1}>계정 복구</h1>
      <p>탈퇴 유예 중인 계정이에요.<br />유예 기간 안에 복구하면<br />서비스를 다시 이용할 수 있어요.</p>
      <div className={styles.restorationDeadline}>
        <p>삭제예정일</p>
        {deadline && scheduledDeleteAt ? <>
          <p><time dateTime={scheduledDeleteAt}>{deadline.date}까지</time></p>
          <p className={styles.restorationDeadlineTime}>한국 시간 {deadline.time} 전까지 복구할 수 있어요.</p>
        </> : <p>삭제 예정일을 확인할 수 없어요.</p>}
      </div>
    </section>
    <div className={styles.loginActions}>
      <p className={styles.loginFeedback} role="status">{busy ? "계정 복구를 확인하고 있어요." : ""}</p>
      {error && <p className={styles.loginFeedback} role="alert">{error}</p>}
      {!terminal && <PrimaryButton onClick={() => void confirm()} aria-disabled={busy || undefined} aria-busy={busy || undefined}>
        {busy ? "복구 확인 중…" : error ? "다시 시도" : "계정 복구하기"}
      </PrimaryButton>}
      <button ref={loginButton} className={styles.textButton} type="button" disabled={busy} onClick={returnToLogin}>로그인 화면으로</button>
    </div>
  </PivotFrame>;
}
