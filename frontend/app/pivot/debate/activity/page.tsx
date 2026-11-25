import type { Metadata } from "next";
import { DebateActivity, type DebateActivityTab } from "@/components/debate/debate-activity";

export const metadata: Metadata = { title: "me;nu 토론 내 활동" };

export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string | string[]; created?: string | string[] }> }) {
  const { tab, created } = await searchParams;
  // 탭을 정하지 않고 들어오면 '내가 올린 주제' (2026-11-25 사용자 요청: 첫 탭)
  const initialTab: DebateActivityTab = tab === "voted" ? "voted" : tab === "joined" ? "joined" : "mine";
  return <DebateActivity key={initialTab} initialTab={initialTab} created={created === "1"} />;
}
