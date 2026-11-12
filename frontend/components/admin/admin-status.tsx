import Link from "next/link";
import type { AdminReadState } from "@/lib/admin-content";
import styles from "./admin.module.css";

export function AdminStatus({ state, retry }: { state: Exclude<AdminReadState<unknown>, { status: "ready" }>; retry: () => void }) {
  const titles = { "login-required": "로그인이 필요합니다", forbidden: "접근 권한이 없습니다", unavailable: "조회 연결 준비 중",
    "not-found": "콘텐츠를 찾을 수 없습니다", error: "목록을 불러오지 못했습니다" };
  return <div className={styles.feedback} data-admin-state={state.status} role={state.status === "error" ? "alert" : "status"}>
    {state.status === "loading" ? <><div className={styles.skeleton} /><h2>콘텐츠를 불러오는 중입니다</h2><p>잠시만 기다려 주세요.</p></>
      : <><h2>{titles[state.status]}</h2><p>{state.message}</p><div className={styles.feedbackActions}>
        {state.status === "login-required" && <Link className={styles.primaryButton} href="/pivot/login">로그인</Link>}
        {state.status !== "not-found" && <button className={styles.secondaryButton} onClick={retry} type="button">{state.status === "error" ? "다시 시도" : "다시 확인"}</button>}
      </div></>}
  </div>;
}
