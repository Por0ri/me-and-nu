import { Suspense } from "react";
import { notFound } from "next/navigation";
import { MemberDetailRoute } from "@/components/admin/member-detail-screen";

export default async function MemberPage({ params }: { params: Promise<{ memberId: string }> }) {
  const { memberId } = await params;
  // Local UI identifier only. No lookup or implied numeric API contract.
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(memberId)) notFound();
  return <Suspense fallback={<p role="status">회원 화면을 준비 중입니다</p>}><MemberDetailRoute /></Suspense>;
}
