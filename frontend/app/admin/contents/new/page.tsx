import { Suspense } from "react";
import { ContentEditorScreen } from "@/components/admin/content-editor-screen";

export default function AdminContentCreatePage() {
  return <Suspense fallback={<p role="status">콘텐츠를 불러오는 중입니다</p>}><ContentEditorScreen /></Suspense>;
}
