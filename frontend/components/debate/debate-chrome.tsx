"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
// 새 알림 여부는 pivot 공통 틀(NewNoticeProvider)이 채우고, 홈 머리 부분과 같은 값을 쓴다.
import { NewNoticeContext } from "@/components/pivot/home-screen";
import { DEBATE_DATA_SOURCE } from "@/lib/debate/client";
import { ChevronLeftIcon, InfoIcon } from "./debate-icons";
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

/** 토론방 첫 화면 머리 부분 (2026-11-23 시안): 홈과 같은 로고·문구, 오른쪽에 이용 안내(ⓘ)와 알림 */
export function DebateHomeHeader({ onGuide }: { onGuide: () => void }) {
  // 홈 머리 부분과 같은 기준으로, 새 알림이 있을 때만 점을 보여준다.
  const hasNewNotice = useContext(NewNoticeContext);
  return <header className={styles.homeHeader}>
    <p className={styles.brandLine}>
      <img src="/pivot/home/chrome/logo.svg" alt="me;nu" width={103} height={20.5862} />
      <span>Little discoveries<br />for a more you</span>
    </p>
    <h1 className={styles.srOnly}>토론방</h1>
    <div className={styles.headerActions}>
      <button type="button" className={styles.iconButton} aria-label="토론방 이용 안내" onClick={onGuide}><InfoIcon size={24} /></button>
      <Link className={styles.iconButton} href="/pivot/home?view=notifications" aria-label="알림"
        aria-description={hasNewNotice ? "오늘 온 새 알림이 있어요" : undefined}>
        <img src="/pivot/home/chrome/notification.svg" alt="" width={24} height={24} />
        {hasNewNotice ? <span className={styles.bellDot} aria-hidden="true" /> : null}
      </Link>
    </div>
  </header>;
}

/** 안쪽 화면(토론방·주제 올리기·내 활동)의 머리 부분. back이 있으면 뒤로가기, brand면 me;nu 로고와 구분선을 함께 보여준다. */
export function DebateHeader({ title, back, brand = true, extra }: {
  title: string; back?: string | (() => void); brand?: boolean; extra?: ReactNode;
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
 * 토론방 화면의 하단바 (2026-11-23 시안: HOME·SAVE·TALK·MY). 홈 하단바는 아직 예전 모양이라
 * 홈 쪽을 바꿀 때 공용 하단바로 합치고 이 부품은 지운다.
 */
function DebateBottomNav() {
  const items = [
    { href: "/pivot/home", label: "HOME" },
    { href: "/pivot/home?view=saved", label: "SAVE" },
    { href: "/pivot/debate", label: "TALK", current: true },
    { href: "/pivot/home?view=my", label: "MY" },
  ];
  return <nav className={styles.bottomNav} aria-label="주요 메뉴">
    {items.map((item) => <Link key={item.label} href={item.href} className={styles.navItem} aria-current={item.current ? "page" : undefined}>
      <span>{item.label}</span>
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
