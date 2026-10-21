import Link from "next/link";
import { notFound } from "next/navigation";
import ContentArticle from "@/components/ContentArticle";

type Props = {
  params: Promise<{ contentId: string }>;
  searchParams: Promise<{ topicId?: string | string[]; subtopicId?: string | string[] }>;
};

function positiveId(value: string | string[] | undefined): number | null {
  if (typeof value !== "string" || !/^\d+$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

export default async function ContentPage({ params, searchParams }: Props) {
  const [{ contentId: rawContentId }, query] = await Promise.all([params, searchParams]);
  const contentId = positiveId(rawContentId);
  if (contentId === null) notFound();
  const topicId = positiveId(query.topicId);
  const subtopicId = positiveId(query.subtopicId);

  if (topicId === null) return <main className="mx-auto max-w-3xl px-5 py-16 text-slate-300">
    <h1 className="text-2xl font-bold">Topic 정보가 필요합니다</h1>
    <p className="mt-3">홈 피드에서 글을 선택해 주세요.</p>
    <Link href="/" className="mt-5 inline-block text-teal-200 underline">홈으로 이동</Link>
  </main>;

  return <ContentArticle contentId={contentId} topicId={topicId} subtopicId={subtopicId ?? undefined} />;
}
