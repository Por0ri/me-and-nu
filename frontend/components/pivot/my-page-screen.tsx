"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { MyPageState } from "@/lib/pivot/my-page";
import { HomeHeader, HomeNavigation } from "./home-screen";
import homeStyles from "./home.module.css";
import styles from "./my-page.module.css";

function Chevron({ muted = false, last = false }: { muted?: boolean; last?: boolean }) {
  return <img src={`/pivot/my/chevron${muted ? "-muted" : ""}${last ? "-last" : ""}.svg`} alt="" width={16} height={24} />;
}
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
      : "시연용 화면이에요. 계정 삭제를 요청하거나 개인정보를 삭제하지 않아요. 실제 회원탈퇴 기능은 준비 중이에요."}</p>
    <div className={styles.dialogActions}>
      <button type="button" autoFocus onClick={onClose}>취소</button>
      <button type="button" onClick={onClose}>확인</button>
    </div>
  </dialog>;
}

export function PivotMyPageScreen({ state, demo, onHome, onRetry, onSaved }: {
  state: MyPageState; demo: boolean; onHome: () => void; onRetry: () => void; onSaved?: () => void;
}) {
  const screen = useRef<HTMLElement>(null);
  const [notice, setNotice] = useState("");
  const [notifications, setNotifications] = useState(true);
  const [nightNotifications, setNightNotifications] = useState(false);
  const [accountDialog, setAccountDialog] = useState<"로그아웃" | "회원탈퇴" | null>(null);
  useEffect(() => { screen.current?.querySelector<HTMLHeadingElement>("h1")?.focus({ preventScroll: true }); }, []);
  function pending(label: string) { setNotice(`${label} 기능은 준비 중이에요.`); }
  // Settings are demo-only local state. A future Service profile must not enable
  // these controls before the notification policy/write adapter are agreed.
  const profile = state.status === "ready" && state.profile.origin === (demo ? "demo" : "service") ? state.profile : null;
  return <main ref={screen} className={`${homeStyles.screen} ${styles.screen}`} data-my-state={state.status} data-my-mode={demo ? "demo" : "service"}
    aria-busy={state.status === "loading"}>
    <HomeHeader title="마이페이지" showMascot={false} />
    <div className={styles.content}>
      <p className={styles.intro}>내 정보와 설정을 관리해보세요</p>
      {profile ? <>
        <section className={`${styles.card} ${styles.profile}`} aria-label={demo ? "시연용 가상 프로필" : "프로필"}>
          <div><p className={styles.nickname}>{profile.nickname}님</p><p className={styles.email}>{profile.maskedEmail}</p></div>
          <button type="button" className={styles.edit} onClick={() => pending("프로필 수정")}><span>프로필 수정<Chevron /></span></button>
        </section>
        <MenuSection title="내 정보">
          {["내 활동", "관심분야 관리"].map((label, index) => <button key={label} type="button" className={styles.row} onClick={() => pending(label)}>{label}<Chevron last={index === 1} /></button>)}
        </MenuSection>
        <MenuSection title="알림 설정">
          <button type="button" role="switch" aria-checked={demo && notifications} aria-describedby="my-demo-help" aria-disabled={!demo || undefined}
            className={styles.row} onClick={() => demo ? setNotifications((value) => !value) : pending("알림 설정")}>
            알림 허용<img src={`/pivot/my/switch-${demo && notifications ? "on" : "off"}.svg`} alt="" width={33} height={18} />
          </button>
          <button type="button" role="switch" aria-checked={demo && nightNotifications} aria-describedby="my-demo-help" aria-disabled={!demo || undefined}
            className={styles.row} onClick={() => demo ? setNightNotifications((value) => !value) : pending("알림 설정")}>
            21시 이후 알림 허용<img src={`/pivot/my/switch-${demo && nightNotifications ? "on" : "off"}.svg`} alt="" width={33} height={18} />
          </button>
        </MenuSection>
        <MenuSection title="이용 안내">
          {["문의 및 신고", "이용약관", "개인정보처리방침"].map((label, index) => <button key={label} type="button" className={styles.row}
            onClick={() => setNotice(`${label}${index ? " 문서는" : " 기능은"} 준비 중이에요.`)}>{label}<Chevron last={index === 2} /></button>)}
        </MenuSection>
        <MenuSection>
          {(["로그아웃", "회원탈퇴"] as const).map((label, index) => <button key={label} type="button" className={`${styles.row} ${styles.account}`}
            onClick={() => demo ? setAccountDialog(label) : pending(label)}>{label}<Chevron muted last={index === 1} /></button>)}
        </MenuSection>
        <p id="my-demo-help" className={styles.help}>{demo
          ? "시연용 가상 프로필이에요. 알림 선택은 이 화면에서만 적용되며, 화면을 떠나거나 새로고침하면 초기화돼요."
          : "알림·프로필 변경과 계정 기능은 연결 준비 중이에요."}</p>
      </> : <section className={homeStyles.status} aria-label="My Page 연결 상태">
        <p role={state.status === "error" || state.status === "ready" ? "alert" : "status"}>{state.status === "loading" ? "프로필을 불러오고 있어요."
          : state.status === "ready" ? "프로필을 표시할 수 없어요." : state.message}</p>
        {state.status === "error" ? <button type="button" className={homeStyles.button} onClick={onRetry}>다시 시도</button> : null}
      </section>}
    </div>
    <HomeNavigation active="my" onHome={onHome} onSaved={onSaved} hideAI />
    {notice ? <p role="status" className={homeStyles.pendingNotice}>{notice}</p> : null}
    {accountDialog ? <DemoAccountDialog action={accountDialog} onClose={() => setAccountDialog(null)} /> : null}
  </main>;
}
