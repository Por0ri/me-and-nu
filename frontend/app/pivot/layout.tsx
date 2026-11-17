import type { Metadata } from "next";
import { PivotSessionProvider } from "@/components/pivot/pivot-session";
import { NewNoticeProvider } from "@/components/pivot/new-notice-provider";
import styles from "@/components/pivot/pivot.module.css";

export const metadata: Metadata = { title: "me;nu 시작하기", robots: { index: false, follow: false } };
export default function PivotLayout({ children }: { children: React.ReactNode }) {
  return <div className={styles.root}><PivotSessionProvider><NewNoticeProvider>{children}</NewNoticeProvider></PivotSessionProvider></div>;
}
