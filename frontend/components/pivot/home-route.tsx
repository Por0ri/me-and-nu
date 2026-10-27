"use client";

import { pendingHomeSource } from "@/lib/pivot/home-service";
import { pendingDetailSource } from "@/lib/pivot/detail-service";
import { isActiveSession } from "@/lib/pivot/services";
import { Suspense } from "react";
import { usePivotSession } from "./pivot-session";
import { HomeDetailExperience } from "./home-detail-experience";

export function HomeRoute() {
  const { state, refresh } = usePivotSession();
  if (state.status !== "ready" || !isActiveSession(state.session)) {
    const message = state.status === "loading" ? "로그인 상태를 확인하고 있어요."
      : state.status === "disconnected" ? "이 주소에는 콘텐츠 서버가 아직 연결되지 않았어요."
      : state.status === "error" ? state.message : "로그인 화면으로 이동하고 있어요.";
    return <main><p role={state.status === "error" ? "alert" : "status"}>{message}</p>
      {state.status === "error" && <button type="button" onClick={refresh}>다시 확인</button>}
    </main>;
  }
  return <Suspense fallback={<p role="status">콘텐츠를 불러오고 있어요.</p>}><HomeDetailExperience key={state.session?.user?.id} homeSource={pendingHomeSource} detailSource={pendingDetailSource} /></Suspense>;
}
