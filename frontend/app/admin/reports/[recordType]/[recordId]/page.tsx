import { Suspense } from "react";
import { SupportDetailRoute } from "@/components/admin/support-detail-screen";

export default async function SupportDetailPage({ params }: {
  params: Promise<{ recordType: string; recordId: string }>;
}) {
  const { recordType, recordId } = await params;
  return <Suspense fallback={<p role="status">신고·문의 상세를 준비 중입니다</p>}>
    <SupportDetailRoute recordType={recordType} recordId={recordId} />
  </Suspense>;
}
