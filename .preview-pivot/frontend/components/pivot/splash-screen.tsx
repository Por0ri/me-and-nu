"use client";

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { PivotAsset, PivotBrand, PivotFrame } from "./pivot-ui";
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
    <div className={styles.splashRings} aria-hidden="true">
      <img src="/pivot/splash-decoration.png" alt="" width={375} height={375} />
    </div>
    <PivotBrand screen="splash" className={styles.splashBrand} />
    <div className={styles.splashMascot}><PivotAsset screen="splash" name="imgImage28" /></div>
    {!preview && <button type="button" className={styles.skip} onClick={continueOnce}>로그인 화면으로 건너뛰기</button>}
    </PivotFrame>
  </div>;
}
