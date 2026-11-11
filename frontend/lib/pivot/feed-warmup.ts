import { getFeed, type FeedResponse } from "@/lib/api";
import { DEMO_LOADING_DURATION_MS } from "./transitions";

/** At least one full turn of the loading artwork, and the longest the feed is held back. */
export const FEED_LOADING_LIMITS = { min: DEMO_LOADING_DURATION_MS, max: 10_000 };
const FIRST_PICTURES = 4;

export type FeedWarmUpDeps = {
  load: (topicId: number) => Promise<FeedResponse>;
  preload: (url: string) => Promise<void>;
  wait: (ms: number) => Promise<void>;
  now: () => number;
};

const browserDeps: FeedWarmUpDeps = {
  // The same first request as the feed screen (lib/consumer-api/home.ts), so the answer is ready when it asks.
  load: topicId => getFeed(topicId, { limit: 100 }),
  preload: url => new Promise(resolve => { const image = new Image(); image.onload = image.onerror = () => resolve(); image.src = url; }),
  wait: ms => new Promise(resolve => setTimeout(resolve, ms)),
  now: () => Date.now(),
};

/** After onboarding or a My Page taste edit the first feed request waits up to about 3 s for the first
 * recommendation round. This asks for that first page and its first pictures while the loading screen shows,
 * so the feed opens ready: at least one turn of the artwork since `since`, at most 10 s. A failure only leaves
 * the feed screen to load it itself. */
export async function warmUpFeed(topicId: number | null, since: number, deps: FeedWarmUpDeps = browserDeps,
  limits = FEED_LOADING_LIMITS): Promise<void> {
  const ready = (async () => {
    if (!topicId) return;
    try {
      const page = await deps.load(topicId);
      const pictures = page.sections.flatMap(section => section.contents.map(card => card.imageUrl))
        .filter((url): url is string => !!url).slice(0, FIRST_PICTURES);
      await Promise.all(pictures.map(deps.preload));
    } catch { /* the feed screen asks again */ }
  })();
  await Promise.race([ready, deps.wait(Math.max(0, limits.max - (deps.now() - since)))]);
  const left = limits.min - (deps.now() - since);
  if (left > 0) await deps.wait(left);
}
