"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { isPendingOnboarding } from "@/lib/pivot/session";
import { usePivotSession } from "./pivot-session";
import { OnboardingFlow } from "./onboarding-screens";
import { PivotFrame, PivotBrand } from "./pivot-ui";
import styles from "./pivot.module.css";

export function OnboardingRoute() {
  const router = useRouter();
  const { state, draft, onboarding, dispatchOnboarding } = usePivotSession();
  const pending = state.status === "ready" && isPendingOnboarding(state.session);
  if (!pending || !draft) return <PivotFrame>
    <PivotBrand screen="consent" className={styles.consentBrand} />
    <section className={styles.gate}><h1>온보딩 확인</h1>
      <p role="status">{state.status === "loading" ? "로그인 상태를 확인하고 있어요." : !pending
        ? "실제 로그인과 약관 확인이 필요해요. 가입이나 서버 저장은 진행되지 않았어요."
        : "약관 내용을 확인하고 필수 약관에 동의해 주세요."}</p>
      <Link href="/pivot/consent">약관 동의 확인하기</Link><Link href="/pivot/login">로그인 화면으로</Link>
    </section>
  </PivotFrame>;
  return <OnboardingFlow state={onboarding} dispatch={dispatchOnboarding} mode="service" onExit={() => router.push("/pivot/consent")} />;
}
