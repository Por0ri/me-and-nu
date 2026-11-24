import Link from "next/link";
import type { AdminReadState } from "@/lib/admin-content";
import styles from "./admin.module.css";

export function AdminStatus({ state, retry, subject = "콘텐츠" }: { state: Exclude<AdminReadState<unknown>, { status: "ready" }>; retry?: () => void; subject?: "콘텐츠" | "회원" | "신고·문의" | "알림·공지" | "통계" | "대시보드" | "추천 성능" }) {
  const statistics = ["통계", "대시보드", "추천 성능"].includes(subject);
  const object = statistics ? `${subject} 데이터를` : subject === "회원" ? "회원 정보를" : subject === "신고·문의" ? "신고·문의를" : subject === "알림·공지" ? "알림·공지를" : "콘텐츠를";
  const message = statistics ? ({ forbidden: "관리자 조회 권한을 확인해 주세요.", unavailable: "통계 조회 연결을 준비하고 있습니다.", error: "통계를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요." } as Record<string, string>)[state.status] ?? (state.status === "loading" ? "" : state.message) : state.status === "loading" ? "" : state.message;
  const titles = { "login-required": "로그인이 필요합니다", forbidden: "접근 권한이 없습니다", unavailable: "조회 연결 준비 중",
    "not-found": subject === "회원" ? "회원을 찾을 수 없습니다" : subject === "신고·문의" ? "신고·문의를 찾을 수 없습니다" : subject === "알림·공지" ? "알림·공지를 찾을 수 없습니다" : "콘텐츠를 찾을 수 없습니다", error: "목록을 불러오지 못했습니다" };
  return <div className={styles.feedback} data-admin-state={state.status} role={state.status === "error" ? "alert" : "status"}>
    {state.status === "loading" ? <><div className={styles.skeleton} /><h2>{object} 불러오는 중입니다</h2><p>잠시만 기다려 주세요.</p></>
      : <><h2>{titles[state.status]}</h2><p>{message}</p><div className={styles.feedbackActions}>
        {state.status === "login-required" && <Link className={styles.primaryButton} href="/pivot/login">로그인</Link>}
        {retry && state.status !== "not-found" && <button className={styles.secondaryButton} onClick={retry} type="button">{state.status === "error" ? "다시 시도" : "다시 확인"}</button>}
      </div></>}
  </div>;
}
