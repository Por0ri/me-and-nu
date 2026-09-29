import { MyTopicsScreen } from "@/components/my/my-topics-screen";

type PageProps = {
  searchParams: Promise<{ topicId?: string | string[] }>;
};

export default async function Page({ searchParams }: PageProps) {
  const query = await searchParams;
  const topicId =
    typeof query.topicId === "string" && query.topicId.trim() ? query.topicId : null;

  return <MyTopicsScreen topicId={topicId} />;
}
