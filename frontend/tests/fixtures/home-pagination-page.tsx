"use client";

// Imported only by the ignored preview checkout prepared by the accompanying script.
// The real adapter and components run against in-memory data, with no API transport.
import { useCallback, useMemo, useRef, useState } from "react";
import { HomeController } from "@/components/pivot/home-controller";
import { createServiceHomeSource } from "@/lib/pivot/home-service";
import type { FeedCard, Session } from "@/lib/api";
import type { HomeLoadResult } from "@/lib/pivot/home";
import styles from "@/components/pivot/pivot.module.css";

export function FixtureHomePaginationPage() {
  const [events, setEvents] = useState<string[]>([]);
  const controls = useRef({ fail: false, delay: false });
  const ready = useCallback(() => setEvents(items => [...items, "home-ready"]), []);
  const source = useMemo(() => {
    const owner: Session = { authenticated: true, sessionState: "active", onboardingCompleted: true,
      user: { id: 1, accountType: "user" }, csrfToken: "fixture-only" };
    const record = (event: string) => setEvents(items => [...items, event]);
    const card = (id: number): FeedCard => ({ kind: "content", id, topicId: 42, title: `검증 카드 ${id}`,
      productionType: "human", recommendationId: null, summary: "운영 데이터 없는 로컬 회귀 화면", imageUrl: null,
      sourceName: "fixture", sourceUrl: "", publishedAt: null, saved: false, savedItemId: null,
      recommendationReason: null, seen: false, isPromotional: false, notices: [], myReaction: { liked: false, preference: null } });
    const adapter = createServiceHomeSource({
      async getMyTopics() { return { topics: [{ id: 42, code: "anime", name: "검증", status: "active", deletedAt: null, subtopicIds: [] }] }; },
      async getFeed(_, options = {}, init = {}) {
        const index = Number(options.cursor ?? 0);
        record(`request:${index}`);
        if (index && controls.current.delay) {
          await new Promise<void>((resolve, reject) => {
            const timer = setTimeout(resolve, 3000);
            init.signal?.addEventListener("abort", () => { clearTimeout(timer); record(`abort:${index}`); reject(new Error("cancelled")); }, { once: true });
          });
        }
        if (index && controls.current.fail) { controls.current.fail = false; record(`error:${index}`); throw new Error("fixture page failure"); }
        record(`ready:${index}`);
        return { selectedTopicId: 42, sections: [{ id: "fixture", type: "recommended", title: "검증", subtopicId: null,
          contents: [card(index * 2 + 1), card(index * 2 + 2)] }], nextCursor: index < 2 ? String(index + 1) : null, emptyReason: null };
      },
      async searchFeed(_, q) { return { selectedTopicId: 42, query: q, works: [q], contents: [card(99)] }; },
      async searchWorks(_, q) { return { selectedTopicId: 42, query: q, works: [] }; },
    }, () => {}, () => owner);
    function illustrated(result: HomeLoadResult): HomeLoadResult {
      if (result.status !== "ready") return result;
      return { ...result, home: { ...result.home, cards: result.home.cards.map(item => ({ ...item,
        slides: [1, 2, 3].map(n => ({ id: `${item.id}-${n}`, order: n, description: `검증 슬라이드 ${n}`,
          image: { src: `/pivot/home/slide-${n}.png`, alt: `검증 슬라이드 ${n}` } })),
      })) }, nextPage: result.nextPage ? async options => illustrated(await result.nextPage!(options)) : undefined };
    }
    return { ...adapter, load: async (options: { signal: AbortSignal }) => illustrated(await adapter.load(options)) };
  }, []);
  return <div className={styles.root}>
    <HomeController source={source} onReady={ready} />
    <details style={{ position: "fixed", zIndex: 30, right: 0, top: 0, maxWidth: 180, background: "white", fontSize: 12 }}>
      <summary>격리 검증 제어</summary>
      <button onClick={() => { controls.current.fail = true; }}>다음 페이지 1회 오류</button>
      <label><input type="checkbox" onChange={e => { controls.current.delay = e.target.checked; }} />후속 요청 3초 지연</label>
      <output data-testid="fixture-events" style={{ display: "block" }}>{events.join(" · ")}</output>
    </details>
  </div>;
}
