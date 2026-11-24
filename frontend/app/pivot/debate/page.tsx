import type { Metadata } from "next";
import { DebateHome, type DebateHomeTab } from "@/components/debate/debate-home";

export const metadata: Metadata = { title: "me;nu 토론방" };

const TABS: readonly DebateHomeTab[] = ["live", "vote", "past"];

export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string | string[] }> }) {
  const { tab } = await searchParams;
  const initialTab = TABS.find((value) => value === tab) ?? "live";
  // 다른 화면에서 탭을 지정해 들어오면 그 탭으로 새로 그린다.
  return <DebateHome key={initialTab} initialTab={initialTab} />;
}
