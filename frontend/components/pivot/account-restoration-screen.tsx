"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { PivotBrand, PivotFrame, PrimaryButton } from "./pivot-ui";
import styles from "./pivot.module.css";

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

  const deadline = typeof scheduledDeleteAt === "string" ? Date.parse(scheduledDeleteAt) : NaN;
  const formattedDeadline = Number.isFinite(deadline) ? new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "long", timeStyle: "short", timeZone: "Asia/Seoul",
  }).format(deadline) : null;

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
      <p>탈퇴 유예 중인 계정이에요. 유예 기간 안에 복구하면 서비스를 다시 이용할 수 있어요.</p>
      <p>{formattedDeadline && scheduledDeleteAt
        ? <>삭제 예정일 <time dateTime={scheduledDeleteAt}>{formattedDeadline}</time> (한국 시간)</>
        : "삭제 예정일을 확인할 수 없어요."}</p>
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
