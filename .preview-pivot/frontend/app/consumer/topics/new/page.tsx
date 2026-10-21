import { AddTopicScreen } from "@/components/topics/add-topic-screen";

type NewTopicPageProps = {
  searchParams: Promise<{
    returnTopicId?: string | string[];
    targetTopicId?: string | string[];
  }>;
};

export default async function NewTopicPage({ searchParams }: NewTopicPageProps) {
  const query = await searchParams;
  const returnTopicId =
    query.returnTopicId === undefined
      ? null
      : typeof query.returnTopicId === "string"
        ? query.returnTopicId
        : "";
  const targetTopicId =
    query.targetTopicId === undefined
      ? null
      : typeof query.targetTopicId === "string"
        ? query.targetTopicId
        : "";

  return (
    <AddTopicScreen
      key={JSON.stringify([returnTopicId, targetTopicId])}
      returnTopicId={returnTopicId}
      targetTopicId={targetTopicId}
    />
  );
}
