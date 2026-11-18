"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
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
    return icon === "contents" || icon === "users" || icon === "reports" || icon === "notices"
      ? <Link key={icon} className={active ? styles.navActive : styles.navLink} href={href} aria-current={active ? "page" : undefined}>
        <img src={`/admin/${icon === "users" && active ? "users-active" : icon === "reports" && active ? "reports-active" : icon === "notices" && active ? "notices-active" : icon === "contents" && !active ? "contents-inactive" : icon}.svg`} alt="" />{label}</Link>
      : <span key={icon} className={styles.navDisabled} aria-disabled="true" title={`${label} 준비 중`}><img src={`/admin/${icon}.svg`} alt="" />{label}<span className={styles.srOnly}> 준비 중</span></span>;
  })}</nav>;
}

export function AdminBreadcrumb() {
  const pathname = usePathname();
  const members = pathname === "/admin/users" || pathname.startsWith("/admin/users/");
  const reports = pathname === "/admin/reports" || pathname.startsWith("/admin/reports/");
  const notices = pathname === "/admin/notices" || pathname.startsWith("/admin/notices/");
  return <span>서비스 관리 / <strong>{notices ? "알림 및 공지" : reports ? "신고 및 문의 관리" : members ? "회원 관리" : "콘텐츠 관리"}</strong>{members && pathname !== "/admin/users" && <> / <strong>회원 상세</strong></>}</span>;
}
