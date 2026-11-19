"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { DEBATE_DATA_SOURCE } from "@/lib/debate/client";
import { BellIcon, BookmarkIcon, ChatFilledIcon, ChevronLeftIcon, HomeIcon, SearchIcon, UserIcon } from "./debate-icons";
import styles from "./debate.module.css";

/** 토론방 화면의 바깥 틀. 하단바·입력창이 있으면 그만큼 아래 여백을 둔다. */
export function DebateScreen({ children, nav = false, composer = false, label }: {
  children: ReactNode; nav?: boolean; composer?: boolean; label: string;
}) {
  return <main className={styles.screen} aria-label={label} data-composer={composer || undefined}>
    {DEBATE_DATA_SOURCE === "mock" ? <p className={styles.mockBanner} role="note">가짜 데이터로 보여주는 화면이에요 · 새로고침하면 처음 상태로 돌아가요</p> : null}
    {children}
    {nav ? <DebateBottomNav /> : null}
  </main>;
}

/** 머리 부분. back이 있으면 뒤로가기, brand면 me;nu 로고와 구분선을 함께 보여준다. */
export function DebateHeader({ title, back, brand = true, onSearch, extra }: {
  title: string; back?: string | (() => void); brand?: boolean; onSearch?: () => void; extra?: ReactNode;
}) {
  const router = useRouter();
  const goBack = () => {
    if (typeof back === "function") back();
    else if (back) router.push(back);
  };
  return <header className={styles.header}>
    {back ? <button type="button" className={styles.back} aria-label="뒤로가기" onClick={goBack}><ChevronLeftIcon size={22} /></button> : null}
    <div className={styles.headerTitle}>
      {brand ? <>
        <img className={styles.logo} src="/pivot/home/chrome/logo.svg" alt="me;nu" width={84} height={16.8} />
        <span className={styles.divider} aria-hidden="true" />
      </> : null}
      <h1>{title}</h1>
      {extra}
    </div>
    {onSearch ? <div className={styles.headerActions}>
      <button type="button" className={styles.iconButton} aria-label="토론 검색" onClick={onSearch}><SearchIcon size={22} /></button>
      <Link className={styles.iconButton} href="/pivot/home?view=notifications" aria-label="알림"><BellIcon size={21} /><span className={styles.bellDot} aria-hidden="true" /></Link>
    </div> : null}
  </header>;
}

export function DebateTabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: readonly { id: T; label: string }[]; value: T; onChange: (id: T) => void; label: string;
}) {
  return <div className={styles.tabs} role="tablist" aria-label={label}>
    {tabs.map((tab) => <button key={tab.id} type="button" role="tab" className={styles.tab} aria-selected={tab.id === value}
      onClick={() => onChange(tab.id)}>{tab.label}</button>)}
  </div>;
}

/**
 * 토론방 화면에서만 쓰는 임시 하단바 (2026-11 시안의 홈·저장·토론방·마이).
 * 공용 하단바 디자인이 확정되면 공용 하단바에 토론방 칸을 넣고 이 부품은 지운다.
 */
function DebateBottomNav() {
  const items = [
    { href: "/pivot/home", label: "홈", icon: <HomeIcon size={22} /> },
    { href: "/pivot/home?view=saved", label: "저장", icon: <BookmarkIcon size={22} /> },
    { href: "/pivot/debate", label: "토론방", icon: <ChatFilledIcon size={24} />, current: true },
    { href: "/pivot/home?view=my", label: "마이", icon: <UserIcon size={22} /> },
  ];
  return <nav className={styles.bottomNav} aria-label="하단 메뉴 (토론방 임시)">
    {items.map((item) => <Link key={item.label} href={item.href} className={styles.navItem} aria-current={item.current ? "page" : undefined}>
      {item.icon}<span>{item.label}</span>
    </Link>)}
  </nav>;
}

/** 잠깐 떴다 사라지는 안내 문구 */
export function useToast(): [ReactNode, (message: string) => void] {
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const show = useCallback((next: string) => {
    setMessage(next);
    if (timer.current !== undefined) clearTimeout(timer.current);
    timer.current = setTimeout(() => setMessage(null), 2600);
  }, []);
  useEffect(() => () => { if (timer.current !== undefined) clearTimeout(timer.current); }, []);
  return [message ? <p className={styles.toast} role="status">{message}</p> : null, show];
}

/** 불러오는 중·실패 상태 */
export function DebateState({ loading, error, onRetry }: { loading?: boolean; error?: string | null; onRetry?: () => void }) {
  if (loading) return <p className={styles.state} role="status">불러오는 중이에요.</p>;
  if (error) return <div className={styles.state} role="alert">
    <p>{error}</p>
    {onRetry ? <button type="button" className={styles.softButton} onClick={onRetry}>다시 시도</button> : null}
  </div>;
  return null;
}

export function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "잠시 후 다시 시도해 주세요.";
}
