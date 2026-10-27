export const SPLASH_WAIT_MS = 3000;
export const SPLASH_DISSOLVE_MS = 300;
export const DEMO_LOADING_STEP_MS = 800;
// Temporary demo policy: one Prototype cycle, independent of any real API latency.
export const DEMO_LOADING_DURATION_MS = DEMO_LOADING_STEP_MS * 3;
export type LoadingPhase = 0 | 1 | 2;

type Clock = {
  setTimeout: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>;
  clearTimeout: (timer: ReturnType<typeof setTimeout>) => void;
};
const browserClock: Clock = {
  setTimeout: (callback, delay) => setTimeout(callback, delay),
  clearTimeout: (timer) => clearTimeout(timer),
};

function timeline(clock: Clock) {
  let active = true;
  const timers: ReturnType<typeof setTimeout>[] = [];
  return {
    after(delay: number, callback: () => void) {
      timers.push(clock.setTimeout(() => { if (active) callback(); }, delay));
    },
    cancel() {
      active = false;
      timers.forEach((timer) => clock.clearTimeout(timer));
    },
  };
}

export function startDemoLoading(onPhase: (phase: LoadingPhase) => void, onComplete: () => void, clock = browserClock) {
  const sequence = timeline(clock);
  sequence.after(DEMO_LOADING_STEP_MS, () => onPhase(1));
  sequence.after(DEMO_LOADING_STEP_MS * 2, () => onPhase(2));
  sequence.after(DEMO_LOADING_DURATION_MS, () => { sequence.cancel(); onComplete(); });
  return sequence.cancel;
}

export function startSplashTransition(onDissolve: () => void, onComplete: () => void, reducedMotion: boolean, clock = browserClock) {
  const sequence = timeline(clock);
  const finish = () => { sequence.cancel(); onComplete(); };
  sequence.after(SPLASH_WAIT_MS, () => {
    if (reducedMotion) finish();
    else {
      onDissolve();
      sequence.after(SPLASH_DISSOLVE_MS, finish);
    }
  });
  return sequence.cancel;
}
