import { HomeScreen } from "@/components/home/home-screen";

type HomePageProps = {
  searchParams: Promise<{ topicId?: string | string[] }>;
};

export default async function Home({ searchParams }: HomePageProps) {
  const query = await searchParams;
  // null은 query 없음이다. 빈 값 / 중복 query는 invalid로 구분해 fallback하지 않는다.
  const topicId =
    query.topicId === undefined
      ? null
      : typeof query.topicId === "string"
        ? query.topicId
        : "";

  return <HomeScreen key={JSON.stringify(topicId)} topicId={topicId} />;
}
