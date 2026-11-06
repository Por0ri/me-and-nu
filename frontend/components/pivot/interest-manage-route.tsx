"use client";
// My Page 관심분야 관리 (2026-11 design): the onboarding 관심 분야 screen showing the member's field.
import Link from "next/link";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, type Session } from "@/lib/api";
import { loadManagedInterest, type ManagedInterest } from "@/lib/pivot/interest-manage";
import { isActiveSession } from "@/lib/pivot/services";
import { usePivotSession } from "./pivot-session";
import { InterestScreen } from "./onboarding-screens";
import { PivotBrand, PivotFrame } from "./pivot-ui";
import pivotStyles from "./pivot.module.css";

const MY_PAGE = "/pivot/home?view=my";
type InterestState = ManagedInterest | { status: "error" };

export function InterestManageRoute() {
  const router = useRouter();
  const { state: session, refresh } = usePivotSession();
  const owner = session.status === "ready" && isActiveSession(session.session) ? session.session : null;
  const currentSession = useRef(owner);
  const currentRequest = useRef<AbortController | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ owner: Session; attempt: number; state: InterestState }>();
  // Abort at the session-change commit, before a late 401 can clear a newer session.
  useLayoutEffect(() => {
    currentSession.current = owner;
    return () => { currentRequest.current?.abort(); };
  }, [owner]);
  useEffect(() => {
    if (!owner) return;
    const request = new AbortController();
    currentRequest.current = request;
    const current = () => !request.signal.aborted && currentSession.current === owner;
    void loadManagedInterest(() => currentSession.current, request.signal).then((state) => {
      if (current()) setResult({ owner, attempt, state });
    }).catch((reason) => {
      if (!current()) return;
      if (reason instanceof ApiError && reason.status === 401) { router.replace("/pivot/login"); return; }
      setResult({ owner, attempt, state: { status: "error" } });
    });
    return () => request.abort();
  }, [owner, attempt, router]);
  useEffect(() => {
    if (session.status === "ready" && !session.session?.authenticated) router.replace("/pivot/login");
  }, [session.status, session.session, router]);
  // Hide previous results immediately, including during a same-user session refresh.
  const state = owner && result?.owner === owner && result.attempt === attempt ? result.state : null;
  if (state?.status === "ready" || state?.status === "empty") {
    return <InterestScreen selected={state.status === "ready"} onToggle={() => {}} onNext={() => {}} busy={false}
      manage={{ onBack: () => router.replace(MY_PAGE) }} />;
  }
  const loading = session.status === "loading" || (!!owner && !state);
  const failed = state?.status === "error" || session.status === "error";
  return <PivotFrame>
    <PivotBrand screen="consent" className={pivotStyles.consentBrand} />
    <section className={pivotStyles.gate} aria-busy={loading}><h1>관심분야 관리</h1>
      <p role={failed ? "alert" : "status"}>{loading ? "관심 분야를 불러오고 있어요."
        : failed ? "관심 분야를 불러오지 못했어요. 잠시 후 다시 시도해주세요."
        : "로그인한 뒤 관심 분야를 볼 수 있어요."}</p>
      {failed ? <button type="button" className={pivotStyles.textButton}
        onClick={() => owner ? setAttempt((value) => value + 1) : refresh()}>다시 시도</button> : null}
      <Link href={MY_PAGE}>마이페이지로 돌아가기</Link>
    </section>
  </PivotFrame>;
}
