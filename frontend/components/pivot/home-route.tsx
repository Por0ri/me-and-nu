"use client";

import { createServiceHomeSource, pendingHomeSource } from "@/lib/pivot/home-service";
import { pendingDetailSource } from "@/lib/pivot/detail-service";
import { Suspense, useEffect, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { parsePivotSession } from "@/lib/pivot/session";
import { HomeDetailExperience } from "./home-detail-experience";
import { usePivotSession } from "./pivot-session";
import { PivotBrand, PivotFrame } from "./pivot-ui";
import styles from "./pivot.module.css";

export function HomeRoute() {
  const { state, refresh } = usePivotSession();
  const router = useRouter();
  const session = state.status === "ready" ? parsePivotSession(state.session) : null;
  const homeSource = useMemo(() => createServiceHomeSource(() => state.status === "ready" ? state.session : null), [state.status, state.session]);
  const destination = state.status !== "ready" ? null
    : !session || session.sessionState === "anonymous" ? "/pivot/login"
    : session.sessionState === "onboarding_pending" ? "/pivot/consent" : null;
  useEffect(() => { if (destination) router.replace(destination); }, [destination, router]);
  if (session?.sessionState === "active" || state.status === "disconnected") return <Suspense fallback={<p role="status">콘텐츠를 불러오고 있어요.</p>}>
    <HomeDetailExperience homeSource={session?.sessionState === "active" ? homeSource : pendingHomeSource} detailSource={pendingDetailSource} />
  </Suspense>;
  const message = state.status === "loading" ? "로그인 상태를 확인하고 있어요."
    : state.status === "error" ? "로그인 상태를 확인하지 못했어요. 다시 시도해 주세요."
    : session?.sessionState === "restoration_pending" ? "계정 복원 확인이 필요해요. 계정 복원 절차를 먼저 진행해 주세요."
    : destination === "/pivot/consent" ? "가입을 마치려면 약관과 온보딩을 확인해 주세요."
    : state.message || "로그인이 필요해요. 로그인 화면으로 이동하고 있어요.";
  return <PivotFrame><PivotBrand screen="login" />
    <section className={styles.gate}><h1>로그인 확인</h1><p role="status">{message}</p>
      {state.status === "error" && <button type="button" className={styles.textButton} onClick={refresh}>로그인 상태 다시 확인</button>}
      {state.status !== "loading" && <Link href={destination ?? "/pivot/login"}>{destination === "/pivot/consent" ? "약관 확인하기" : "로그인 화면으로"}</Link>}
    </section>
  </PivotFrame>;
}
