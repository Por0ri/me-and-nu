"use client";

import { createContext, useContext, useEffect, useLayoutEffect, useRef, useSyncExternalStore, type ReactNode, type RefObject } from "react";
import { ACTIVITY_BOUNDARY_EVENT, ACTIVITY_RESUME_EVENT, createExposure, createCarouselMeasurement, type ActivityQueue } from "@/lib/pivot/activity";
import type { ReactionControls } from "@/lib/pivot/reaction-service";
import type { HomeDetailTarget } from "@/lib/pivot/home";

export type InteractionScope = { activity: ActivityQueue; reactions: ReactionControls };
const InteractionContext = createContext<InteractionScope | null>(null);
export const InteractionViewContext = createContext("home");
const noSubscribe = () => () => {};
const noSnapshot = () => 0;
export function InteractionProvider({ scope, children }: { scope: InteractionScope; children: ReactNode }) {
  useLayoutEffect(() => {
    scope.activity.start();
    const flush = () => { void scope.activity.flush(document.visibilityState === "hidden"); };
    window.addEventListener("online", flush);
    const leaving = () => queueMicrotask(flush);
    window.addEventListener("pagehide", leaving);
    // Exposure handlers finish first, then flush their queued measurements.
    const hidden = () => { if (document.visibilityState === "hidden") queueMicrotask(flush); };
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("online", flush); window.removeEventListener("pagehide", leaving);
      document.removeEventListener("visibilitychange", hidden);
      scope.activity.stop(); scope.reactions.clear();
    };
  }, [scope]);
  return <InteractionContext.Provider value={scope}>{children}</InteractionContext.Provider>;
}
export function useInteractions() {
  const scope = useContext(InteractionContext);
  useSyncExternalStore(scope?.reactions.subscribe ?? noSubscribe, scope?.reactions.getSnapshot ?? noSnapshot, noSnapshot);
  return scope;
}
function foreground(element: HTMLElement) { return document.visibilityState !== "hidden" && !element.closest("[hidden]"); }

export function useHomeExposure(ref: RefObject<HTMLElement | null>, target: HomeDetailTarget, demo: boolean) {
  const scope = useContext(InteractionContext), view = useContext(InteractionViewContext);
  const { postId, contentId, topicId, recommendationId, recommendationReason } = target;
  const carousel = useRef(createCarouselMeasurement());
  useEffect(() => {
    const element = ref.current;
    if (demo || !scope || !element || view !== "home") return;
    const target = { postId, contentId, topicId, recommendationId, recommendationReason };
    scope.activity.remember(target);
    const exposure = createExposure();
    let inView = false;
    function finish() {
      const value = exposure.finish();
      if (value.duration >= 1000) scope!.activity.record(target, "impression", { surface: "home_feed", viewMode: "card", visibleMs: value.duration });
    }
    function update() {
      const visible = inView && foreground(element!);
      if (!visible) finish();
      exposure.setVisible(visible);
    }
    const observer = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(entries => {
      // A tall card can exceed the viewport; measure half of the visible viewport/card.
      const entry = entries[0];
      inView = !!entry?.isIntersecting && entry.intersectionRect.height >= Math.min(entry.boundingClientRect.height, window.innerHeight) / 2;
      update();
    }, { threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] });
    observer?.observe(element);
    const pointer = () => carousel.current.interact();
    const wheel = (event: WheelEvent) => { if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) pointer(); };
    const keyboard = (event: KeyboardEvent) => { if (["ArrowLeft", "ArrowRight", "Home", "End", "Enter", " "].includes(event.key)) pointer(); };
    element.addEventListener("pointerdown", pointer, { passive: true });
    element.addEventListener("wheel", wheel, { passive: true });
    element.addEventListener("keydown", keyboard);
    document.addEventListener("visibilitychange", update);
    window.addEventListener("pagehide", finish);
    window.addEventListener(ACTIVITY_BOUNDARY_EVENT, finish);
    window.addEventListener(ACTIVITY_RESUME_EVENT, update);
    return () => { window.removeEventListener(ACTIVITY_RESUME_EVENT, update); element.removeEventListener("pointerdown", pointer); element.removeEventListener("wheel", wheel); element.removeEventListener("keydown", keyboard); observer?.disconnect(); document.removeEventListener("visibilitychange", update); window.removeEventListener("pagehide", finish); window.removeEventListener(ACTIVITY_BOUNDARY_EVENT, finish); finish(); };
  }, [scope, view, ref, demo, postId, contentId, topicId, recommendationId, recommendationReason]);
  return (index: number, count: number) => {
    const moved = carousel.current.move(index, count);
    if (moved && !demo && view === "home" && ref.current && foreground(ref.current))
      scope?.activity.record(target, "card_swipe", { surface: "home_feed", viewMode: "card", cardIndex: index + 1, cardCount: count });
  };
}

export function useDetailExposure(ref: RefObject<HTMLElement | null>, target: HomeDetailTarget | null, bodyChars: number, demo: boolean) {
  const scope = useContext(InteractionContext), view = useContext(InteractionViewContext);
  const postId = target?.postId, contentId = target?.contentId, topicId = target?.topicId;
  useEffect(() => {
    const element = ref.current;
    if (demo || !scope || !element || view !== "detail" || !postId || !contentId) return;
    const target = { postId, contentId, topicId }, exposure = createExposure();
    function scroll() {
      if (!foreground(element!)) return;
      const bounds = element!.getBoundingClientRect();
      exposure.scroll(bounds.height > 0 ? (window.innerHeight - bounds.top) / bounds.height * 100 : 0);
    }
    function finish() {
      const value = exposure.finish();
      if (value.duration > 0) scope!.activity.record(target, "open", { surface: "detail", activeMs: value.duration, scrollPct: value.scrollPct, bodyChars: Math.min(1000000, bodyChars) });
    }
    function visible() { if (!foreground(element!)) finish(); else { exposure.setVisible(true); scroll(); } }
    visible();
    window.addEventListener("scroll", scroll, { passive: true });
    window.addEventListener("resize", scroll);
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("pagehide", finish);
    window.addEventListener(ACTIVITY_BOUNDARY_EVENT, finish);
    window.addEventListener(ACTIVITY_RESUME_EVENT, visible);
    return () => { window.removeEventListener(ACTIVITY_RESUME_EVENT, visible); window.removeEventListener("scroll", scroll); window.removeEventListener("resize", scroll); document.removeEventListener("visibilitychange", visible); window.removeEventListener("pagehide", finish); window.removeEventListener(ACTIVITY_BOUNDARY_EVENT, finish); finish(); };
  }, [scope, view, ref, demo, postId, contentId, topicId, bodyChars]);
}
