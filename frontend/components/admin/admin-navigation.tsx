"use client";

import Link from "next/link";
import { Suspense } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import styles from "./admin.module.css";

const navigation = [
  ["dashboard", "대시보드"], ["users", "회원 관리"], ["contents", "콘텐츠 관리"],
  ["analytics", "데이터 분석"], ["reports", "신고 및 문의 관리"], ["notices", "알림 및 공지"],
] as const;

export function AdminNavigation() {
  const pathname = usePathname();
  return <nav aria-label="관리자 메뉴"><p className={styles.workspace}>WORKSPACE</p>{navigation.map(([icon, label]) => {
    const href = `/admin/${icon}`;
    const active = pathname === href || pathname.startsWith(`${href}/`);
    const asset = icon === "contents" ? active ? "contents" : "contents-inactive" : active ? `${icon}-active` : icon;
    return <Link key={icon} className={active ? styles.navActive : styles.navLink} href={href} aria-current={active ? "page" : undefined}>
      <img src={`/admin/${asset}.svg`} alt="" />{label}</Link>;
  })}</nav>;
}

export function AdminBreadcrumb() {
  const pathname = usePathname();
  const members = pathname === "/admin/users" || pathname.startsWith("/admin/users/");
  const reports = pathname === "/admin/reports" || pathname.startsWith("/admin/reports/");
  const notices = pathname === "/admin/notices" || pathname.startsWith("/admin/notices/");
  const analytics = pathname === "/admin/analytics";
  return <span>서비스 관리 / <strong>{analytics ? "데이터 분석" : pathname === "/admin/dashboard" ? "대시보드" : notices ? "알림 및 공지" : reports ? "신고 및 문의 관리" : members ? "회원 관리" : "콘텐츠 관리"}</strong>{members && pathname !== "/admin/users" && <> / <strong>회원 상세</strong></>}{analytics && <Suspense><AnalyticsBreadcrumb /></Suspense>}</span>;
}

function AnalyticsBreadcrumb() {
  const params = useSearchParams();
  return params.get("view") === "performance" ? <> / <strong>추천 성능</strong></> : null;
}
