"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import { getNotifications, markNotificationRead, type NotificationItem } from "@/lib/api";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { consumerRoutes } from "@/lib/consumer-routes";
import { formatRelativeTime } from "@/lib/relative-time";

const GROUPS = ["오늘", "어제", "최근 7일", "최근 30일"] as const;

function groupOf(createdAt: string, now = new Date()): (typeof GROUPS)[number] {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const time = Date.parse(createdAt);
  if (time >= startOfToday) return "오늘";
  if (time >= startOfToday - 86_400_000) return "어제";
  if (time >= startOfToday - 7 * 86_400_000) return "최근 7일";
  return "최근 30일";
}

/** 24시간 안의 알림은 "1시간 전", 그 전은 "2026. 09. 17 10:47" */
function formatNotificationTime(createdAt: string): string {
  if (Date.now() - Date.parse(createdAt) < 86_400_000) return formatRelativeTime(createdAt) ?? "";
  const d = new Date(createdAt);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}. ${pad(d.getMonth() + 1)}. ${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function NotificationsScreen({ topicId }: { topicId: string | null }) {
  const router = useRouter();
  const { flowState, isRestoring } = useConsumerFlow();
  const [items, setItems] = useState<NotificationItem[] | null>(null);
  const [hasError, setHasError] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const currentTopicId = topicId ?? flowState?.initialHomeTopicId ?? null;

  useEffect(() => {
    if (!isConsumerApiMode) {
      setItems([]);
      return;
    }
    let isCancelled = false;
    getNotifications()
      .then((result) => {
        if (!isCancelled) setItems(result.items.filter((item) => item.type === "system_notice"));
      })
      .catch(() => {
        if (!isCancelled) setHasError(true);
      });
    return () => {
      isCancelled = true;
    };
  }, []);

  async function openNotification(item: NotificationItem) {
    if (item.type !== "system_notice") return;
    setReadError(null);
    if (!item.isRead) {
      try {
        const result = await markNotificationRead(item.notificationId);
        setItems((current) =>
          current?.map((n) => (n.notificationId === item.notificationId ? { ...n, isRead: result.isRead, readAt: result.readAt } : n)) ?? null,
        );
      } catch {
        setReadError("공지의 읽음 상태를 저장하지 못했습니다. 다시 시도해 주세요.");
        return;
      }
    }
    if (item.contentId && item.topicId) {
      router.push(`${consumerRoutes.content(String(item.contentId))}?topicId=${item.topicId}`);
    }
  }

  if (isConsumerApiMode && isRestoring) return null;

  const visible = items ?? [];
  const backHref = currentTopicId
    ? { pathname: consumerRoutes.home, query: { topicId: currentTopicId } }
    : consumerRoutes.home;

  return (
    <main className="ui-version-a ui-home">
      <div className="va-shell va-tab-page">
        <header className="va-noti-header">
          <Link href={backHref} aria-label="홈으로 돌아가기" className="va-noti-back">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M15 5l-7 7 7 7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </Link>
          <h1 className="text-lg font-semibold">운영공지</h1>
        </header>

        {readError ? <p role="alert" className="va-message text-center text-sm text-red-600">{readError}</p> : null}
        {hasError ? (
          <p role="alert" className="va-message text-center text-sm text-red-600">
            알림을 불러오지 못했습니다.
          </p>
        ) : items === null ? (
          <p role="status" className="va-message va-muted text-center text-sm">불러오는 중입니다.</p>
        ) : visible.length === 0 ? (
          <p role="status" className="va-message va-muted text-center text-sm">
            새 운영공지가 없어요.
          </p>
        ) : (
          // 기간 제목은 알림이 없어도 항상 보여준다.
          GROUPS.map((group) => {
            const groupItems = visible.filter((item) => groupOf(item.createdAt) === group);
            return (
              <section key={group} aria-label={group}>
                <h2 className="va-noti-group">{group}</h2>
                {groupItems.length === 0 ? (
                  <p className="va-noti-empty">이 기간의 알림이 없어요.</p>
                ) : null}
                <ul>
                  {groupItems.map((item) => (
                    <li key={item.notificationId}>
                      <button
                        type="button"
                        onClick={() => void openNotification(item)}
                        aria-label={`${item.title} ${item.body}`}
                        className={`va-noti-item${item.isRead ? "" : " va-noti-unread"}`}
                      >
                        <span className="va-noti-text">
                          <span className="font-semibold">{item.title}</span>
                          <span className="va-noti-item-body">{item.body}</span>
                          <span className="va-noti-item-time">{formatNotificationTime(item.createdAt)}</span>
                        </span>
                        {item.imageUrl ? (
                          <span className="va-noti-thumb">
                            <Image src={item.imageUrl} alt="" fill sizes="56px" unoptimized className="object-cover" />
                          </span>
                        ) : null}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })
        )}
      </div>
    </main>
  );
}
