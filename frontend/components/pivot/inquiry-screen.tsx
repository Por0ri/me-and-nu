"use client";

import { useEffect, useRef } from "react";
import { INQUIRY_LIMIT, inquiryLength, inquiryTypes, inquiryValidation, type InquiryDraft, type InquiryState } from "@/lib/pivot/inquiry";
import { HomeHeader, HomeNavigation } from "./home-screen";
import homeStyles from "./home.module.css";
import styles from "./inquiry.module.css";

export function InquiryScreen({ state, demo, available, onChange, onSubmit, onMy, onHome, onSaved }: {
  state: InquiryState; demo: boolean; available: boolean; onChange: (draft: Partial<InquiryDraft>) => void;
  onSubmit: () => void; onMy: () => void; onHome: () => void; onSaved: () => void;
}) {
  const screen = useRef<HTMLElement>(null);
  useEffect(() => { screen.current?.querySelector("h1")?.focus({ preventScroll: true }); }, []);
  const busy = state.status === "submitting";
  const locked = busy || state.status === "received" || state.status === "unavailable";
  const length = inquiryLength(state.detail);
  return <main ref={screen} className={`${homeStyles.screen} ${styles.screen}`} data-inquiry-state={state.status} data-inquiry-mode={demo ? "demo" : "service"}>
    <HomeHeader title="문의 및 신고" showMascot={false} onBack={onMy} />
    <form className={styles.form} onSubmit={event => { event.preventDefault(); onSubmit(); }} aria-busy={busy}>
      <h2 className={styles.intro}>문의 유형을 선택하고 세부 내용을 작성해주세요</h2>
      <div className={styles.card}>
        <fieldset className={styles.types} disabled={locked || !available}>
          <legend className={homeStyles.srOnly}>유형 선택 (필수)</legend>
          <p className={styles.label} aria-hidden="true">유형 선택<span>필수</span></p>
          <div className={styles.options}>
            {inquiryTypes.map(type => <label key={type.value} className={styles.option}>
              <input type="radio" name="inquiryType" value={type.value} checked={state.inquiryType === type.value} required onChange={() => onChange({ inquiryType: type.value })} />
              <span className={styles.optionBody}>{type.label}{state.inquiryType === type.value ? <img src="/pivot/inquiry/check.svg" alt="" width={18} height={18} /> : null}</span>
            </label>)}
          </div>
        </fieldset>
        <div className={styles.details}>
          <label className={styles.label} htmlFor="inquiry-detail">세부사항<span aria-hidden="true">필수</span></label>
          <div className={styles.textbox}>
            <textarea id="inquiry-detail" name="detail" required value={state.detail} disabled={locked || !available}
              placeholder="문의하실 내용을 자세히 입력해주세요" aria-describedby="inquiry-count inquiry-help inquiry-feedback"
              aria-invalid={length > INQUIRY_LIMIT || undefined} onChange={event => onChange({ detail: event.target.value })} />
            <span id="inquiry-count" className={styles.count} data-invalid={length > INQUIRY_LIMIT || undefined}>{length}/{INQUIRY_LIMIT}</span>
          </div>
          <p id="inquiry-help" className={styles.help}>개인정보는 입력하지 않도록 주의해주세요</p>
        </div>
      </div>
      <div id="inquiry-feedback" className={styles.feedback}>
        {length > INQUIRY_LIMIT ? <p role="alert">세부 내용은 500자까지 입력할 수 있어요.</p> : null}
        {demo ? <p role="status">Demo에서는 실제 문의를 접수하지 않아요. 로그인 후 이용해주세요.</p> : null}
        {state.message ? <p role={state.status === "received" ? "status" : "alert"}>{state.message}</p> : null}
      </div>
      <button className={styles.submit} type="submit" disabled={demo || !available || locked || Boolean(inquiryValidation(state))}>
        {busy ? "접수 중…" : state.status === "error" ? "다시 시도" : "접수하기"}
      </button>
    </form>
    <HomeNavigation active="my" onHome={onHome} onMy={onMy} onSaved={onSaved} hideAI />
  </main>;
}
