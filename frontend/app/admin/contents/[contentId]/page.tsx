import { Suspense } from "react";
import { notFound } from "next/navigation";
import { ContentDetailScreen } from "@/components/admin/content-detail-screen";

export default async function AdminContentPage({ params }: { params: Promise<{ contentId: string }> }) {
  const { contentId } = await params;
  if (!/^[1-9]\d*$/.test(contentId) || !Number.isSafeInteger(Number(contentId))) notFound();
  return <Suspense fallback={<p role="status">콘텐츠를 불러오는 중입니다</p>}><ContentDetailScreen contentId={Number(contentId)} /></Suspense>;
}
