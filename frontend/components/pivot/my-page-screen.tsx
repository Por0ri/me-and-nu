"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { MyPageState } from "@/lib/pivot/my-page";
import type { NotificationSettingsSource } from "@/lib/pivot/notification-settings";
import type { MyPagePolicyType } from "@/lib/pivot/policy-document";
import { MyPagePolicyDialog } from "./my-page-policy-dialog";
import { MyNotificationSettings } from "./my-notification-settings";
import { WithdrawalDialog } from "./withdrawal-dialog";
import { HomeHeader, HomeNavigation } from "./home-screen";
import homeStyles from "./home.module.css";
import styles from "./my-page.module.css";

function Chevron({ muted = false, last = false }: { muted?: boolean; last?: boolean }) {
  return <img src={`/pivot/my/chevron${muted ? "-muted" : ""}${last ? "-last" : ""}.svg`} alt="" width={16} height={24} />;
}
// The 내 정보 rows that open their own pages; Demo keeps the pending notice.
const TASTE_EDIT = "내 피드 취향 조정하기";
const ROW_PAGES: Record<string, string> = { [TASTE_EDIT]: "/pivot/taste", "관심분야 관리": "/pivot/interests" };
function MenuSection({ title, children }: { title?: string; children: ReactNode }) {
  return <section className={styles.card} aria-label={title ?? "계정 안내"}>
    {title ? <h2 className={styles.sectionTitle}>{title}</h2> : null}
    <div className={styles.rows}>{children}</div>
  </section>;
}
function DemoAccountDialog({ action, onClose }: { action: "로그아웃" | "회원탈퇴"; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    const trigger = document.activeElement as HTMLElement | null;
    element?.showModal();
    return () => { element?.close(); trigger?.focus({ preventScroll: true }); };
  }, []);
  return <dialog ref={dialog} className={styles.dialog} aria-labelledby="my-account-title" aria-describedby="my-account-description"
    onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <h2 id="my-account-title">Demo {action} 안내</h2>
    <p id="my-account-description">{action === "로그아웃"
      ? "가상 프로필로 체험 중이에요. 실제 로그인 세션이 없어 로그아웃을 수행하지 않아요."
      : "시연용 화면이라 실제 계정 삭제 API를 호출하거나 개인정보를 삭제하지 않아요."}</p>
    <div className={styles.dialogActions}>
      <button type="button" autoFocus onClick={onClose}>취소</button>
      <button type="button" onClick={onClose}>확인</button>
    </div>
  </dialog>;
}

function ServiceLogoutDialog({ busy, error, onConfirm, onClose }: {
  busy: boolean; error: string; onConfirm: () => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    const trigger = document.activeElement as HTMLElement | null;
    element?.showModal();
    return () => {
      element?.close();
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, []);
  return <dialog ref={dialog} className={`${styles.dialog} ${styles.logoutDialog}`} aria-modal="true"
    aria-labelledby="my-logout-title" aria-describedby="my-logout-question my-logout-hint" aria-busy={busy || undefined}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClose={(event) => {
      // Strict Mode can reopen the dialog before cleanup's queued close event arrives.
      if (!event.currentTarget.open) onClose();
    }}>
    <h2 id="my-logout-title">로그아웃</h2>
    <p id="my-logout-question" className={styles.logoutQuestion}>정말 로그아웃하시겠어요?</p>
    <p id="my-logout-hint" className={styles.logoutHint}>다시 로그인하면 저장한 내용은 그대로 볼 수 있어요</p>
    {error ? <p role="alert" className={styles.logoutError}>{error}</p> : null}
    <div className={styles.dialogActions}>
      <button type="button" autoFocus disabled={busy} onClick={onClose}>취소</button>
      {/* Keep keyboard focus on confirmation while the parent ref blocks duplicate requests. */}
      <button type="button" aria-disabled={busy || undefined} onClick={onConfirm}>{busy ? "로그아웃 중…" : error ? "다시 시도" : "로그아웃"}</button>
    </div>
  </dialog>;
}

export function PivotMyPageScreen({ state, demo, notificationScope, notificationSource, onNavigate, onHome, onRetry, onSaved, onActivity, onInquiry, onLogout, onWithdraw, onNotifications }: {
  state: MyPageState; demo: boolean; notificationScope?: object; notificationSource?: NotificationSettingsSource; onNavigate: (path: string) => void; onHome: () => void; onRetry: () => void; onSaved?: () => void; onActivity?: () => void; onInquiry?: () => void; onLogout?: () => Promise<void>; onWithdraw?: () => Promise<void>; onNotifications?: () => void;
}) {
  const screen = useRef<HTMLElement>(null);
  const [notice, setNotice] = useState("");
  const [policy, setPolicy] = useState<MyPagePolicyType | null>(null);
  const closingPolicy = useRef(false);
  const [accountDialog, setAccountDialog] = useState<"로그아웃" | "회원탈퇴" | null>(null);
  const [withdrawalDialog, setWithdrawalDialog] = useState(false);
  const [logoutDialog, setLogoutDialog] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  const logoutLock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { screen.current?.querySelector<HTMLHeadingElement>("h1")?.focus({ preventScroll: true }); }, []);
  useEffect(() => {
    if (!policy) return;
    const closeOnBack = () => setPolicy(null);
    window.addEventListener("popstate", closeOnBack);
    return () => window.removeEventListener("popstate", closeOnBack);
  }, [policy]);
  function openPolicy(type: MyPagePolicyType) {
    if (policy) return;
    closingPolicy.current = false;
    setNotice("");
    // A same-URL entry lets browser Back dismiss the dialog without unmounting MY.
    window.history.pushState({ ...window.history.state, pivotPolicyDialog: true }, "");
    setPolicy(type);
  }
  function closePolicy() {
    if (closingPolicy.current) return;
    closingPolicy.current = true;
    if (window.history.state?.pivotPolicyDialog) window.history.back();
    else setPolicy(null);
  }
  function pending(label: string) { setNotice(`${label} 기능은 준비 중이에요.`); }
  function confirmLogout() {
    if (demo || !onLogout || logoutLock.current) return;
    setNotice(""); setLogoutError(""); setLogoutDialog(true);
  }
  function closeLogout() {
    // Once sent, closing cannot cancel the session-ending request.
    if (logoutLock.current) return;
    setLogoutDialog(false); setLogoutError("");
  }
  async function logout() {
    if (demo || !onLogout || !logoutDialog || logoutLock.current) return;
    logoutLock.current = true; setLoggingOut(true); setLogoutError("");
    try {
      await onLogout();
      if (mounted.current) setLogoutDialog(false);
    }
    catch {
      if (mounted.current) setLogoutError("로그아웃하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      logoutLock.current = false;
      if (mounted.current) setLoggingOut(false);
    }
  }
  const profile = state.status === "ready" && state.profile.origin === (demo ? "demo" : "service") ? state.profile : null;
  return <main ref={screen} className={`${homeStyles.screen} ${styles.screen}`} data-my-state={state.status} data-my-mode={demo ? "demo" : "service"}
    aria-busy={state.status === "loading" && !logoutDialog}>
    <HomeHeader title="마이페이지" showMascot={false} onNotice={setNotice} onNotifications={onNotifications} />
    <div className={styles.content}>
      <p className={styles.intro}>내 정보와 설정을 관리해보세요</p>
      {profile ? <>
        <section className={`${styles.card} ${styles.profile}`} aria-label={demo ? "시연용 가상 프로필" : "프로필"}>
          <div><p className={styles.nickname}>{profile.nickname}님</p><p className={styles.email}>{profile.maskedEmail}</p></div>
          <button type="button" className={styles.edit} onClick={() => !demo ? onNavigate("/pivot/profile") : pending("프로필 수정")}><span>프로필 수정<Chevron /></span></button>
        </section>
        <MenuSection title="내 정보">
          {["내 활동", TASTE_EDIT, "관심분야 관리"].map((label, index) => <button key={label} type="button" className={styles.row}
            onClick={() => label === "내 활동" && onActivity ? onActivity() : ROW_PAGES[label] && !demo ? onNavigate(ROW_PAGES[label]) : pending(label)}>{label}<Chevron last={index === 2} /></button>)}
        </MenuSection>
        <MyNotificationSettings demo={demo} scope={notificationScope} source={notificationSource} />
        <MenuSection title="이용 안내">
          {["문의 및 신고", "이용약관", "개인정보처리방침"].map((label, index) => <button key={label} type="button" className={styles.row}
            aria-haspopup={index ? "dialog" : undefined}
            onClick={() => index === 1 ? openPolicy("terms") : index === 2 ? openPolicy("privacy") : onInquiry ? onInquiry() : pending(label)}>{label}<Chevron last={index === 2} /></button>)}
        </MenuSection>
        <MenuSection>
          {(["로그아웃", "회원탈퇴"] as const).map((label, index) => <button key={label} type="button" className={`${styles.row} ${styles.account}`}
            disabled={label === "로그아웃" && loggingOut} aria-busy={label === "로그아웃" && loggingOut || undefined}
            aria-haspopup={demo || (index === 0 ? onLogout : onWithdraw) ? "dialog" : undefined}
            onClick={() => {
              if (demo) setAccountDialog(label);
              else if (label === "로그아웃" && onLogout) confirmLogout();
              else if (label === "회원탈퇴" && onWithdraw) { setNotice(""); setWithdrawalDialog(true); }
              else pending(label);
            }}>{label}<Chevron muted last={index === 1} /></button>)}
        </MenuSection>
        {demo ? <p id="my-demo-help" className={styles.help}>시연용 가상 프로필이에요. 알림 선택은 이 화면에서만 적용되며, 화면을 떠나거나 새로고침하면 초기화돼요.</p> : null}
      </> : <section className={homeStyles.status} aria-label="My Page 연결 상태">
        <p role={state.status === "error" || state.status === "ready" ? "alert" : "status"}>{state.status === "loading" ? "프로필을 불러오고 있어요."
          : state.status === "ready" ? "프로필을 표시할 수 없어요." : state.message}</p>
        {state.status === "error" ? <button type="button" className={homeStyles.button} onClick={onRetry}>다시 시도</button> : null}
      </section>}
      {!profile && !demo && onLogout ? <MenuSection>
        <button type="button" className={`${styles.row} ${styles.account}`} disabled={loggingOut} aria-busy={loggingOut || undefined}
          onClick={confirmLogout}>로그아웃<Chevron muted last /></button>
      </MenuSection> : null}
    </div>
    <HomeNavigation active="my" onHome={onHome} onSaved={onSaved} hideAI onNotice={setNotice} />
    {notice ? <p role="status" className={homeStyles.pendingNotice}>{notice}</p> : null}
    {policy ? <MyPagePolicyDialog type={policy} demo={demo} onClose={closePolicy} /> : null}
    {accountDialog ? <DemoAccountDialog action={accountDialog} onClose={() => setAccountDialog(null)} /> : null}
    {logoutDialog && !demo ? <ServiceLogoutDialog busy={loggingOut} error={logoutError} onConfirm={() => void logout()} onClose={closeLogout} /> : null}
    {withdrawalDialog && !demo && onWithdraw ? <WithdrawalDialog withdraw={onWithdraw} onClose={() => setWithdrawalDialog(false)} /> : null}
  </main>;
}
