import { Suspense } from "react";
import { notFound } from "next/navigation";
import { ContentEditorScreen } from "@/components/admin/content-editor-screen";

export default async function AdminContentEditPage({ params }: { params: Promise<{ contentId: string }> }) {
  const { contentId } = await params;
  if (!/^[1-9]\d*$/.test(contentId) || !Number.isSafeInteger(Number(contentId))) notFound();
  return <Suspense fallback={<p role="status">콘텐츠를 불러오는 중입니다</p>}><ContentEditorScreen key={contentId} contentId={Number(contentId)} /></Suspense>;
}
