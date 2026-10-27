import { notFound } from "next/navigation";
import Link from "next/link";
import { SplashScreen } from "@/components/pivot/splash-screen";
import { LoginScreen } from "@/components/pivot/login-screen";
import { ConsentScreen } from "@/components/pivot/consent-screen";
import { OnboardingPreview } from "@/components/pivot/onboarding-preview";
import type { OnboardingStep } from "@/lib/pivot/onboarding";
import styles from "@/components/pivot/pivot.module.css";

export default async function Page({ searchParams }: { searchParams: Promise<{ screen?: string; selected?: string }> }) {
  if (process.env.NODE_ENV === "production") notFound();
  const { screen = "consent", selected } = await searchParams;
  const selectionScreen = ["interest", "q1", "q2", "q3", "result"].includes(screen) ? screen as OnboardingStep : null;
  return <>
    {screen === "splash" ? <SplashScreen preview /> : screen === "login" ? <LoginScreen preview />
      : selectionScreen ? <OnboardingPreview key={screen + selected} screen={selectionScreen} selected={selected === "1"} /> : <ConsentScreen preview />}
    <aside className={styles.previewNotice}>디자인 미리보기 · 약관과 답변은 예시이며 로그인·가입·서버 저장을 수행하지 않습니다.
      <nav aria-label="미리보기 화면">
        <Link href="/pivot/preview?screen=splash">스플래시</Link><Link href="/pivot/preview?screen=login">SNS 로그인</Link><Link href="/pivot/preview?screen=consent">약관 동의</Link>
        <Link href="/pivot/preview?screen=interest">관심 분야</Link><Link href="/pivot/preview?screen=q1">Q1</Link><Link href="/pivot/preview?screen=q2">Q2</Link><Link href="/pivot/preview?screen=q3">Q3</Link><Link href="/pivot/preview?screen=result">결과</Link>
        {selectionScreen && <Link href={"/pivot/preview?screen=" + selectionScreen + "&selected=" + (selected === "1" ? "0" : "1")}>선택 상태 전환</Link>}
      </nav>
    </aside>
  </>;
}
