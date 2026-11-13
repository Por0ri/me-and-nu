"use client";

import { useEffect } from "react";

/** Editor entry links use document navigation so Back also runs beforeunload. */
export function useContentInputGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    let leaving = false;
    const unload = (event: BeforeUnloadEvent) => {
      if (leaving) return;
      event.preventDefault();
      event.returnValue = "";
    };
    const click = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (!(anchor instanceof HTMLAnchorElement) || anchor.download || (anchor.target && anchor.target !== "_self")) return;
      const destination = new URL(anchor.href);
      if (destination.origin === location.origin && destination.pathname === location.pathname && destination.search === location.search) return;
      if (!window.confirm("입력한 내용이 저장되지 않았습니다. 저장하지 않고 이동할까요?")) {
        event.preventDefault(); event.stopImmediatePropagation();
      } else leaving = true;
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", click, true);
    };
  }, [dirty]);
}
