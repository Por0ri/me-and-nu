"use client";

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { PivotBrand, PivotFrame } from "./pivot-ui";
import { SplashBackground } from "./splash-background";
import styles from "./pivot.module.css";
import { SPLASH_DISSOLVE_MS, startSplashTransition } from "@/lib/pivot/transitions";

export function SplashScreen({ preview = false, onContinue }: { preview?: boolean; onContinue?: () => void }) {
  const router = useRouter();
  const [leaving, setLeaving] = useState(false);
  const completed = useRef(false);
  const cancel = useRef<() => void>(() => {});
  const continueOnce = useCallback(() => {
    if (completed.current) return;
    completed.current = true;
    cancel.current();
    if (onContinue) onContinue(); else router.replace("/pivot/login");
  }, [onContinue, router]);
  useEffect(() => {
    if (preview) return;
    completed.current = false;
    cancel.current = startSplashTransition(() => setLeaving(true), continueOnce, window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    return () => cancel.current();
  }, [preview, continueOnce]);
  return <div className={leaving ? styles.splashLeaving : undefined} style={{ "--splash-dissolve-duration": `${SPLASH_DISSOLVE_MS}ms` } as CSSProperties}>
    <PivotFrame splash chrome={preview}>
    <h1 className={styles.srOnly} tabIndex={-1}>me;nu, 당신의 덕질을 위한 시작</h1>
    <SplashBackground active={!leaving} />
    <PivotBrand screen="splash" className={styles.splashBrand} />
    <div className={styles.splashMascot} aria-hidden="true" data-splash-mascot="">
      <div className={styles.splashMascotRest} data-splash-character-state="1"><img src="/pivot/home/chrome/mascot.png" alt="" width={322} height={172} /></div>
      <div className={styles.splashMascotRaised} data-splash-character-state="2"><img src="/pivot/splash/state-2.png" alt="" width={303} height={207} /></div>
    </div>
    {!preview && <button type="button" className={styles.skip} onClick={continueOnce}>로그인 화면으로 건너뛰기</button>}
    </PivotFrame>
  </div>;
}
