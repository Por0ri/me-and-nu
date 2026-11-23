"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
// 새 알림 여부는 pivot 공통 틀(NewNoticeProvider)이 채우고, 홈 머리 부분과 같은 값을 쓴다. 하단바도 홈과 같은 부품이다.
import { HomeNavigation, NewNoticeContext } from "@/components/pivot/home-screen";
import { DEBATE_DATA_SOURCE } from "@/lib/debate/client";
import { CheckCircleIcon, ChevronLeftIcon, InfoIcon } from "./debate-icons";
import homeStyles from "@/components/pivot/home.module.css";
import styles from "./debate.module.css";

/** 토론방 화면의 바깥 틀. 하단바·입력창이 있으면 그만큼 아래 여백을 둔다.
 *  떠 있는 동안 브라우저 스크롤바를 숨겨서(홈과 같음) 내용 길이에 따라 화면이 좌우로 밀리지 않게 한다. */
export function DebateScreen({ children, nav = false, composer = false, label, searchOpen = false }: {
  children: ReactNode; nav?: boolean; composer?: boolean; label: string;
  /** 검색창을 눌러 뒤가 흐려진 동안: 머리 부분·탭·검색창은 흐림 위에 둔다 */
  searchOpen?: boolean;
}) {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add(styles.hideScrollbar);
    return () => root.classList.remove(styles.hideScrollbar);
  }, []);
  return <main className={styles.screen} aria-label={label} data-nav={nav || undefined} data-composer={composer || undefined} data-search-open={searchOpen || undefined}>
    {DEBATE_DATA_SOURCE === "mock" ? <p className={styles.mockBanner} role="note">가짜 데이터로 보여주는 화면이에요 · 새로고침하면 처음 상태로 돌아가요</p> : null}
    {children}
    {nav ? <DebateBottomNav /> : null}
  </main>;
}

/** 토론방 첫 화면·내 활동의 머리 부분 (2026-11-23 시안). 홈 머리 부분 스타일(home.module.css)을 그대로 써서
 *  홈과 토론방을 오가도 로고·알림이 같은 자리·모양이다. 오른쪽에 이용 안내(ⓘ)와 알림,
 *  back 이 있으면 로고 앞에 둥근 뒤로가기 (홈의 프로필 수정 화면과 같은 모양). */
export function DebateHomeHeader({ onGuide, back, title = "토론방" }: { onGuide: () => void; back?: () => void; title?: string }) {
  // 홈 머리 부분과 같은 기준으로, 새 알림이 있을 때만 점을 보여준다.
  const hasNewNotice = useContext(NewNoticeContext);
  return <div className={styles.homeHeader}>
    <header className={homeStyles.header}>
      <p className={homeStyles.brand}>
        {back ? <button type="button" className={homeStyles.headerBack} aria-label="뒤로가기" onClick={back}>
          <span><img src="/pivot/detail/back.svg" alt="" width={24} height={24} /></span>
        </button> : null}
        <img src="/pivot/home/chrome/logo.svg" alt="me;nu" width={103} height={20.5862} />
        <span>Little discoveries<br />for a more you</span>
      </p>
      <h1 className={homeStyles.srOnly}>{title}</h1>
      <div className={styles.headerActions}>
        <button type="button" className={homeStyles.notification} aria-label="토론방 이용 안내" onClick={onGuide}><InfoIcon size={24} /></button>
        <Link className={homeStyles.notification} href="/pivot/home?view=notifications" aria-label="알림"
          aria-description={hasNewNotice ? "오늘 온 새 알림이 있어요" : undefined}>
          <img src="/pivot/home/chrome/notification.svg" alt="" width={24} height={24} />
          {hasNewNotice ? <span className={homeStyles.notificationDot} aria-hidden="true" /> : null}
        </Link>
      </div>
    </header>
  </div>;
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

export function DebateTabs<T extends string>({ tabs, value, onChange, label, spread = false }: {
  tabs: readonly { id: T; label: string }[]; value: T; onChange: (id: T) => void; label: string;
  /** 탭 사이를 똑같이 벌린다 (내 활동처럼 글자가 긴 탭, 시안) */
  spread?: boolean;
}) {
  return <div className={styles.tabs} role="tablist" aria-label={label} data-spread={spread || undefined}>
    {tabs.map((tab) => <button key={tab.id} type="button" role="tab" className={styles.tab} aria-selected={tab.id === value}
      onClick={() => onChange(tab.id)}>{tab.label}</button>)}
  </div>;
}

/** 토론방 첫 화면의 하단바: 홈과 같은 공용 하단바(HOME·SAVE·TALK·MY)에서 TALK 이 지금 메뉴. AI 마스코트는 없다 (2026-11-23 시안).
 *  TALK 을 다시 누르면 맨 위로 올라간다. */
function DebateBottomNav() {
  const router = useRouter();
  return <HomeNavigation active="talk" hideAI
    onHome={() => router.push("/pivot/home")}
    onSaved={() => router.push("/pivot/home?view=saved")}
    onMy={() => router.push("/pivot/home?view=my")}
    onTalk={() => window.scrollTo({ top: 0 })} />;
}

export type Loaded<T> = { loading: boolean; error: string | null; data: T };

/** 목록 불러오기: [상태, 다시 불러오기, 화면에서 바로 고치기]. loader 는 바뀌지 않는 함수(바깥 상수나 useCallback)여야 한다. */
export function useLoad<T>(initial: T, loader: () => Promise<T>): [Loaded<T>, () => void, (update: (data: T) => T) => void] {
  const [state, setState] = useState<Loaded<T>>({ loading: true, error: null, data: initial });
  const load = useCallback(() => {
    let alive = true;
    setState((current) => ({ ...current, loading: true, error: null }));
    loader().then(
      (data) => { if (alive) setState({ loading: false, error: null, data }); },
      (error: unknown) => { if (alive) setState((current) => ({ ...current, loading: false, error: errorText(error) })); },
    );
    return () => { alive = false; };
  }, [loader]);
  useEffect(() => load(), [load]);
  const update = useCallback((change: (data: T) => T) => setState((current) => ({ ...current, data: change(current.data) })), []);
  return [state, () => { load(); }, update];
}

/** 알림 종류: done 은 끝난 일(보라 체크와 함께), info 는 안내·오류 */
export type ToastTone = "done" | "info";
export type ShowToast = (message: string, tone?: ToastTone) => void;

/** 화면 아래에 잠깐 떴다가 서서히 사라지는 알림 (2026-11-23 시안: 연보라 바탕·진회색 테두리, 완료 알림은 보라 체크).
 *  약 3초 보였다가 0.6초 동안 사라지고(.toast 애니메이션), 새 알림이 오면 처음부터 다시 보인다. */
export function useToast(): [ReactNode, ShowToast] {
  const [toast, setToast] = useState<{ message: string; tone: ToastTone; id: number } | null>(null);
  const next = useRef(0);
  const show = useCallback((message: string, tone: ToastTone = "info") => {
    next.current += 1;
    setToast({ message, tone, id: next.current });
  }, []);
  const node = toast ? <p key={toast.id} className={styles.toast} role="status" data-tone={toast.tone}
    onAnimationEnd={() => setToast((current) => current?.id === toast.id ? null : current)}>
    {toast.tone === "done" ? <CheckCircleIcon size={22} /> : null}<span>{toast.message}</span>
  </p> : null;
  return [node, show];
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
