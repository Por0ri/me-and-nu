"use client";
// My Page 관심분야 관리 (2026-11 design): the onboarding 관심 분야 screen showing the member's field.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { isActiveSession } from "@/lib/pivot/services";
import { usePivotSession } from "./pivot-session";
import { InterestScreen } from "./onboarding-screens";
import { PivotBrand, PivotFrame } from "./pivot-ui";
import pivotStyles from "./pivot.module.css";

const MY_PAGE = "/pivot/home?view=my";

export function InterestManageRoute() {
  const router = useRouter();
  const { state: session } = usePivotSession();
  if (isActiveSession(session.session)) {
    return <InterestScreen selected onToggle={() => {}} onNext={() => {}} busy={false} manage={{ onBack: () => router.replace(MY_PAGE) }} />;
  }
  if (session.status === "loading") return <PivotFrame decorated={false}>{null}</PivotFrame>;
  return <PivotFrame>
    <PivotBrand screen="consent" className={pivotStyles.consentBrand} />
    <section className={pivotStyles.gate}><h1>관심분야 관리</h1>
      <p role="status">로그인한 뒤 관심 분야를 볼 수 있어요.</p>
      <Link href={MY_PAGE}>마이페이지로 돌아가기</Link>
    </section>
  </PivotFrame>;
}
