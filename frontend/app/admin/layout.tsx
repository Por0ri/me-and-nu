import Link from "next/link";
import type { Metadata } from "next";
import styles from "@/components/admin/admin.module.css";
import { AdminNavigation, AdminBreadcrumb } from "@/components/admin/admin-navigation";

export const metadata: Metadata = { title: "me;nu 관리자", robots: { index: false, follow: false } };

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <div className={styles.root}>
    <a className={styles.skipLink} href="#admin-main">본문으로 이동</a>
    <aside className={styles.sidebar}>
      <div className={styles.brand}><Link href="/admin/contents" aria-label="me;nu 관리자 콘텐츠"><img src="/admin/logo.svg" alt="me;nu" /><span>ADMIN</span></Link><p>콘텐츠 큐레이션 운영 콘솔</p></div>
      <AdminNavigation />
    </aside>
    <div className={styles.workspaceBody}><header className={styles.header}><AdminBreadcrumb /><span className={styles.scopeBadge}>애니메이션만 운영 중</span></header>
      <main id="admin-main" className={styles.main}>{children}</main>
    </div>
  </div>;
}
