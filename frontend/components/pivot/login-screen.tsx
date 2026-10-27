"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { startSnsLogin, type SnsProvider } from "@/lib/pivot/services";
import { parsePivotSession } from "@/lib/pivot/session";
import { usePivotSession } from "./pivot-session";
import { PivotAsset, PivotBrand, PivotFrame, PrimaryButton } from "./pivot-ui";
import styles from "./pivot.module.css";

function oauthReturnMessage(search: string): string {
  switch (new URLSearchParams(search).get("error")) {
    case null: return "";
    case "AUTH_CANCELLED": return "로그인이 취소되었어요. 원하시면 다시 로그인해 주세요.";
    case "AUTH_FAILED": return "로그인에 실패했어요. 다시 시도해 주세요.";
    case "AUTH_UNAVAILABLE": return "지금은 로그인 서비스를 이용할 수 없어요. 잠시 후 다시 시도해 주세요.";
    case "AUTH_ACCOUNT_EXISTS": return "이미 가입된 계정이 있어요. 기존에 사용하던 로그인 방법으로 다시 시도해 주세요.";
    case "AUTH_ACCOUNT_UNAVAILABLE": return "이 계정으로는 로그인할 수 없어요. 계정 상태를 확인해 주세요.";
    default: return "로그인을 완료하지 못했어요. 다시 시도해 주세요.";
  }
}

export function LoginScreen({ preview = false, onDemoContinue }: { preview?: boolean; onDemoContinue?: () => void }) {
  const demo = onDemoContinue !== undefined;
  const router = useRouter();
  const { state, refresh } = usePivotSession();
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const loginInFlight = useRef(false);
  const loginAttempt = useRef(0);
  useEffect(() => {
    if (preview || demo || typeof window === "undefined") return;
    // Read callback feedback after hydration; never render raw query values.
    setMessage(oauthReturnMessage(window.location.search));
    const restore = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      loginAttempt.current++;
      loginInFlight.current = false;
      setPending(false);
      setMessage(oauthReturnMessage(window.location.search));
    };
    window.addEventListener("pageshow", restore);
    return () => {
      loginAttempt.current++;
      window.removeEventListener("pageshow", restore);
    };
  }, [preview, demo]);
  useEffect(() => {
    if (preview || demo || state.status !== "ready") return;
    const session = parsePivotSession(state.session);
    if (session?.sessionState === "onboarding_pending") router.replace("/pivot/consent");
    if (session?.sessionState === "active") router.replace("/pivot/home");
  }, [preview, demo, state.status, state.session, router]);
  async function login(provider: SnsProvider) {
    if (preview || demo) {
      setMessage(`${provider === "kakao" ? "카카오" : "네이버"} 로그인 UI를 확인했어요. 실제 로그인은 진행되지 않아요.${demo ? " 아래 시연용 버튼으로 계속해 주세요." : ""}`);
      return;
    }
    if (loginInFlight.current || state.status === "loading") return;
    loginInFlight.current = true;
    const attempt = ++loginAttempt.current;
    setPending(true);
    setMessage("로그인 페이지로 연결하고 있어요.");
    // assign() returns before unloading: successful navigation keeps the lock
    // until a remount or BFCache restoration. Only a failed request unlocks here.
    try { await startSnsLogin(provider); }
    catch (error) {
      if (attempt !== loginAttempt.current) return;
      loginInFlight.current = false;
      setPending(false);
      setMessage(error instanceof Error ? error.message : "로그인을 시작할 수 없어요.");
    }
  }
  const disabled = pending || (!preview && !demo && state.status === "loading");
  const sessionNotice = !preview && !demo && state.session?.sessionState === "active"
    ? "로그인이 확인되었어요. 홈으로 이동하고 있어요."
    : !preview && !demo && state.session?.sessionState === "restoration_pending"
      ? "계정 복원 확인이 필요해요. 계정 복원 절차를 먼저 진행해 주세요."
      : !preview && !demo ? state.status === "loading" ? "로그인 상태를 확인하고 있어요." : state.message : "";
  return <PivotFrame chrome={preview}>
    <section className={styles.loginIntro} aria-labelledby="login-heading">
      <PivotBrand screen="login" />
      <h1 id="login-heading" tabIndex={-1}>당신의 덕질에<br />딱 맞는 정보를 만나보세요</h1>
      <div className={styles.loginMascot}><PivotAsset screen="login" name="imgImage36" width={169} height={205.183} /></div>
    </section>
    <div className={styles.loginActions}>
      <div className={styles.bubble} aria-hidden="true"><span>SNS로 간편하게 시작하기</span><PivotAsset screen="login" name="imgPolygon3" width={10.392} height={6} /></div>
      <button type="button" className={styles.kakao} onClick={() => login("kakao")} disabled={disabled} aria-busy={pending} aria-describedby="login-feedback">
        <span className={styles.kakaoBackground} aria-hidden="true"><PivotAsset screen="login" name="imgRoundedRectangle2" width={327} height={40} /></span>
        <span className={styles.providerLabel}><PivotAsset screen="login" name="imgShape1" width={18} height={16.889} />카카오 로그인</span>
      </button>
      <button type="button" className={styles.naver} onClick={() => login("naver")} disabled={disabled} aria-busy={pending} aria-describedby="login-feedback">
        <span className={styles.naverCrop}><PivotAsset screen="login" name="img2" /></span>네이버 로그인
      </button>
      <div id="login-feedback" className={styles.loginFeedback} role="status" aria-live="polite">{message || sessionNotice}</div>
      {onDemoContinue && <div className={styles.demoEntry}>
        <p id="demo-entry-notice">SNS 버튼으로 실제 로그인하지 않습니다.<br />로그인 없이 화면 흐름을 시연할 수 있어요.</p>
        <PrimaryButton onClick={onDemoContinue} aria-describedby="demo-entry-notice">로그인 없이 시연 계속하기</PrimaryButton>
      </div>}
      {!preview && !demo && state.status === "error" && <button className={styles.textButton} type="button" disabled={pending} onClick={refresh}>로그인 상태 다시 확인</button>}
    </div>
  </PivotFrame>;
}
