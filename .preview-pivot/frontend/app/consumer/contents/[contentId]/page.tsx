import { ContentDetailScreen } from "@/components/content/content-detail-screen";
import { normalizeContentReadingOrigin } from "@/lib/continue-reading";

type ContentDetailPageProps = {
  params: Promise<{ contentId: string }>;
  searchParams: Promise<{ topicId?: string | string[]; from?: string | string[] }>;
};

export default async function ContentDetailPage({
  params,
  searchParams,
}: ContentDetailPageProps) {
  const [{ contentId }, query] = await Promise.all([params, searchParams]);
  const topicId =
    typeof query.topicId === "string" && query.topicId.trim()
      ? query.topicId
      : null;

  return <ContentDetailScreen contentId={contentId} topicId={topicId} readingOrigin={normalizeContentReadingOrigin(query.from)} />;
}
