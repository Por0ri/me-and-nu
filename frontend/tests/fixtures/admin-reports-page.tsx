"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import type { AdminReadState } from "../../lib/admin-content";
import { initialReportQuery, reportUnavailable, type ReportPage } from "../../lib/admin-reports";
import { ReportsScreen } from "../../components/admin/reports-screen";
import { selectReportFixtures, reportFixtureStatistics } from "./admin-reports";

export function FixtureReportsPage() {
  const params = useSearchParams(), mode = params.get("fixture");
  const [query, setQuery] = useState(initialReportQuery);
  const data = mode === "empty" ? { items: [], total: 0 } : selectReportFixtures(query);
  const state: AdminReadState<ReportPage> = mode === "loading" ? { status: "loading" } : mode === "unavailable" ? reportUnavailable
    : mode === "login-required" || mode === "forbidden" || mode === "error" || mode === "not-found" ? { status: mode, message: "테스트 전용 조회 상태" } : { status: "ready", data };
  return <><p data-fixture-banner style={{ position: "fixed", bottom: 0, left: 0, zIndex: 10, background: "#27272a", color: "white", padding: "4px 12px", fontSize: 12 }}>테스트 전용 가상 신고·문의 · 운영 데이터 아님</p>
    <ReportsScreen query={query} state={state} navigate={setQuery} statistics={reportFixtureStatistics(query.period)} /></>;
}
