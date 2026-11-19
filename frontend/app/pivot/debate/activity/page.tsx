import type { Metadata } from "next";
import { DebateActivity, type DebateActivityTab } from "@/components/debate/debate-activity";

export const metadata: Metadata = { title: "me;nu 토론 내 활동" };

export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string | string[]; created?: string | string[] }> }) {
  const { tab, created } = await searchParams;
  const initialTab: DebateActivityTab = tab === "mine" ? "mine" : "voted";
  return <DebateActivity key={initialTab} initialTab={initialTab} created={created === "1"} />;
}
