import { SavedScreen } from "@/components/saved/saved-screen";

type SavedPageProps = {
  searchParams: Promise<{ topicId?: string | string[] }>;
};

export default async function SavedPage({ searchParams }: SavedPageProps) {
  const query = await searchParams;
  const topicId =
    typeof query.topicId === "string" && query.topicId.trim()
      ? query.topicId
      : null;

  return <SavedScreen topicId={topicId} />;
}
