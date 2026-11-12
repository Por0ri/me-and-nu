"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import styles from "./home.module.css";

/** Observe the rendered first image, including the authenticated carousel's blob URL.
 * Keeping its owner mounted avoids a second download or a cross-session pixel cache. */
export function HomeEntryImage({ children, onReady }: { children: ReactNode; onReady?: () => void }) {
  const frame = useRef<HTMLSpanElement>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const element = frame.current;
    if (!onReady || !element) return;
    let stopped = false;
    let decoding: HTMLImageElement | null = null;
    const finish = (error: boolean) => {
      if (stopped) return;
      stopped = true;
      cleanup();
      setFailed(error);
      onReady();
    };
    const check = () => {
      if (stopped) return;
      const image = element.querySelector("img");
      if (image) {
        if (!image.complete || decoding === image) return;
        if (!image.naturalWidth) { finish(true); return; }
        decoding = image;
        void (typeof image.decode === "function" ? image.decode() : Promise.resolve()).then(
          () => finish(false), () => finish(true),
        );
      } else if (element.querySelector('[data-carousel-image-state="error"], [data-home-image-state="error"]')) {
        finish(true);
      } else if (element.querySelector('[role="img"]')) {
        finish(false); // An intentional missing-image placeholder is already usable.
      }
    };
    const observer = new MutationObserver(check);
    const timeout = setTimeout(() => finish(true), 5000);
    const cleanup = () => {
      clearTimeout(timeout); observer.disconnect();
      element.removeEventListener("load", check, true);
      element.removeEventListener("error", check, true);
    };
    observer.observe(element, { childList: true, subtree: true, attributes: true, attributeFilter: ["src", "data-carousel-image-state", "data-home-image-state"] });
    element.addEventListener("load", check, true);
    element.addEventListener("error", check, true);
    check();
    return () => { stopped = true; cleanup(); };
  }, [onReady, attempt]);
  if (!onReady) return children;
  return <span ref={frame} className={styles.entryImage}>
    {failed ? <span className={styles.entryImageFallback} role="status">
      <span>이미지를 불러오지 못했어요.</span>
      <button type="button" onClick={() => { setFailed(false); setAttempt(value => value + 1); }}>이미지 다시 시도</button>
    </span> : children}
  </span>;
}
