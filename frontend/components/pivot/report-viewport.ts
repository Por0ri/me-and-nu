import { useEffect, type RefObject } from "react";

// Keep the current insertion point visible after a viewport resize, including
// selection in the middle of a long textarea (scrollHeight would jump to the end).
function revealCaret(field: HTMLTextAreaElement) {
  const style = getComputedStyle(field), mirror = document.createElement("div"), caret = document.createElement("span");
  for (const key of ["box-sizing", "width", "font-family", "font-size", "font-weight", "line-height", "letter-spacing", "padding", "border-width", "border-style", "word-break", "overflow-wrap", "tab-size"])
    mirror.style.setProperty(key, style.getPropertyValue(key));
  Object.assign(mirror.style, { position: "fixed", visibility: "hidden", whiteSpace: "pre-wrap", overflowWrap: "break-word", top: "0", left: "0", height: "auto" });
  mirror.setAttribute("aria-hidden", "true");
  const position = field.selectionDirection === "backward" ? field.selectionStart : field.selectionEnd;
  mirror.textContent = field.value.slice(0, position);
  caret.textContent = field.value.slice(position) || ".";
  mirror.append(caret); document.body.append(mirror);
  const top = caret.offsetTop, line = parseFloat(style.lineHeight) || 24;
  const padding = parseFloat(style.paddingTop) || 0;
  const bottomPadding = parseFloat(style.paddingBottom) || 0;
  if (top < field.scrollTop + padding) field.scrollTop = Math.max(0, top - padding);
  else if (top + line > field.scrollTop + field.clientHeight - bottomPadding) field.scrollTop = top + line - field.clientHeight + bottomPadding;
  mirror.remove();
}

export function useReportViewport(dialog: RefObject<HTMLDialogElement | null>) {
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const viewport = window.visualViewport;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const height = viewport?.height ?? window.innerHeight;
        element.style.setProperty("--report-viewport-height", `${height}px`);
        element.style.setProperty("--report-viewport-top", `${viewport?.offsetTop ?? 0}px`);
        element.style.setProperty("--report-viewport-left", `${viewport?.offsetLeft ?? 0}px`);
        element.style.setProperty("--report-viewport-width", `${viewport?.width ?? window.innerWidth}px`);
        const active = document.activeElement;
        if (!(active instanceof HTMLElement) || !element.contains(active)) return;
        const bounds = element.getBoundingClientRect(), box = active.getBoundingClientRect();
        const top = bounds.top + 8, bottom = Math.min(bounds.bottom, (viewport?.offsetTop ?? 0) + height) - 8;
        if (box.bottom > bottom) element.scrollTop += box.bottom - bottom;
        else if (box.top < top) element.scrollTop -= top - box.top;
        if (active instanceof HTMLTextAreaElement) revealCaret(active);
      });
    };
    update();
    viewport?.addEventListener("resize", update); viewport?.addEventListener("scroll", update);
    window.addEventListener("resize", update);
    for (const event of ["focusin", "input", "keyup", "click", "compositionend"]) element.addEventListener(event, update);
    document.addEventListener("selectionchange", update);
    return () => {
      cancelAnimationFrame(frame);
      viewport?.removeEventListener("resize", update); viewport?.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      for (const event of ["focusin", "input", "keyup", "click", "compositionend"]) element.removeEventListener(event, update);
      document.removeEventListener("selectionchange", update);
    };
  }, [dialog]);
}
