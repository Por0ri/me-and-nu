import { notFound } from "next/navigation";
import ContentBrowser from "@/components/ContentBrowser";

type Props = { params: Promise<{ topicId: string }> };

export default async function TopicPage({ params }: Props) {
  const { topicId: rawTopicId } = await params;
  const topicId = Number(rawTopicId);
  if (!/^\d+$/.test(rawTopicId) || !Number.isSafeInteger(topicId) || topicId <= 0) notFound();

  return <main className="mx-auto min-h-screen w-full max-w-6xl px-5 py-10 sm:px-8 lg:py-16"><ContentBrowser fixedTopicId={topicId} /></main>;
}
