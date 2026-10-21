"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { isPendingConsumer, startSnsLogin, type SnsProvider } from "@/lib/pivot/services";
import { usePivotSession } from "./pivot-session";
import { PivotAsset, PivotBrand, PivotFrame, PrimaryButton } from "./pivot-ui";
import styles from "./pivot.module.css";

export function LoginScreen({ preview = false, onDemoContinue }: { preview?: boolean; onDemoContinue?: () => void }) {
  const demo = onDemoContinue !== undefined;
  const router = useRouter();
  const { state, refresh } = usePivotSession();
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!preview && !demo && isPendingConsumer(state.session)) router.replace("/pivot/consent");
  }, [preview, demo, state.session, router]);
  async function login(provider: SnsProvider) {
    if (demo) {
      setMessage(`${provider === "kakao" ? "카카오" : "네이버"} 로그인 UI를 확인했어요. 실제 로그인은 진행되지 않아요. 아래 시연용 버튼으로 계속해 주세요.`);
      return;
    }
    try { await startSnsLogin(provider); }
    catch (error) { setMessage(error instanceof Error ? error.message : "로그인을 시작할 수 없어요."); }
  }
  const sessionNotice = !preview && !demo && state.session?.sessionState === "active"
    ? "이미 로그인되어 있어요. 기존 서비스에서 계속 이용해 주세요."
    : !preview && !demo && state.session?.sessionState === "restoration_pending"
      ? "계정 복원 확인이 필요해요. 계정 복원 절차를 먼저 진행해 주세요."
      : !preview && !demo ? state.message : "";
  return <PivotFrame chrome={preview}>
    <section className={styles.loginIntro} aria-labelledby="login-heading">
      <PivotBrand screen="login" />
      <h1 id="login-heading" tabIndex={-1}>당신의 덕질에<br />딱 맞는 정보를 만나보세요</h1>
      <div className={styles.loginMascot}><PivotAsset screen="login" name="imgImage36" width={169} height={205.183} /></div>
    </section>
    <div className={styles.loginActions}>
      <div className={styles.bubble} aria-hidden="true"><span>SNS로 간편하게 시작하기</span><PivotAsset screen="login" name="imgPolygon3" width={10.392} height={6} /></div>
      <button type="button" className={styles.kakao} onClick={() => login("kakao")} aria-describedby="login-feedback">
        <span className={styles.kakaoBackground} aria-hidden="true"><PivotAsset screen="login" name="imgRoundedRectangle2" width={327} height={40} /></span>
        <span className={styles.providerLabel}><PivotAsset screen="login" name="imgShape1" width={18} height={16.889} />카카오 로그인</span>
      </button>
      <button type="button" className={styles.naver} onClick={() => login("naver")} aria-describedby="login-feedback">
        <span className={styles.naverCrop}><PivotAsset screen="login" name="img2" /></span>네이버 로그인
      </button>
      <div id="login-feedback" className={styles.loginFeedback} role="status" aria-live="polite">{message || sessionNotice}</div>
      {onDemoContinue && <div className={styles.demoEntry}>
        <p id="demo-entry-notice">SNS 버튼으로 실제 로그인하지 않습니다.<br />로그인 없이 화면 흐름을 시연할 수 있어요.</p>
        <PrimaryButton onClick={onDemoContinue} aria-describedby="demo-entry-notice">로그인 없이 시연 계속하기</PrimaryButton>
      </div>}
      {!preview && !demo && state.status === "error" && <button className={styles.textButton} type="button" onClick={refresh}>로그인 상태 다시 확인</button>}
    </div>
  </PivotFrame>;
}
