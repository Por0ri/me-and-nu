import { Suspense } from "react";
import { DashboardScreen } from "@/components/admin/dashboard-screen";

export default function AdminDashboardPage() { return <Suspense fallback={<p role="status">대시보드를 불러오는 중입니다.</p>}><DashboardScreen /></Suspense>; }
