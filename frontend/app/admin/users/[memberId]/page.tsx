import { Suspense } from "react";
import { notFound } from "next/navigation";
import { MemberDetailRoute } from "@/components/admin/member-detail-screen";

export default async function MemberPage({ params }: { params: Promise<{ memberId: string }> }) {
  const { memberId } = await params;
  if (!/^[1-9]\d{0,18}$/.test(memberId) || BigInt(memberId) > BigInt("9223372036854775807")) notFound();
  return <Suspense fallback={<p role="status">회원 화면을 준비 중입니다</p>}><MemberDetailRoute memberId={memberId} /></Suspense>;
}
