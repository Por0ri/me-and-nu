"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { DEMO_LOADING_STEP_MS, type LoadingPhase } from "@/lib/pivot/transitions";
import { LoadingArtwork } from "./demo-loading-screen";
import { PivotFrame } from "./pivot-ui";
import styles from "./demo-loading.module.css";

// The teammate's demo loading artwork and styles (demo-loading-screen.tsx), used as they are for the real flow.
const motionQuery = "(prefers-reduced-motion: reduce)";
function subscribeMotion(listener: () => void) {
  const media = window.matchMedia(motionQuery);
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}
const getReducedMotion = () => window.matchMedia(motionQuery).matches;
const serverMotion = () => false;

/** Between finishing onboarding (or a My Page taste edit) and the feed, while its first page is prepared. */
export function FeedLoadingScreen() {
  const [phase, setPhase] = useState<LoadingPhase>(0);
  const reducedMotion = useSyncExternalStore(subscribeMotion, getReducedMotion, serverMotion);
  useEffect(() => {
    if (reducedMotion) return;
    const timer = setInterval(() => setPhase(current => ((current + 1) % 3) as LoadingPhase), DEMO_LOADING_STEP_MS);
    return () => clearInterval(timer);
  }, [reducedMotion]);
  return <PivotFrame decorated={false} className={styles.screen}>
    <img className={styles.background} src="/pivot/loading/background.svg" alt="" aria-hidden="true" width={375} height={375} />
    <section className={styles.intro} aria-labelledby="feed-loading-heading">
      <h1 id="feed-loading-heading">취향을 불러오는 중</h1>
      <p role="status">맞춤 콘텐츠를 수집하고 있어요</p>
    </section>
    <LoadingArtwork phase={reducedMotion ? 0 : phase} />
  </PivotFrame>;
}
