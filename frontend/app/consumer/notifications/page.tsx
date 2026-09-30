import { NotificationsScreen } from "@/components/notifications/notifications-screen";

type NotificationsPageProps = {
  searchParams: Promise<{ topicId?: string | string[] }>;
};

export default async function NotificationsPage({ searchParams }: NotificationsPageProps) {
  const query = await searchParams;
  const topicId =
    typeof query.topicId === "string" && query.topicId.trim() ? query.topicId : null;

  return <NotificationsScreen topicId={topicId} />;
}
