"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";

import { ContentNotFoundError, getContent } from "@/lib/consumer-api/contents";
import { consumerRoutes } from "@/lib/consumer-routes";
import type { ContentDetail } from "@/types/content";

export type PreparedContentResult = {
  content: ContentDetail | null;
  error: "not-found" | "error" | null;
};

type PreparedNavigation = {
  contentId: string;
  topicId: string;
  result: PreparedContentResult;
  complete?: () => void;
};

type ContentNavigation = {
  navigate: (contentId: string, topicId: string) => Promise<void>;
  read: (contentId: string, topicId: string) => PreparedContentResult | null;
  release: (result: PreparedContentResult) => void;
};

const ContentNavigationContext = createContext<ContentNavigation | null>(null);

/** One navigation only; ConsumerSessionProvider remounts this when the user changes. */
export function ContentNavigationProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const prepared = useRef<PreparedNavigation | null>(null);
  const generation = useRef(0);
  const active = useRef<{ pathname: string; cancel: () => void } | null>(null);

  useEffect(() => () => {
    generation.current += 1;
    active.current?.cancel();
    prepared.current = null;
  }, []);

  useEffect(() => {
    // Invalidate a slow request even if the user later returns to the same home URL.
    generation.current += 1;
    if (active.current && pathname !== active.current.pathname) {
      active.current.cancel();
    }
    if (prepared.current && pathname !== consumerRoutes.content(prepared.current.contentId)) {
      prepared.current = null;
    }
  }, [pathname]);

  const value = useMemo<ContentNavigation>(() => ({
    async navigate(contentId, topicId) {
      if (active.current) return;
      const request = ++generation.current;
      const origin = window.location.href;
      // Prepare the body before taking snapshots; network latency must not freeze the UI.
      const result = await getContent({ contentId, topicContext: { topicId } }).then(
        (content): PreparedContentResult => {
          if (content.id !== contentId || content.topicContext.topicId !== topicId) {
            return { content: null, error: "error" };
          }
          return { content, error: null };
        },
        (cause): PreparedContentResult => ({
          content: null,
          error: cause instanceof ContentNotFoundError ? "not-found" : "error",
        }),
      );
      if (request !== generation.current || window.location.href !== origin) return;
      prepared.current = { contentId, topicId, result };

      const target = consumerRoutes.content(contentId);
      const href = `${target}?${new URLSearchParams({ topicId })}`;
      if (!document.startViewTransition || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        router.push(href);
        return;
      }

      let complete = () => {};
      const committed = new Promise<void>((resolve) => { complete = resolve; });
      prepared.current.complete = complete;
      let transition: ViewTransition | undefined;
      let timeout: ReturnType<typeof setTimeout> | undefined;
      let cancelled = false;
      let pushed = false;
      const cleanup = () => {
        clearTimeout(timeout);
        if (active.current?.cancel !== cancel) return;
        document.documentElement.classList.remove("consumer-content-opening");
        active.current = null;
      };
      const cancel = () => {
        cancelled = true;
        complete();
        transition?.skipTransition();
        cleanup();
      };
      active.current = { pathname: target, cancel };
      document.documentElement.classList.add("consumer-content-opening");
      try {
        transition = document.startViewTransition(() => {
          if (cancelled || request !== generation.current || window.location.href !== origin) return;
          pushed = true;
          router.push(href);
          // Resolve after the detail body commits, not merely after router.push returns.
          return committed;
        });
        // An interrupted route must never leave the browser waiting on a frozen snapshot.
        timeout = setTimeout(cancel, 4000);
        void transition.ready.catch(() => undefined);
        void transition.finished.catch(() => undefined).finally(cleanup);
      } catch {
        cancel();
        if (!pushed) router.push(href);
      }
    },
    read(contentId, topicId) {
      const current = prepared.current;
      return current?.contentId === contentId && current.topicId === topicId
        ? current.result
        : null;
    },
    release(result) {
      if (prepared.current?.result === result) {
        prepared.current.complete?.();
        prepared.current = null;
      }
    },
  }), [router]);

  return <ContentNavigationContext.Provider value={value}>{children}</ContentNavigationContext.Provider>;
}

export function useContentNavigation() {
  const context = useContext(ContentNavigationContext);
  if (!context) throw new Error("ContentNavigationProvider is required.");
  return context;
}
