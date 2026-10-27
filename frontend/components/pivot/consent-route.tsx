"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { isPendingConsumer } from "@/lib/pivot/services";
import { usePivotSession } from "./pivot-session";
import { ConsentScreen } from "./consent-screen";
import { PivotBrand, PivotFrame } from "./pivot-ui";
import styles from "./pivot.module.css";

export function ConsentRoute() {
  const router = useRouter();
  const { state, refresh, rememberDraft, draft } = usePivotSession();
  if (isPendingConsumer(state.session)) return <ConsentScreen initialDraft={draft ?? undefined} onContinue={(nextDraft) => { rememberDraft(nextDraft); router.push("/pivot/onboarding"); }} />;
  const message = state.status === "loading" ? "로그인 상태를 확인하고 있어요."
    : state.session?.sessionState === "active" ? "이미 가입된 계정이에요. 기존 서비스에서 계속 이용해 주세요."
    : state.session?.sessionState === "restoration_pending" ? "계정 복원을 먼저 확인해 주세요."
    : state.status === "disconnected" ? "약관을 확인할 로컬 서버가 아직 연결되지 않았어요."
    : state.message || "약관에 동의하려면 먼저 로그인해 주세요.";
  return <PivotFrame><PivotBrand screen="consent" className={styles.consentBrand} />
    <section className={styles.gate}><h1>로그인 확인</h1><p role="status">{message}</p>
      {state.status === "error" && <button type="button" className={styles.textButton} onClick={refresh}>다시 확인</button>}
      <Link href="/pivot/login">로그인 화면으로</Link>
    </section>
  </PivotFrame>;
}
