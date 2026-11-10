"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { findInboxNotification, type InboxNotification, type InboxPage, type NotificationInboxSource } from "@/lib/pivot/notification-inbox";
import { formatRelativeTime } from "@/lib/relative-time";
import { HomeHeader } from "./home-screen";
import styles from "./notification-inbox.module.css";

const detailTime = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function formatDetailTime(iso: string) {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return "";
  const { year, month, day, weekday, hour, minute } = Object.fromEntries(
    detailTime.formatToParts(date).map(({ type, value }) => [type, value]),
  );
  return `${year}.${month}.${day} (${weekday}) ${hour}:${minute}`;
}

type ListState = { page?: InboxPage; loading: boolean; failed: boolean };
const loadingList: ListState = { loading: true, failed: false };

export function NotificationInbox({ source, unreadOnly, id, onFilter, onOpen, onBack }: {
  source: NotificationInboxSource;
  unreadOnly: boolean;
  id: number | null;
  onFilter: (unreadOnly: boolean) => void;
  onOpen: (id: number) => void;
  onBack: () => void;
}) {
  const [list, setList] = useState<ListState>(loadingList);
  const [selected, setSelected] = useState<InboxNotification | null>(null);
  const [lookup, setLookup] = useState<{ id: number; item: InboxNotification | null; failed?: boolean } | null>(null);
  const [lookupAttempt, setLookupAttempt] = useState(0);
  const [readStates, setReadStates] = useState<Record<number, "pending" | "failed">>({});
  const [unreadCount, setUnreadCount] = useState<number | null>(null);
  const [countRevision, setCountRevision] = useState(0);
  const lifetime = useRef<AbortController | null>(null);
  const pendingReads = useRef(new Set<number>());
  const listScroll = useRef(0);
  const opener = useRef<HTMLButtonElement | null>(null);
  const screen = useRef<HTMLElement>(null);

  const load = useCallback(async (signal: AbortSignal, cursor?: string) => {
    setList((current) => ({ ...current, loading: true, failed: false }));
    try {
      const page = await source.list({ unreadOnly, cursor, signal });
      if (signal.aborted) return;
      setList((current) => ({
        page: cursor ? { ...page, items: [...(current.page?.items ?? []), ...page.items] } : page,
        loading: false, failed: false,
      }));
    } catch {
      if (!signal.aborted) setList((current) => ({ ...current, loading: false, failed: true }));
    }
  }, [source, unreadOnly]);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    source.count(signal).then(
      (count) => { if (!signal.aborted) setUnreadCount(count); },
      () => { if (!signal.aborted) setUnreadCount(null); },
    );
    return () => controller.abort();
  }, [source, countRevision]);

  const known = selected?.notificationId === id ? selected : list.page?.items.find((item) => item.notificationId === id);
  const resolved = lookup?.id === id ? lookup : null;
  const detail = known ?? resolved?.item;
  useEffect(() => {
    if (id === null || known || list.loading) return;
    const controller = new AbortController();
    findInboxNotification(source, id, controller.signal).then(
      (item) => { if (!controller.signal.aborted) setLookup({ id, item }); },
      () => { if (!controller.signal.aborted) setLookup({ id, item: null, failed: true }); },
    );
    return () => controller.abort();
  }, [id, known, list.loading, lookupAttempt, source]);

  useEffect(() => {
    window.scrollTo({ top: id === null ? listScroll.current : 0, behavior: "instant" });
    const card = opener.current;
    const target = id === null && card?.isConnected ? card : screen.current?.querySelector<HTMLElement>("h1");
    target?.focus({ preventScroll: true });
  }, [id]);

  async function markRead(item: InboxNotification) {
    const signal = lifetime.current?.signal;
    const key = item.notificationId;
    if (!signal || signal.aborted || item.isRead || pendingReads.current.has(key)) return;
    pendingReads.current.add(key);
    setReadStates((current) => ({ ...current, [key]: "pending" }));
    try {
      const readAt = await source.markRead(key, signal);
      if (signal.aborted) return;
      const update = (row: InboxNotification) => row.notificationId === key ? { ...row, isRead: true, readAt } : row;
      setSelected((current) => current && update(current));
      setList((current) => current.page ? {
        ...current, page: { ...current.page, items: current.page.items.map(update) },
      } : current);
      setReadStates((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      setUnreadCount((current) => current === null ? null : Math.max(0, current - 1));
      setCountRevision((current) => current + 1);
    } catch {
      if (!signal.aborted) setReadStates((current) => ({ ...current, [key]: "failed" }));
    } finally {
      pendingReads.current.delete(key);
    }
  }

  function open(item: InboxNotification, button: HTMLButtonElement) {
    listScroll.current = window.scrollY;
    opener.current = button;
    setSelected(item);
    onOpen(item.notificationId);
    void markRead(item);
  }

  function loadNext() {
    const signal = lifetime.current?.signal;
    if (signal && !signal.aborted && !list.loading) void load(signal, list.page?.nextCursor ?? undefined);
  }

  const visible = list.page?.items.filter((item) => !unreadOnly || !item.isRead) ?? [];
  const hasUnread = (unreadCount ?? 0) > 0 || list.page?.items.some((item) => !item.isRead);
  const readState = id === null ? undefined : readStates[id];

  return <main ref={screen} className={styles.screen} data-demo={source.kind === "demo" || undefined} aria-label={id === null ? "알림함" : "알림 상세"}>
    <HomeHeader title={id === null ? "알림함" : "알림 상세"} onBack={onBack} showMascot={false} showNotification={false} />
    <div className={styles.content} hidden={id !== null}>
      <div className={styles.filters} role="group" aria-label="알림 필터">
        <button type="button" aria-pressed={!unreadOnly} onClick={() => onFilter(false)}>전체</button>
        <button type="button" aria-pressed={unreadOnly} onClick={() => onFilter(true)}>
          읽지 않음{hasUnread && <span className={styles.dot} aria-hidden="true" />}
        </button>
      </div>
      {visible.length > 0 && <ul className={styles.cards}>
        {visible.map((item) => <li key={item.notificationId}>
          <button type="button" className={styles.card} data-unread={!item.isRead || undefined}
            aria-label={`${item.title}, ${item.isRead ? "읽음" : "읽지 않음"}`}
            onClick={(event) => open(item, event.currentTarget)}>
            <span className={styles.meta}>
              <span className={styles.tag}>운영 공지</span>
              <span className={styles.time}>
                <time dateTime={item.createdAt} title={formatDetailTime(item.createdAt)}>{formatRelativeTime(item.createdAt)}</time>
                {!item.isRead && <span className={styles.dot} aria-hidden="true" />}
              </span>
            </span>
            <span className={styles.preview}>
              <span className={styles.title}>{item.title}</span>
              <span className={styles.summary}>{item.preview ?? item.body.replace(/\s+/g, " ").trim()}</span>
              <img src="/pivot/notifications/chevron.svg" width={24} height={24} alt="" />
            </span>
          </button>
        </li>)}
      </ul>}
      {list.loading ? <p role="status">알림을 불러오고 있어요.</p>
        : list.failed ? <div className={styles.feedback}>
          <p role="alert">알림을 불러오지 못했어요.</p>
          <button type="button" onClick={loadNext}>다시 시도</button>
        </div> : list.page?.nextCursor ? <button type="button" className={styles.more} onClick={loadNext}>알림 더 보기</button>
          : visible.length === 0 && <p role="status">{unreadOnly ? "읽지 않은 알림이 없어요." : "아직 받은 알림이 없어요."}</p>}
    </div>
    {id !== null && <div className={styles.content}>
      {detail ? <article aria-labelledby="notification-title" className={styles.detail}>
        <div className={styles.detailHeading}>
          <h2 id="notification-title">{detail.title}</h2>
          <time dateTime={detail.createdAt}>{formatDetailTime(detail.createdAt)}</time>
        </div>
        <p className={styles.body}>{detail.body}</p>
        {readState === "pending" && <p role="status">읽음 상태를 저장하고 있어요.</p>}
        {readState === "failed" && <div className={styles.feedback}>
          <p role="alert">읽음 상태를 저장하지 못했어요.</p>
          <button type="button" onClick={() => void markRead(detail)}>읽음 처리 다시 시도</button>
        </div>}
      </article> : resolved?.failed ? <div className={styles.feedback}>
        <p role="alert">알림을 불러오지 못했어요.</p>
        <button type="button" onClick={() => { setLookup(null); setLookupAttempt((current) => current + 1); }}>다시 시도</button>
      </div> : resolved ? <p role="status">알림을 찾을 수 없어요.</p> : <p role="status">알림을 불러오고 있어요.</p>}
    </div>}
  </main>;
}
