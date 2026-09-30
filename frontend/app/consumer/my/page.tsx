import { MyPageScreen } from "@/components/my/my-page-screen";

type MyPageProps = {
  searchParams: Promise<{ topicId?: string | string[] }>;
};

export default async function MyPage({ searchParams }: MyPageProps) {
  const query = await searchParams;
  const topicId =
    typeof query.topicId === "string" && query.topicId.trim() ? query.topicId : null;

  return <MyPageScreen topicId={topicId} />;
}
