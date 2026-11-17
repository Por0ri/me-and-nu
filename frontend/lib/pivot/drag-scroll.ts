// Mouse drag for sideways chip rows (Q1 selected works, the result's answer chips). 2026-11 design feedback:
// a mouse without a sideways wheel could not move them. Touch and pens keep the native swipe.
import { useRef, type HTMLAttributes, type PointerEvent } from "react";

/** The same start distance as the detail cards' mouse drag (content-carousel.tsx). */
const DRAG_START = 6;

type Drag = { pointerId: number; x: number; y: number; left: number; moving: boolean };

/** Props for a sideways-scrolling element; one hook can serve several rows. A drag never ends in a click,
 * so letting go over a chip's × does not remove it, while a plain click works as before. */
export function useDragScroll<T extends HTMLElement>(): HTMLAttributes<T> & { "data-drag-scroll": "" } {
  const drag = useRef<Drag | null>(null);
  const dragged = useRef(false);
  const finish = (event: PointerEvent<T>) => {
    const start = drag.current;
    if (!start || start.pointerId !== event.pointerId) return;
    drag.current = null;
    const row = event.currentTarget;
    if (row.hasPointerCapture?.(event.pointerId)) row.releasePointerCapture(event.pointerId);
    delete row.dataset.dragging;
    // The click that follows this release is swallowed below; nothing later is.
    if (start.moving) window.setTimeout(() => { dragged.current = false; }, 0);
  };
  return {
    "data-drag-scroll": "",
    // The grab cursor shows only when there is something to slide.
    onPointerEnter: (event) => {
      const row = event.currentTarget;
      row.dataset.scrollable = String(row.scrollWidth > row.clientWidth + 1);
    },
    onPointerDown: (event) => {
      if (event.pointerType !== "mouse" || event.button !== 0) return;
      dragged.current = false;
      drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: event.currentTarget.scrollLeft, moving: false };
    },
    onPointerMove: (event) => {
      const start = drag.current;
      if (!start || start.pointerId !== event.pointerId) return;
      if (event.buttons !== 1) { finish(event); return; }
      const row = event.currentTarget;
      const dx = event.clientX - start.x, dy = event.clientY - start.y;
      if (!start.moving) {
        if (Math.abs(dy) > DRAG_START && Math.abs(dy) >= Math.abs(dx)) { drag.current = null; return; }
        if (Math.abs(dx) < DRAG_START) return;
        if (row.scrollWidth <= row.clientWidth + 1) { drag.current = null; return; }
        start.moving = true;
        dragged.current = true;
        row.setPointerCapture(event.pointerId);
        row.dataset.dragging = "true";
      }
      event.preventDefault();
      row.scrollLeft = start.left - dx;
    },
    onPointerUp: finish,
    onPointerCancel: finish,
    onLostPointerCapture: finish,
    onClickCapture: (event) => {
      if (!dragged.current) return;
      dragged.current = false;
      event.preventDefault();
      event.stopPropagation();
    },
  };
}
