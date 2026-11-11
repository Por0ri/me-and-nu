"use client";

import { useEffect, useRef, useState } from "react";
import {
  createNotificationSettingsController, initialNotificationSettingsState, pendingNotificationSettingsSource,
  type NotificationPreferenceKey, type NotificationPreferences, type NotificationSettingsSource, type NotificationSettingsState,
} from "@/lib/pivot/notification-settings";
import styles from "./my-page.module.css";

const labels: [NotificationPreferenceKey, string][] = [["allowed", "알림 허용"], ["after21Allowed", "21시 이후 알림 허용"]];
function SettingsRows({ values, disabled, description, onChange }: {
  values: NotificationPreferences | null; disabled: boolean; description: string;
  onChange: (key: NotificationPreferenceKey, value: boolean) => void;
}) {
  return <div className={styles.rows}>{labels.map(([key, label]) => {
    const checked = values?.[key] ?? false;
    return <button key={key} type="button" role="switch" aria-checked={checked} aria-describedby={description}
      aria-disabled={disabled || undefined} className={`${styles.row} ${styles.notificationSwitch}`}
      onClick={() => { if (!disabled) onChange(key, !checked); }}>
      {label}<img src={`/pivot/my/switch-${checked ? "on" : "off"}.svg`} alt="" width={33} height={18} />
    </button>;
  })}</div>;
}

function DemoSettings() {
  // Figma sample values only. Neither persisted nor interpreted as Service defaults.
  const [values, setValues] = useState<NotificationPreferences>({ allowed: true, after21Allowed: false });
  return <SettingsRows values={values} disabled={false} description="my-demo-help"
    onChange={(key, value) => setValues(previous => ({ ...previous, [key]: value }))} />;
}

export function NotificationSettingsView({ state, onChange, onRetry }: {
  state: NotificationSettingsState; onChange: (key: NotificationPreferenceKey, value: boolean) => void; onRetry: () => void;
}) {
  const busy = state.status === "loading" || state.status === "saving";
  const message = state.status === "unavailable" ? "알림 설정은 준비 중이에요. 현재 설정값을 확인하거나 변경할 수 없어요."
    : state.status === "loading" ? "알림 설정을 불러오고 있어요."
    : state.status === "saving" ? "알림 설정을 저장하고 있어요."
    : state.status === "expired" ? "로그인 상태가 변경됐어요. 로그인 상태를 확인해주세요."
    : state.status === "error" ? state.reason === "conflict" ? "다른 화면에서 설정이 변경됐어요. 현재 설정을 다시 불러와주세요." : state.operation === "save"
      ? "저장 결과를 확인하지 못했어요. 현재 설정을 다시 불러와주세요."
      : "알림 설정을 불러오지 못했어요. 다시 시도해주세요."
    : state.status === "ready" && state.saved ? "알림 설정을 저장했어요." : "알림 설정을 변경할 수 있어요.";
  return <>
    <div aria-busy={busy || undefined}><SettingsRows values={state.values} disabled={state.status !== "ready"}
      description="my-notification-status" onChange={onChange} /></div>
    <p id="my-notification-status" role={state.status === "error" ? "alert" : "status"}
      className={state.status === "ready" && !state.saved ? styles.notificationReady : styles.notificationStatus}>{message}</p>
    <p className={styles.notificationStatus}>선택적 알림 수신 제어는 준비 중이에요. 필수 운영 공지는 계속 확인할 수 있어요.</p>
    {state.status === "error" ? <button type="button" className={styles.notificationRetry} onClick={onRetry}>설정 다시 불러오기</button> : null}
  </>;
}

function ServiceSettings({ source, scope }: { source: NotificationSettingsSource; scope?: object }) {
  const [result, setResult] = useState<{ source: NotificationSettingsSource; scope?: object; state: NotificationSettingsState }>();
  const active = useRef<{ source: NotificationSettingsSource; scope?: object; controller: ReturnType<typeof createNotificationSettingsController> } | null>(null);
  useEffect(() => {
    const controller = createNotificationSettingsController(source, state => setResult({ source, scope, state }));
    const entry = { source, scope, controller }; active.current = entry;
    void controller.load();
    return () => { controller.cancel(); if (active.current === entry) active.current = null; };
  }, [source, scope]);
  // Hide a previous session's values during render, before effect cleanup runs.
  const state = result?.source === source && result.scope === scope ? result.state : initialNotificationSettingsState(source);
  const controller = () => active.current?.source === source && active.current.scope === scope ? active.current.controller : null;
  return <NotificationSettingsView state={state}
    onChange={(key, value) => { void controller()?.change(key, value); }} onRetry={() => { void controller()?.load(); }} />;
}

export function MyNotificationSettings({ demo, scope, source = pendingNotificationSettingsSource }: {
  demo: boolean; scope?: object; source?: NotificationSettingsSource;
}) {
  return <section className={styles.card} aria-label="알림 설정">
    <h2 className={styles.sectionTitle}>알림 설정</h2>
    {demo ? <DemoSettings /> : <ServiceSettings source={source} scope={scope} />}
  </section>;
}
