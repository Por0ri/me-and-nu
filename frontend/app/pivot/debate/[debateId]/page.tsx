import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DebateRoom } from "@/components/debate/debate-room";

export const metadata: Metadata = { title: "me;nu 토론방" };

export default async function Page({ params }: { params: Promise<{ debateId: string }> }) {
  const { debateId } = await params;
  const id = /^\d+$/.test(debateId) ? Number(debateId) : NaN;
  if (!Number.isSafeInteger(id) || id <= 0) notFound();
  return <DebateRoom key={id} debateId={id} />;
}
