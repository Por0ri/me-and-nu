import { notFound } from "next/navigation";
import ContentBrowser from "@/components/ContentBrowser";

type Props = { params: Promise<{ topicId: string; subtopicId: string }> };

export default async function SubtopicPage({ params }: Props) {
  const { topicId: rawTopicId, subtopicId: rawSubtopicId } = await params;
  const topicId = Number(rawTopicId);
  const subtopicId = Number(rawSubtopicId);
  if (!/^\d+$/.test(rawTopicId) || !/^\d+$/.test(rawSubtopicId) || !Number.isSafeInteger(topicId) || !Number.isSafeInteger(subtopicId) || topicId <= 0 || subtopicId <= 0) notFound();

  return <main className="mx-auto min-h-screen w-full max-w-6xl px-5 py-10 sm:px-8 lg:py-16"><ContentBrowser fixedTopicId={topicId} subtopicId={subtopicId} /></main>;
}
