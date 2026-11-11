import { getNotifications, getSession, getUnreadNotificationCount, markNotificationRead, type NotificationItem, type NotificationsResponse, type Session } from "@/lib/api";
import type { HomeDetailTarget } from "./home";
import { isActiveSession, requirePivotApi } from "./services";

export type InboxNotification = NotificationItem & { preview?: string };
export type InboxPage = Omit<NotificationsResponse, "items"> & { items: InboxNotification[] };

function positiveId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

/** New-post and recommended notices are about one post and open it. */
export function isPostNotice(item: NotificationItem): boolean {
  return item.type === "topic_new_post" || item.type === "recommended_post";
}

/** The inbox shows operational notices and post notices that point to a post. */
export function isInboxItem(item: NotificationItem): boolean {
  if (item.type === "system_notice") return true;
  return isPostNotice(item) && positiveId(item.contentId) && positiveId(item.topicId);
}

/** A post notice opens its post; service posts use the content ID as the post ID. */
export function newPostTarget(item: NotificationItem): HomeDetailTarget | null {
  if (!isPostNotice(item) || !positiveId(item.contentId) || !positiveId(item.topicId)) return null;
  return { postId: String(item.contentId), contentId: String(item.contentId), topicId: item.topicId };
}

export type NotificationInboxSource = {
  kind: "service" | "demo";
  list(options: { unreadOnly?: boolean; cursor?: string; signal: AbortSignal }): Promise<InboxPage>;
  count(signal: AbortSignal): Promise<number>;
  markRead(id: number, signal: AbortSignal): Promise<string>;
};

export function createServiceNotificationInbox(
  readSession: () => Session | null,
  refresh: () => void,
  api = { getSession, getNotifications, getUnreadNotificationCount, markNotificationRead },
  guard = requirePivotApi,
): NotificationInboxSource {
  const owner = readSession();
  function check(signal: AbortSignal) {
    signal.throwIfAborted();
    guard();
    if (owner !== readSession() || !isActiveSession(owner) || !owner?.csrfToken) {
      throw new Error("Notification session changed");
    }
    return owner.csrfToken;
  }
  async function verifySession(signal: AbortSignal) {
    const csrf = check(signal);
    const session = await api.getSession({ signal });
    check(signal);
    if (!isActiveSession(session) || session.user?.id !== owner?.user?.id || session.csrfToken !== csrf) {
      refresh();
      throw new Error("Notification session changed");
    }
    return csrf;
  }
  async function read<T>(signal: AbortSignal, request: () => Promise<T>) {
    await verifySession(signal);
    const result = await request();
    // A cookie change during a GET must not expose another account's notices.
    await verifySession(signal);
    return result;
  }
  return {
    kind: "service",
    async list(options) {
      const result = await read(options.signal, () => api.getNotifications(options));
      return { ...result, items: result.items.filter(isInboxItem) };
    },
    async count(signal) {
      const result = await read(signal, () => api.getUnreadNotificationCount(signal));
      if (!Number.isSafeInteger(result.unreadCount) || result.unreadCount < 0) throw new Error("Invalid unread count");
      return result.unreadCount;
    },
    async markRead(id, signal) {
      const csrf = await verifySession(signal);
      const result = await api.markNotificationRead(id, { signal, headers: { "X-CSRF-Token": csrf } });
      check(signal);
      if (result.notificationId !== id || result.isRead !== true || !result.readAt || !Number.isFinite(Date.parse(result.readAt))) {
        throw new Error("Unconfirmed notification read");
      }
      return result.readAt;
    },
  };
}

export async function findInboxNotification(source: NotificationInboxSource, id: number, signal: AbortSignal) {
  const seen = new Set<string>();
  let cursor: string | undefined;
  do {
    signal.throwIfAborted();
    const page = await source.list({ cursor, signal });
    signal.throwIfAborted();
    const item = page.items.find((notice) => notice.notificationId === id);
    if (item) return item;
    cursor = page.nextCursor ?? undefined;
    if (cursor) {
      if (seen.has(cursor)) throw new Error("Repeated notification cursor");
      seen.add(cursor);
    }
  } while (cursor);
  return null;
}

export function createDemoNotificationInbox(now = Date.now()): NotificationInboxSource {
  const fixture = [
    ["[공지] me;nu 업데이트 안내", "me;nu가 더욱 편리해졌어요! 이번 업데이트에서 추가된 기능과 개선사항을 확인해보세요.", 0],
    ["[시스템 점검] 11월 12일 새벽 2시~4시", "안녕하세요. me;nu입니다.\n\n더 안정적인 서비스 제공을 위해\n11월 12일 (목) 새벽 2시부터 4시까지 서비스 점검이 진행됩니다.\n\n점검 중에는 서비스 접속이 어렵고, 일부 기능이\n정상적으로 동작하지 않을 수 있어요.\n이용에 불편을 드려 죄송하며, 더 나은 서비스로 보답하겠습니다.", 1_000,
      "더 안정적인 서비스 제공을 위해 11월 12일 (목) 새벽 2시부터 4시까지 서비스 점검이 진행됩니다."],
    ["[공지] 서비스 이용약관 개정 안내", "me;nu가 이용약관을 개정했어요! 개정된 약관의 내용을 확인해보세요.", 7_200_000],
  ] as const;
  const items: InboxNotification[] = fixture.map(([title, body, age, preview], index) => ({
    notificationId: index + 1, type: "system_notice", title, body, preview,
    topicId: null, contentId: null, imageUrl: null,
    createdAt: new Date(now - age).toISOString(), isRead: index !== 0,
    readAt: index === 0 ? null : new Date(now).toISOString(),
  }));
  return {
    kind: "demo",
    async list({ unreadOnly, signal }) {
      signal.throwIfAborted();
      return { items: items.filter((item) => !unreadOnly || !item.isRead), nextCursor: null };
    },
    async count(signal) {
      signal.throwIfAborted();
      return items.filter((item) => !item.isRead).length;
    },
    async markRead(id, signal) {
      signal.throwIfAborted();
      const index = items.findIndex((item) => item.notificationId === id);
      if (index < 0) throw new Error("Unknown demo notification");
      const readAt = items[index].readAt ?? new Date().toISOString();
      items[index] = { ...items[index], isRead: true, readAt };
      return readAt;
    },
  };
}
