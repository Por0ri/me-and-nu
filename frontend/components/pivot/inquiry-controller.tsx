"use client";

import { useEffect, useState } from "react";
import { createInquiryController, initialInquiryState, type InquirySource, type InquiryState } from "@/lib/pivot/inquiry";
import { InquiryScreen } from "./inquiry-screen";
import { createInquiryHistoryController, initialInquiryHistory, type InquiryHistoryState } from "@/lib/pivot/inquiry-history";
import { InquiryHistoryPanel } from "./inquiry-history-panel";
import styles from "./inquiry.module.css";
import { API_UNAUTHORIZED_EVENT } from "@/lib/api";

export function InquiryController({ source, demo, onMy, onHome, onSaved, onNotifications }: {
  source?: InquirySource; demo: boolean; onMy: () => void; onHome: () => void; onSaved: () => void; onNotifications?: () => void;
}) {
  const [request, setRequest] = useState<{
    source?: InquirySource; demo: boolean; state: InquiryState; control: ReturnType<typeof createInquiryController>;
  }>();
  const [view, setView] = useState<"compose" | "history">("compose");
  const [history, setHistory] = useState<{ source?: InquirySource; state: InquiryHistoryState;
    control: ReturnType<typeof createInquiryHistoryController> }>();
  useEffect(() => {
    const control = createInquiryController(source, demo, state => setRequest({ source, demo, control, state }));
    setRequest({ source, demo, control, state: initialInquiryState() });
    return () => control.cancel();
  }, [source, demo]);
  useEffect(() => {
    const control = createInquiryHistoryController(demo ? undefined : source?.history,
      state => setHistory({ source, state, control }));
    setHistory({ source, state: initialInquiryHistory(), control });
    setView("compose");
    return () => control.cancel();
  }, [source, demo]);
  const current = request?.source === source && request?.demo === demo ? request : undefined;
  const currentHistory = history?.source === source ? history : undefined;
  useEffect(() => {
    const control = currentHistory?.control;
    if (!control) return;
    const unauthorized = () => control.invalidate();
    const refresh = () => { if (view === "history" && document.visibilityState === "visible") void control.load(currentHistory.state.page); };
    window.addEventListener(API_UNAUTHORIZED_EVENT, unauthorized);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener(API_UNAUTHORIZED_EVENT, unauthorized);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [currentHistory?.control, currentHistory?.state.page, view]);
  function showHistory() { setView("history"); void currentHistory?.control.load(1); }
  return <InquiryScreen state={current?.state ?? initialInquiryState()} demo={demo}
    available={Boolean(current && (demo || source))} onChange={draft => current?.control.change(draft)}
    onSubmit={() => { void current?.control.submit(); }} onMy={onMy} onHome={onHome} onSaved={onSaved} onNotifications={onNotifications}
    onReceived={() => { current?.control.resetReceived(); showHistory(); }} historyVisible={view === "history"}
    tabs={<nav className={styles.historyTabs} aria-label="문의 화면">
      <button type="button" aria-pressed={view === "compose"} disabled={current?.state.status === "submitting"} onClick={() => setView("compose")}>문의 작성</button>
      <button type="button" aria-pressed={view === "history"} disabled={current?.state.status === "submitting"} onClick={showHistory}>문의 내역 · 답변</button>
    </nav>}
    history={<InquiryHistoryPanel state={currentHistory?.state ?? initialInquiryHistory()}
      onLoad={page => { void currentHistory?.control.load(page); }} onSelect={id => { void currentHistory?.control.select(id); }} />} />;
}
