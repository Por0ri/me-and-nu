"use client";

import { useLayoutEffect, useRef } from "react";
import styles from "./home.module.css";

type Rect = { left: number; top: number; right: number; bottom: number };
type Obstacle = Rect & { scrolls?: boolean };
type Offset = { x: number; y: number };
const clearance = 4;
const scrollClearance = 20;
const settleDelay = 190;
const overlaps = (a: Rect, b: Rect, gap = 0) =>
  a.left < b.right + gap && a.right > b.left - gap && a.top < b.bottom + gap && a.bottom > b.top - gap;

/** Keep a safe temporary position until scrolling settles; never move for a near miss. */
export function chooseHomeAIPosition(base: Rect, leftLane: number, current: Offset,
  obstacles: readonly Obstacle[], viewport: Rect, mayReturn: boolean): Offset | null {
  // The SVG includes a non-interactive shadow. Only the actual button is a hit target.
  const at = ({ x, y }: Offset): Rect => ({ left: base.left + x, right: base.right + x, top: base.top + y, bottom: base.bottom + y });
  const free = (box: Rect, gap = 0) =>
    box.left >= viewport.left && box.right <= viewport.right && box.top >= viewport.top && box.bottom <= viewport.bottom &&
    !obstacles.some(rect => overlaps(box, rect, gap));
  function missingScrollClearance(box: Rect, gap: number) {
    let missing = 0;
    for (const rect of obstacles) {
      if (!rect.scrolls || box.left >= rect.right + gap || box.right <= rect.left - gap) continue;
      const verticalGap = Math.max(box.top - rect.bottom, rect.top - box.bottom);
      missing = Math.max(missing, gap + scrollClearance - verticalGap);
    }
    return missing;
  }
  const home = { x: 0, y: 0 }, displaced = current.x !== 0 || current.y !== 0;
  const currentIsFree = free(at(current));
  if (currentIsFree && (!displaced || !mayReturn)) return current;
  if (displaced && mayReturn && free(base, clearance) && !missingScrollClearance(base, clearance)) return home;
  if (currentIsFree) return current;

  function nearestFree(gap: number) {
    const xs = new Set([current.x, 0, leftLane - base.left, viewport.left - base.left, viewport.right - base.right]);
    const ys = new Set([current.y, 0, viewport.top - base.top, viewport.bottom - base.bottom]);
    for (const rect of obstacles) {
      xs.add(rect.left - gap - base.right); xs.add(rect.right + gap - base.left);
      const above = rect.top - gap - base.bottom, below = rect.bottom + gap - base.top;
      ys.add(above); ys.add(below);
      if (rect.scrolls) { ys.add(above - scrollClearance); ys.add(below + scrollClearance); }
    }
    let nearest: Offset | null = null, bestCost = Infinity;
    for (const x of xs) {
      for (const y of ys) {
        const candidate = { x, y }, box = at(candidate);
        const distance = Math.hypot(x - current.x, y - current.y);
        if (distance >= bestCost || !free(box, gap)) continue;
        // Favor room for the next bit of scroll without making a long jump to get it.
        const cost = distance + 2 * missingScrollClearance(box, gap);
        if (cost < bestCost) { nearest = candidate; bestCost = cost; }
      }
    }
    return nearest;
  }
  return nearestFree(clearance) ?? nearestFree(0);
}

export function HomeAIButton({ onNotice }: { onNotice: (message: string) => void }) {
  const anchor = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    const slot = anchor.current, control = button.current, root = slot?.closest("main");
    if (!slot || !control || !root) return;
    let offset: Offset = { x: 0, y: 0 }, frame = 0, settle = 0, moving = false, pressed = false;
    const visibleControls = (viewport: Rect) => {
      const rects: Obstacle[] = [];
      const top = viewport.top - scrollClearance - clearance, bottom = viewport.bottom + scrollClearance + clearance;
      for (const card of root.querySelectorAll<HTMLElement>("[data-home-post]")) {
        const cardBox = card.getBoundingClientRect();
        if (cardBox.bottom <= top || cardBox.top >= bottom) continue;
        for (const target of card.querySelectorAll<HTMLElement>("button")) {
          const css = getComputedStyle(target), box = target.getBoundingClientRect();
          if (!target.closest("[inert]") && css.pointerEvents !== "none" && css.visibility === "visible" &&
            Number(css.opacity) > 0 && box.width && box.height && overlaps(box, cardBox) && box.bottom > top && box.top < bottom) {
            rects.push({ left: box.left, right: box.right, top: box.top, bottom: box.bottom, scrolls: true });
          }
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
      if (!base.width || !base.height) return; // Home remains mounted while Detail is open.
      const visual = window.visualViewport;
      const viewport = { left: visual?.offsetLeft ?? 0, top: visual?.offsetTop ?? 0,
        right: (visual?.offsetLeft ?? 0) + (visual?.width ?? innerWidth), bottom: (visual?.offsetTop ?? 0) + (visual?.height ?? innerHeight) };
      const next = chooseHomeAIPosition(base, dock.left + 20, offset, visibleControls(viewport), viewport, !moving);
      if (!next || next.x === offset.x && next.y === offset.y) return;
      offset = next;
      control!.style.setProperty("--home-ai-x", offset.x + "px");
      control!.style.setProperty("--home-ai-y", offset.y + "px");
      control!.dataset.aiDisplaced = String(offset.x !== 0 || offset.y !== 0);
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    const motion = () => {
      moving = true;
      window.clearTimeout(settle);
      schedule();
      settle = window.setTimeout(() => { moving = false; schedule(); }, settleDelay);
    };
    // Horizontal carousel scrolls do not move the fixed dock or card action targets.
    const scroll = (event: Event) => { if (event.target === document) motion(); };
    const down = () => { pressed = true; };
    const up = () => { pressed = false; schedule(); };
    const resize = new ResizeObserver(motion); resize.observe(root); resize.observe(slot);
    const mutation = new MutationObserver(schedule);
    mutation.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ["class", "inert", "hidden", "disabled"] });
    window.addEventListener("scroll", scroll, { passive: true, capture: true });
    window.addEventListener("resize", motion);
    window.visualViewport?.addEventListener("resize", motion);
    window.visualViewport?.addEventListener("scroll", motion);
    root.addEventListener("focusin", schedule); root.addEventListener("focusout", schedule);
    root.addEventListener("pointerover", schedule); root.addEventListener("pointerout", schedule);
    control.addEventListener("pointerdown", down);
    window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
    update();
    return () => {
      cancelAnimationFrame(frame); window.clearTimeout(settle); resize.disconnect(); mutation.disconnect();
      window.removeEventListener("scroll", scroll, true); window.removeEventListener("resize", motion);
      window.visualViewport?.removeEventListener("resize", motion);
      window.visualViewport?.removeEventListener("scroll", motion);
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
