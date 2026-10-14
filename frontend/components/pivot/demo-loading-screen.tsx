"use client";

import { useEffect, useRef, useState, useSyncExternalStore, type RefObject } from "react";
import { startDemoLoading, type LoadingPhase } from "@/lib/pivot/transitions";
import { PivotFrame, PrimaryButton } from "./pivot-ui";
import styles from "./demo-loading.module.css";

const motionQuery = "(prefers-reduced-motion: reduce)";
function subscribeMotion(listener: () => void) {
  const media = window.matchMedia(motionQuery);
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}
const getReducedMotion = () => window.matchMedia(motionQuery).matches;
const serverMotion = () => false;
const phases = [1, 2, 3] as const;
const IMAGE_READY_TIMEOUT_MS = 3000;

type ImageState = "pending" | "ready" | "failed";
export function LoadingArtwork({ phase, imageState = "ready", firstImageRef }: { phase: LoadingPhase; imageState?: ImageState; firstImageRef?: RefObject<HTMLImageElement | null> }) {
  return <div className={styles.figure} aria-hidden="true" data-demo-loading-phase={phase + 1} data-loading-image-state={imageState}>
    <div className={styles.character} data-image-state={imageState}>
      {phases.map((value, index) => <img key={value} ref={index === 0 ? firstImageRef : undefined} hidden={index !== phase} src={`/pivot/loading/phase-${value}.png`} alt="" width={227.7} height={252.9} fetchPriority={index === 0 ? "high" : "low"} decoding="async" />)}
      {imageState !== "ready" ? <p className={styles.imageFallback}>{imageState === "pending" ? "시연용 화면 준비 중" : "이미지 없이 시연을 계속해요"}</p> : null}
    </div>
    <div className={styles.dots}>
      {phases.map((value, index) => <img key={value} hidden={index !== phase} src={`/pivot/loading/dots-${value}.svg`} alt="" width={46.44} height={8.64} />)}
    </div>
  </div>;
}

export function DemoLoadingScreen({ onComplete, onRestart }: { onComplete: () => void; onRestart: () => void }) {
  const [phase, setPhase] = useState<LoadingPhase>(0);
  const [imageState, setImageState] = useState<ImageState>("pending");
  const firstImageRef = useRef<HTMLImageElement>(null);
  const reducedMotion = useSyncExternalStore(subscribeMotion, getReducedMotion, serverMotion);
  useEffect(() => {
    const image = firstImageRef.current;
    if (!image) return;
    let settled = false;
    let decoding = false;
    const cleanup = () => {
      clearTimeout(watchdog);
      image.removeEventListener("load", decode);
      image.removeEventListener("error", failed);
    };
    const finish = (state: "ready" | "failed") => {
      if (settled) return;
      settled = true;
      cleanup();
      setImageState(state);
    };
    const failed = () => finish("failed");
    const decode = () => {
      if (settled || decoding) return;
      decoding = true;
      if (typeof image.decode === "function") {
        void image.decode().then(() => finish("ready"), failed);
      } else finish(image.naturalWidth > 0 ? "ready" : "failed");
    };
    const watchdog = setTimeout(failed, IMAGE_READY_TIMEOUT_MS);
    image.addEventListener("load", decode);
    image.addEventListener("error", failed);
    if (image.complete) { if (image.naturalWidth > 0) decode(); else failed(); }
    return () => { settled = true; cleanup(); };
  }, []);
  // Each 800ms phase starts after the first frame is decoded, rather than consuming it while blank.
  useEffect(() => {
    if (imageState === "pending") return;
    return startDemoLoading(setPhase, onComplete);
  }, [onComplete, imageState]);
  return <PivotFrame decorated={false} className={styles.screen}>
    <img className={styles.background} src="/pivot/loading/background.svg" alt="" aria-hidden="true" width={380} height={380} />
    <section className={styles.intro} aria-labelledby="demo-loading-heading">
      <h1 id="demo-loading-heading" tabIndex={-1}>시연용 피드 준비 중</h1>
      <p role="status">화면 전환 예시예요.<br />실제 콘텐츠 수집·추천·저장은 진행하지 않아요.</p>
    </section>
    <LoadingArtwork phase={reducedMotion ? 0 : phase} imageState={imageState} firstImageRef={firstImageRef} />
    <div className={styles.actions}><PrimaryButton onClick={onRestart}>처음부터 시연</PrimaryButton></div>
  </PivotFrame>;
}
