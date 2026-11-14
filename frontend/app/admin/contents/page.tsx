import { Suspense } from "react";
import { ContentsRoute } from "@/components/admin/contents-screen";

export default function AdminContentsPage() {
  return <Suspense fallback={<p role="status">콘텐츠를 불러오는 중입니다</p>}><ContentsRoute /></Suspense>;
}
