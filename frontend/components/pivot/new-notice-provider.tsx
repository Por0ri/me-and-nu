"use client";

import { createContext, useContext, useEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { createServiceNotificationInbox, type NotificationInboxSource } from "@/lib/pivot/notification-inbox";
import { createNewNoticeStore } from "@/lib/pivot/new-notice";
import { isActiveSession } from "@/lib/pivot/services";
import { usePivotSession } from "./pivot-session";
import { NewNoticeContext } from "./home-screen";

const SourceContext = createContext<NotificationInboxSource | undefined>(undefined);
const noSubscribe = () => () => {};
const noNotice = () => false;
export const useNotificationSource = () => useContext(SourceContext);

/** Lives in the layout so HOME and separate MY routes use the same session-owned source. */
export function NewNoticeProvider({ children }: { children: ReactNode }) {
  const { state, refresh } = usePivotSession();
  const pathname = usePathname();
  const owner = state.status === "ready" && isActiveSession(state.session) ? state.session : null;
  const sessionRef = useRef(owner);
  sessionRef.current = owner;
  const store = useMemo(() => owner ? createNewNoticeStore(createServiceNotificationInbox(() => sessionRef.current, refresh)) : null, [owner, refresh]);
  const hasNewNotice = useSyncExternalStore(store?.subscribe ?? noSubscribe, store?.getSnapshot ?? noNotice, noNotice);
  useEffect(() => {
    if (!store) return;
    store.start();
    const revalidate = () => { if (document.visibilityState === "visible") void store.refresh(); };
    let midnight: ReturnType<typeof setTimeout>;
    const scheduleMidnight = () => {
      const next = new Date(); next.setHours(24, 0, 0, 0);
      midnight = setTimeout(() => { void store.refresh(true); scheduleMidnight(); }, next.getTime() - Date.now());
    };
    scheduleMidnight();
    window.addEventListener("focus", revalidate);
    document.addEventListener("visibilitychange", revalidate);
    return () => {
      store.stop(); clearTimeout(midnight);
      window.removeEventListener("focus", revalidate);
      document.removeEventListener("visibilitychange", revalidate);
    };
  }, [store]);
  useEffect(() => { void store?.refresh(); }, [store, pathname]);
  return <SourceContext.Provider value={store?.source}><NewNoticeContext.Provider value={hasNewNotice}>{children}</NewNoticeContext.Provider></SourceContext.Provider>;
}
