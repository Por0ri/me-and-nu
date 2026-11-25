"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { HomeCardModel } from "@/lib/pivot/home";
import type { HomeReturnSnapshot } from "@/lib/pivot/detail-navigation";
import styles from "./home.module.css";

/** Scoped to the feed list: seed unloaded tracks before restoring HOME's scroll. */
export const RESTORE_HOME_TRACKS = "pivot:restore-home-tracks";
export type HomeTrackRestore = Pick<HomeReturnSnapshot, "tracks" | "focusContentId">;

const OVERSCAN = 400;
const INITIAL_CARDS = 3;
const cardKey = (card: HomeCardModel) => JSON.stringify([card.topicId ?? null, card.id]);

type CardRender = {
  card: HomeCardModel;
  index: number;
  initialSlideIndex: number;
  onSlideIndexChange(index: number): void;
};

/** Keep native feed geometry and accessible entries, but release offscreen card trees.
 * The cache contains only slide numbers, never DOM nodes, image blobs, or URLs. */
export function WindowedHomeFeed({ cards, renderCard, keepFirstMounted = false }: {
  cards: readonly HomeCardModel[];
  renderCard: (props: CardRender) => ReactNode;
  keepFirstMounted?: boolean;
}) {
  const list = useRef<HTMLUListElement>(null);
  const positions = useRef(new Map<string, number>());
  const [nearby, setNearby] = useState<ReadonlySet<string>>(new Set(cards.slice(0, INITIAL_CARDS).map(cardKey)));
  const [pinned, setPinned] = useState<string | null>(null);
  const pendingFocus = useRef<{ key: string; from: HTMLElement } | null>(null);

  useLayoutEffect(() => {
    const element = list.current;
    if (!element) return;
    const restore = (event: Event) => {
      const detail = (event as CustomEvent<HomeTrackRestore>).detail;
      if (!detail || !Array.isArray(detail.tracks)) return;
      const saved = new Map(detail.tracks.filter(item => item && typeof item.postId === "string" && Number.isInteger(item.index) && item.index >= 0)
        .map(item => [item.postId, item.index]));
      cards.forEach((card, index) => {
        const value = saved.get(card.id);
        if (value === undefined) return;
        const position = Math.max(0, Math.min(value, card.slides.length - 1));
        positions.current.set(cardKey(card), position);
        (element.children[index] as HTMLElement).dataset.homeSlideIndex = String(position);
      });
      // Returning to DETAIL via TALK can rebuild HOME while it is hidden. Its
      // opener must exist before the following frame restores keyboard focus.
      const opener = detail.focusContentId ? cards.find(card => card.detailContentId === detail.focusContentId) : undefined;
      if (opener) setPinned(cardKey(opener));
    };
    element.addEventListener(RESTORE_HOME_TRACKS, restore);
    return () => element.removeEventListener(RESTORE_HOME_TRACKS, restore);
  }, [cards]);

  useEffect(() => {
    const element = list.current;
    if (!element) return;
    const root = element.closest<HTMLElement>("[data-home-state]");
    const keys = new Set(cards.map(cardKey));
    for (const key of positions.current.keys()) if (!keys.has(key)) positions.current.delete(key);
    setPinned(current => current && keys.has(current) ? current : null);
    let disposed = false;
    const visibleRoot = () => !disposed && !element.closest("[hidden]") &&
      !!element.getClientRects().length && (!root || (root.clientWidth > 0 && root.clientHeight > 0));
    const slots = Array.from(element.children) as HTMLLIElement[];
    const slotKeys = new Map<Element, string>(slots.map((slot, index) => [slot, cardKey(cards[index])]));
    const publish = (next: Set<string>) => setNearby(current =>
      disposed || (current.size === next.size && [...current].every(key => next.has(key))) ? current : next);
    const measure = () => {
      if (!visibleRoot()) return;
      const bounds = root?.getBoundingClientRect();
      const top = (bounds?.top ?? 0) - OVERSCAN;
      const bottom = (bounds?.bottom ?? window.innerHeight) + OVERSCAN;
      const next = new Set<string>();
      for (const slot of slots) {
        const rect = slot.getBoundingClientRect();
        if (rect.bottom >= top && rect.top <= bottom) next.add(slotKeys.get(slot)!);
      }
      publish(next);
    };
    // Also handles a restored scroll offset or cards appended below the viewport.
    measure();
    // Reconcile the light slots once per delivery, rather than applying old
    // enter/leave entries to state. Hidden HOME can be rebuilt at a deep scroll
    // offset; false-to-false targets never emit a removal for its initial cards.
    // Read the whole geometry batch before publishing any React update.
    const observer = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(measure,
      { root, rootMargin: `${OVERSCAN}px 0px` });
    slots.forEach(slot => observer?.observe(slot));
    // Observe only ancestor visibility attributes, not the growing card subtree.
    // Reconcile once on reveal even if IO has no new intersecting target to emit.
    const visibility = typeof MutationObserver === "undefined" ? null : new MutationObserver(measure);
    for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
      visibility?.observe(ancestor, { attributes: true, attributeFilter: ["hidden"] });
    }
    const scrollTarget = root ?? window;
    if (!observer) scrollTarget.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    const focusOutside = (event: FocusEvent) => {
      if (visibleRoot() && event.target instanceof Node && !element.contains(event.target)) setPinned(null);
    };
    document.addEventListener("focusin", focusOutside);
    return () => {
      disposed = true;
      observer?.disconnect();
      visibility?.disconnect();
      if (!observer) scrollTarget.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
      document.removeEventListener("focusin", focusOutside);
    };
  }, [cards]);

  useLayoutEffect(() => {
    const pending = pendingFocus.current;
    if (!pending) return;
    const slot = Array.from(list.current?.children ?? []).find(item => (item as HTMLElement).dataset.homeFeedKey === pending.key);
    const target = slot?.querySelector<HTMLElement>("[data-home-track], [data-home-detail]");
    if (!target) return;
    pendingFocus.current = null;
    // A removed placeholder leaves focus on body; do not steal a newer focus.
    if (document.activeElement === pending.from || (!pending.from.isConnected && document.activeElement === document.body)) {
      target.focus({ preventScroll: true });
    }
  });

  function activate(key: string, from: HTMLButtonElement) {
    pendingFocus.current = { key, from };
    setPinned(key);
  }

  return <ul ref={list} data-home-feed-window="">
    {cards.map((card, index) => {
      const key = cardKey(card);
      const mounted = cards.length <= INITIAL_CARDS || nearby.has(key) || pinned === key || (index === 0 && keepFirstMounted);
      return <li key={key} className={styles.feedSlot} data-home-feed-slot={card.id} data-home-feed-key={key}
        data-home-slide-index={positions.current.get(key) ?? 0}
        data-home-feed-mounted={mounted ? "true" : "false"} aria-posinset={index + 1} aria-setsize={cards.length}
        onFocusCapture={() => setPinned(key)}
        onClickCapture={event => { if ((event.target as Element).closest("[data-home-detail]")) setPinned(key); }}>
        {mounted ? renderCard({ card, index, initialSlideIndex: positions.current.get(key) ?? 0,
          onSlideIndexChange: value => {
            if (!Number.isInteger(value) || value < 0) return;
            positions.current.set(key, value);
            // A TALK snapshot must include cards which have already unmounted.
            const slot = list.current?.children[index] as HTMLElement | undefined;
            if (slot?.dataset.homeFeedKey === key) slot.dataset.homeSlideIndex = String(value);
          },
        }) : <button type="button" className={styles.feedPlaceholder} data-home-card-activate={card.id}
          aria-label={`${card.title} 카드 보기`} onFocus={event => activate(key, event.currentTarget)}
          onClick={event => activate(key, event.currentTarget)}>
          <span>{card.title}</span><span>카드 보기</span>
        </button>}
      </li>;
    })}
  </ul>;
}
