import type { NotificationInboxSource } from "./notification-inbox";

/** One in-memory bell snapshot per session. Never shared between users or persisted. */
export function createNewNoticeStore(inbox: NotificationInboxSource, now = Date.now) {
  let value = false;
  let checkedAt: number | null = null;
  let active = false;
  let controller: AbortController | null = null;
  let pending: Promise<void> | null = null;
  const listeners = new Set<() => void>();
  function publish(next: boolean) {
    if (value === next) return;
    value = next;
    listeners.forEach(listener => listener());
  }
  function refresh(force = false): Promise<void> {
    if (!active) return Promise.resolve();
    const time = now();
    const sameDay = checkedAt !== null && new Date(checkedAt).toDateString() === new Date(time).toDateString();
    if (!sameDay) publish(false);
    if (!force && pending) return pending;
    if (!force && sameDay && time - checkedAt! >= 0 && time - checkedAt! < 60_000) return Promise.resolve();
    controller?.abort();
    const request = new AbortController();
    controller = request;
    pending = inbox.hasNewToday(request.signal).then(next => {
      if (active && !request.signal.aborted && controller === request) {
        checkedAt = now();
        publish(next);
      }
    }, () => {
      // Even a forced read within the cache window must retry on the next entry/focus.
      if (active && !request.signal.aborted && controller === request) checkedAt = null;
    }).finally(() => {
      if (controller === request) { controller = null; pending = null; }
    });
    return pending;
  }
  const source: NotificationInboxSource = {
    ...inbox,
    async markRead(id, signal) {
      const readAt = await inbox.markRead(id, signal);
      // Recheck all unread notices; reading one must not hide another new notice.
      void refresh(true);
      return readAt;
    },
  };
  return {
    source, refresh,
    getSnapshot: () => value,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    start() { active = true; void refresh(); },
    stop() { active = false; controller?.abort(); controller = null; pending = null; checkedAt = null; publish(false); },
  };
}
