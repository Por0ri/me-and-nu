import { Suspense } from "react";
import { AnalyticsRoute } from "@/components/admin/analytics-screen";

export default function AdminAnalyticsPage() {
  return <Suspense fallback={<p role="status">데이터 분석 화면을 불러오는 중입니다.</p>}><AnalyticsRoute /></Suspense>;
}
