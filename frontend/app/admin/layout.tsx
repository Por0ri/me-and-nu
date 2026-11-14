import Link from "next/link";
import type { Metadata } from "next";
import styles from "@/components/admin/admin.module.css";

export const metadata: Metadata = { title: "me;nu 관리자", robots: { index: false, follow: false } };
const navigation = [
  ["dashboard", "대시보드"], ["users", "회원 관리"], ["contents", "콘텐츠 관리"],
  ["analytics", "데이터 분석"], ["reports", "신고 관리"], ["notices", "알림 및 공지"],
] as const;

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <div className={styles.root}>
    <a className={styles.skipLink} href="#admin-main">본문으로 이동</a>
    <aside className={styles.sidebar}>
      <div className={styles.brand}><Link href="/admin/contents" aria-label="me;nu 관리자 콘텐츠"><img src="/admin/logo.svg" alt="me;nu" /><span>ADMIN</span></Link><p>콘텐츠 큐레이션 운영 콘솔</p></div>
      <nav aria-label="관리자 메뉴"><p className={styles.workspace}>WORKSPACE</p>{navigation.map(([icon, label]) => icon === "contents"
        ? <Link key={icon} className={styles.navActive} href="/admin/contents" aria-current="page"><img src={`/admin/${icon}.svg`} alt="" />{label}</Link>
        : <span key={icon} className={styles.navDisabled} aria-disabled="true"><img src={`/admin/${icon}.svg`} alt="" />{label}<small>준비 중</small></span>)}</nav>
    </aside>
    <div className={styles.workspaceBody}><header className={styles.header}><span>서비스 관리 / <strong>콘텐츠 관리</strong></span></header>
      <main id="admin-main" className={styles.main}>{children}</main>
    </div>
  </div>;
}
