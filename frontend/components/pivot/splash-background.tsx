"use client";

import { useEffect, useRef, useState } from 'react';
import { startMovingGradient, type GradientStatus } from '@/lib/pivot/moving-gradient/runtime';
import styles from './pivot.module.css';

export function SplashBackground({ active }: { active: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState<GradientStatus>('static');
  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    let dispose = () => {};
    const refresh = () => {
      dispose();
      setStatus('static');
      if (active && !preference.matches && document.visibilityState !== 'hidden' && canvas.current) {
        dispose = startMovingGradient(canvas.current, setStatus);
      }
    };
    refresh();
    preference.addEventListener('change', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      dispose();
      preference.removeEventListener('change', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [active]);
  return <div className={styles.splashRings} aria-hidden="true" data-splash-background={status}>
    <img className={styles.splashStaticCircle} src="/pivot/splash-decoration.png" alt="" width={375} height={375} />
    <div className={styles.splashLiveCircle}>
      <canvas ref={canvas} className={styles.splashGradientCanvas} width={375} height={375} />
    </div>
  </div>;
}
