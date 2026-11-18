"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import type { AdminReadState } from "../../lib/admin-content";
import { emptyNoticeDraft, initialNoticeQuery, noticeUnavailable, type NoticePage } from "../../lib/admin-notices";
import { NoticesScreen } from "../../components/admin/notices-screen";
import { selectNoticeFixtures, noticeFixtureStatistics } from "./admin-notices";

export function FixtureNoticesPage() {
  const mode = useSearchParams().get("fixture");
  const [query, setQuery] = useState(initialNoticeQuery);
  const data = mode === "empty" ? { items: [], total: 0 } : selectNoticeFixtures(query);
  const state: AdminReadState<NoticePage> = mode === "loading" ? { status: "loading" } : mode === "unavailable" ? noticeUnavailable
    : mode === "login-required" || mode === "forbidden" || mode === "error" || mode === "not-found" ? { status: mode, message: "테스트 전용 조회 상태" } : { status: "ready", data };
  return <><p data-fixture-banner style={{ position: "fixed", bottom: 0, left: 0, zIndex: 10, background: "#27272a", color: "white", padding: "4px 12px", fontSize: 12 }}>테스트 전용 가상 공지 · 운영 데이터 아님</p>
    <NoticesScreen query={query} navigate={setQuery} state={state} statistics={noticeFixtureStatistics} initialDraft={{ ...emptyNoticeDraft, date: "2026-11-12", time: "10:00" }} /></>;
}
