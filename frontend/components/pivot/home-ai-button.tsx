"use client";

import { useLayoutEffect, useRef } from "react";
import styles from "./home.module.css";

type Rect = { left: number; top: number; right: number; bottom: number };
type Offset = { x: number; y: number };
const overlaps = (a: Rect, b: Rect, gap = 0) =>
  a.left < b.right + gap && a.right > b.left - gap && a.top < b.bottom + gap && a.bottom > b.top - gap;

/** Keep a safe temporary position until scrolling settles; never move for a near miss. */
export function chooseHomeAIPosition(base: Rect, leftLane: number, current: Offset,
  obstacles: readonly Rect[], viewport: Rect, mayReturn: boolean): Offset | null {
  const at = ({ x, y }: Offset): Rect => ({ left: base.left - 8 + x, right: base.left + 48 + x, top: base.top + y, bottom: base.top + 56 + y });
  const free = (offset: Offset, gap = 0) => {
    const box = at(offset);
    return box.left >= viewport.left && box.right <= viewport.right && box.top >= viewport.top && box.bottom <= viewport.bottom &&
      !obstacles.some(rect => overlaps(box, rect, gap));
  };
  const home = { x: 0, y: 0 }, displaced = current.x !== 0 || current.y !== 0;
  if (!displaced && free(home)) return home;
  if (displaced && mayReturn && free(home, 4)) return home;
  if (displaced && free(current)) return current;
  // The empty left end of the existing dock is preferred over moving vertically.
  const left = leftLane - base.left;
  for (let rise = 0; rise <= viewport.bottom - viewport.top; rise += 64) {
    for (const x of [left, 0]) {
      if (x === 0 && rise === 0) continue;
      const candidate = { x, y: -rise };
      if (free(candidate, 8)) return candidate;
    }
  }
  return null;
}

export function HomeAIButton({ onNotice }: { onNotice: (message: string) => void }) {
  const anchor = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    const slot = anchor.current, control = button.current, root = slot?.closest("main");
    if (!slot || !control || !root) return;
    let offset: Offset = { x: 0, y: 0 }, frame = 0, settle = 0, lastMotion = -Infinity, pressed = false;
    const visibleControls = () => {
      const rects: Rect[] = [];
      for (const card of root.querySelectorAll<HTMLElement>("[data-home-post]")) {
        const cardBox = card.getBoundingClientRect();
        if (cardBox.bottom <= 0 || cardBox.top >= innerHeight) continue;
        for (const target of card.querySelectorAll<HTMLElement>("button")) {
          const css = getComputedStyle(target), box = target.getBoundingClientRect();
          if (!target.closest("[inert]") && css.visibility !== "hidden" && Number(css.opacity) > 0 && box.width && box.height &&
            overlaps(box, cardBox) && box.bottom > 0 && box.top < innerHeight) rects.push(box);
        }
      }
      const navigation = slot.parentElement?.querySelector("nav");
      if (navigation) rects.push(navigation.getBoundingClientRect());
      return rects;
    };
    function update() {
      frame = 0;
      if (pressed) return;
      const base = slot!.getBoundingClientRect(), dock = slot!.parentElement!.getBoundingClientRect();
      const visual = window.visualViewport;
      const viewport = { left: visual?.offsetLeft ?? 0, top: visual?.offsetTop ?? 0,
        right: (visual?.offsetLeft ?? 0) + (visual?.width ?? innerWidth), bottom: (visual?.offsetTop ?? 0) + (visual?.height ?? innerHeight) };
      const next = chooseHomeAIPosition(base, dock.left + 20, offset, visibleControls(), viewport, performance.now() - lastMotion >= 180);
      if (!next || next.x === offset.x && next.y === offset.y) return;
      offset = next;
      control!.style.setProperty("--home-ai-x", offset.x + "px");
      control!.style.setProperty("--home-ai-y", offset.y + "px");
      control!.dataset.aiDisplaced = String(offset.x !== 0 || offset.y !== 0);
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    const motion = () => {
      lastMotion = performance.now(); window.clearTimeout(settle);
      schedule(); settle = window.setTimeout(schedule, 190);
    };
    const down = () => { pressed = true; };
    const up = () => { pressed = false; schedule(); };
    const resize = new ResizeObserver(motion); resize.observe(root); resize.observe(slot);
    const mutation = new MutationObserver(schedule);
    mutation.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ["class", "inert", "hidden"] });
    window.addEventListener("scroll", motion, { passive: true, capture: true });
    window.addEventListener("resize", motion);
    window.visualViewport?.addEventListener("resize", motion);
    root.addEventListener("focusin", schedule); root.addEventListener("focusout", schedule);
    root.addEventListener("pointerover", schedule); root.addEventListener("pointerout", schedule);
    control.addEventListener("pointerdown", down);
    window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
    update();
    return () => {
      cancelAnimationFrame(frame); window.clearTimeout(settle); resize.disconnect(); mutation.disconnect();
      window.removeEventListener("scroll", motion, true); window.removeEventListener("resize", motion);
      window.visualViewport?.removeEventListener("resize", motion);
      root.removeEventListener("focusin", schedule); root.removeEventListener("focusout", schedule);
      root.removeEventListener("pointerover", schedule); root.removeEventListener("pointerout", schedule);
      control.removeEventListener("pointerdown", down);
      window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up);
    };
  }, []);
  return <span ref={anchor} className={styles.aiAnchor}>
    <button ref={button} type="button" className={styles.aiButton} aria-label="AI 콘텐츠 탐색" aria-disabled="true" data-home-ai=""
      onClick={() => onNotice("AI 콘텐츠 탐색은 준비 중이에요.")}><img src="/pivot/home/chrome/ai.svg" alt="" /></button>
  </span>;
}
