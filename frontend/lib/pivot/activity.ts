import { apiRequest, getSession, type Session } from "@/lib/api";
import type { HomeDetailTarget } from "./home";
import { requirePivotApi } from "./services";

export type ActivityType = "impression" | "open" | "card_swipe" | "like" | "like_cancel" | "bookmark" | "bookmark_cancel" | "preference_positive" | "preference_negative" | "preference_cancel";
export type ActivityValue = { surface: "home_feed" | "detail" | "saved"; viewMode?: "card" | "list"; sessionId?: string; visibleMs?: number; activeMs?: number; scrollPct?: number; bodyChars?: number; cardIndex?: number; cardCount?: number };
export type ActivityEvent = { clientEventId: string; eventType: ActivityType; topicId: number; contentId: number; recommendationItemId?: number; occurredAt: string; value: ActivityValue };
export type ActivityResult = { saved: number; duplicated: number; rejected: number };
export type ActivityQueue = ReturnType<typeof createActivityQueue>;
export const ACTIVITY_BOUNDARY_EVENT = "pivot:activity-boundary";
export const ACTIVITY_RESUME_EVENT = "pivot:activity-resume";
export const MAX_ACTIVITY_QUEUE = 200;
const BATCH_SIZE = 25;

export function activityIdentity(target: HomeDetailTarget) {
  const contentId = Number(target.contentId), topicId = target.topicId;
  if (!/^[1-9]\d*$/.test(target.contentId) || !Number.isSafeInteger(contentId) || !Number.isSafeInteger(topicId) || (topicId ?? 0) <= 0) return null;
  return { contentId, topicId: topicId! };
}
export function ownsSession(session: Session | null, owner: number): boolean {
  return session?.authenticated === true && session.sessionState === "active" && session.onboardingCompleted === true && session.user?.id === owner && session.user.accountType === "user";
}
export function postActivityEvents(events: ActivityEvent[], init: Pick<RequestInit, "signal" | "keepalive" | "headers"> = {}): Promise<ActivityResult> {
  return apiRequest("/api/v1/users/me/activity-events", { ...init, method: "POST", body: JSON.stringify({ events }) });
}

/** Memory only: retries retain UUIDs, and an account boundary discards the queue. */
export function createActivityQueue(owner: number, readSession: () => Session | null, dependencies: {
  post?: typeof postActivityEvents; verifySession?: typeof getSession; allow?: () => void;
  uuid?: () => string; now?: () => number; schedule?: typeof setTimeout; cancel?: typeof clearTimeout;
} = {}) {
  const { post = postActivityEvents, verifySession = getSession, allow = requirePivotApi,
    uuid = () => crypto.randomUUID(), now = Date.now, schedule = setTimeout, cancel = clearTimeout } = dependencies;
  let verifiedCsrf = ownsSession(readSession(), owner) ? readSession()?.csrfToken : undefined;
  let enabled = true, generation = 0, sessionId: string | undefined, timer: ReturnType<typeof setTimeout> | undefined;
  let sending: Promise<void> | null = null, controller: AbortController | null = null, failures = 0;
  const queue: ActivityEvent[] = [];
  const recommendations = new Map<string, { recommendationId?: number; recommendationReason?: string }>();
  function allowed() { return enabled && ownsSession(readSession(), owner); }
  function clearTimer() { if (timer !== undefined) cancel(timer); timer = undefined; }
  function stop() {
    enabled = false; generation++; clearTimer(); controller?.abort(); controller = null;
    sending = null; queue.length = 0; failures = 0; sessionId = undefined; verifiedCsrf = undefined; recommendations.clear();
  }
  function scheduleFlush(delay = 3000) {
    if (timer !== undefined || !queue.length || !allowed()) return;
    timer = schedule(() => { timer = undefined; void flush(); }, delay);
  }
  async function authorize() {
    allow();
    if (!allowed()) throw new Error("로그인 상태가 변경되었어요. 다시 확인해 주세요.");
    const epoch = generation, verified = await verifySession();
    if (epoch !== generation || !allowed() || !ownsSession(verified, owner)) { stop(); throw new Error("로그인 상태가 변경되었어요. 다시 확인해 주세요."); }
    if (typeof verified.csrfToken !== "string" || !verified.csrfToken) throw new Error("세션 확인에 실패했어요.");
    verifiedCsrf = verified.csrfToken;
    return { headers: { "X-CSRF-Token": verified.csrfToken } };
  }
  async function flush(keepalive = false): Promise<void> {
    if (!allowed()) { stop(); return; }
    if (sending) return sending;
    if (!queue.length) return;
    clearTimer();
    const epoch = generation, batch = queue.slice(0, BATCH_SIZE), abort = new AbortController();
    controller = abort;
    sending = Promise.resolve().then(async () => {
      try {
        allow();
        // A rotated cookie in another tab must never attribute this queue to a new user.
        // The known owner's pinned token is safe on page exit: a different
        // cookie rejects it, and no cancellable GET must finish before POST.
        const verified = keepalive && verifiedCsrf ? { headers: { "X-CSRF-Token": verifiedCsrf } } : await authorize();
        if (epoch !== generation || abort.signal.aborted) return;
        const result = await post(batch, { ...verified, signal: abort.signal, keepalive });
        if (epoch !== generation || abort.signal.aborted) return;
        if (!allowed()) { stop(); return; }
        if (![result?.saved, result?.duplicated, result?.rejected].every(x => Number.isInteger(x) && x >= 0) ||
          result.saved + result.duplicated + result.rejected !== batch.length) throw new Error("Invalid activity acknowledgement");
        queue.splice(0, batch.length); failures = 0;
      } catch (error) {
        if (epoch !== generation || abort.signal.aborted) return;
        const status = (error as { status?: number })?.status;
        if (status === 401 || status === 403) { stop(); return; }
        if (status && status >= 400 && status < 500 && status !== 429) queue.splice(0, batch.length);
        failures++;
      } finally {
        if (epoch === generation) {
          sending = null; controller = null;
          scheduleFlush(failures ? Math.min(60000, 3000 * 2 ** Math.min(failures, 4)) : 100);
        }
      }
    });
    return sending;
  }
  return {
    start() { enabled = true; verifiedCsrf = ownsSession(readSession(), owner) ? readSession()?.csrfToken : undefined; }, stop, flush, authorize,
    async drain() {
      while (queue.length && allowed()) {
        const before = queue.length;
        await flush();
        if (queue.length >= before) break; // Failure: leave the retry queue intact.
      }
    },
    assertOwner() { allow(); if (!allowed()) throw new Error("로그인 상태가 변경되었어요. 다시 확인해 주세요."); },
    remember(target: HomeDetailTarget) {
      if (!allowed() || !activityIdentity(target)) return;
      if ("recommendationId" in target || "recommendationReason" in target) recommendations.set(`${target.topicId}:${target.contentId}`, { recommendationId: target.recommendationId, recommendationReason: target.recommendationReason });
    },
    metadata(target: HomeDetailTarget) { return recommendations.get(`${target.topicId}:${target.contentId}`); },
    record(target: HomeDetailTarget, type: ActivityType, value: ActivityValue): boolean {
      if (!allowed()) return false;
      const identity = activityIdentity(target);
      if (!identity || queue.length >= MAX_ACTIVITY_QUEUE) return false;
      try {
        allow(); sessionId ??= uuid();
        const recommendationId = target.recommendationId ?? recommendations.get(`${target.topicId}:${target.contentId}`)?.recommendationId;
        queue.push({ ...identity, clientEventId: uuid(), eventType: type, occurredAt: new Date(now()).toISOString(),
          ...(Number.isSafeInteger(recommendationId) && (recommendationId ?? 0) > 0 ? { recommendationItemId: recommendationId } : {}),
          value: { ...value, sessionId } });
        scheduleFlush(); return true;
      } catch { return false; } // Metrics never interrupt the reading or saving flow.
    },
  };
}

/** Programmatic restoration updates position but is not a user swipe. */
export function createCarouselMeasurement(now: () => number = Date.now) {
  let interactedAt = -Infinity, lastIndex = 0;
  return {
    interact() { interactedAt = now(); },
    move(index: number, count: number) {
      if (!Number.isInteger(index) || index < 0 || index >= count || count < 1 || count > 10 || index === lastIndex) return false;
      lastIndex = index;
      return now() - interactedAt <= 6000;
    },
  };
}

/** Measures foreground exposure only; one completion yields one immutable event. */
export function createExposure(now: () => number = Date.now) {
  let since: number | null = null, total = 0, maxScroll = 0;
  return {
    setVisible(visible: boolean) {
      if (visible && since === null) since = now();
      if (!visible && since !== null) { total += Math.max(0, now() - since); since = null; }
    },
    scroll(percent: number) { if (Number.isFinite(percent)) maxScroll = Math.max(maxScroll, Math.max(0, Math.min(100, percent))); },
    finish() {
      if (since !== null) { total += Math.max(0, now() - since); since = null; }
      const result = { duration: Math.min(86400000, Math.round(total)), scrollPct: maxScroll };
      total = 0; maxScroll = 0; return result;
    },
  };
}
