"use client";

import { useEffect, useRef, useState } from "react";
import type { Policy } from "@/lib/api";
import { policyKey, policyLabel, type ConsentDraft } from "@/lib/pivot/consent";
import { previewPolicies } from "@/lib/pivot/preview";
import { PivotAsset, PivotBrand, PivotFrame, PrimaryButton } from "./pivot-ui";
import { useConsent } from "./use-consent";
import styles from "./pivot.module.css";

function ConsentCheckbox({ label, checked, partial = false, onChange, all = false }: {
  label: string; checked: boolean; partial?: boolean; onChange: (agreed: boolean) => void; all?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (input.current) input.current.indeterminate = partial; }, [partial]);
  return <label className={`${styles.checkboxLabel} ${all ? styles.allLabel : ""}`}>
    <span className={styles.checkboxSlot}>
      <input ref={input} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <PivotAsset screen={checked ? "accepted" : "consent"} name={checked ? "imgStateOn" : "imgStateOff"} width={23} height={23} />
      {partial && <span className={styles.partial} aria-hidden="true" />}
    </span><span>{label}</span>
  </label>;
}
function PolicyDialog({ policy, onClose }: { policy: Policy; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className={styles.dialog} aria-labelledby="policy-title" onClose={onClose}>
    <h2 id="policy-title">{policyLabel(policy)}</h2>
    <p className={styles.policyVersion}>버전 {policy.policyVersion} · {policy.required ? "필수" : "선택"}</p>
    <div className={styles.policyText}>{policy.text}</div>
    <PrimaryButton autoFocus onClick={() => dialog.current?.close()}>닫기</PrimaryButton>
  </dialog>;
}
type ConsentScreenProps = { initialDraft?: ConsentDraft; preview?: boolean; onContinue?: (draft: ConsentDraft) => void };
type ConsentModel = ReturnType<typeof useConsent>;

export function ConsentScreen({ model, ...props }: ConsentScreenProps & { model?: ConsentModel }) {
  return model ? <ConsentView {...props} model={model} /> : <ConsentController {...props} />;
}
function ConsentController(props: ConsentScreenProps) {
  const model = useConsent(props.preview ? previewPolicies : undefined, props.initialDraft);
  return <ConsentView {...props} model={model} />;
}
function ConsentView({ model, preview = false, onContinue }: ConsentScreenProps & { model: ConsentModel }) {
  const [opened, setOpened] = useState<Policy | null>(null);
  const [continuing, setContinuing] = useState(false);
  const continueLock = useRef(false);
  const continueTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (continueTimer.current) clearTimeout(continueTimer.current); }, []);
  function continueOnce() {
    if (continueLock.current || model.status !== "ready" || !model.summary.canContinue) return;
    const draft = model.continueDraft();
    continueLock.current = true;
    setContinuing(true);
    continueTimer.current = setTimeout(() => { continueLock.current = false; setContinuing(false); }, 200);
    onContinue?.(draft);
  }
  return <PivotFrame chrome={preview}>
    <PivotBrand screen="consent" className={styles.consentBrand} />
    <section className={styles.consentBody} aria-labelledby="consent-heading">
      <div className={styles.consentIntro}>
        <h1 id="consent-heading" tabIndex={-1}>시작하기 전에 확인해주세요</h1>
        <p>서비스 이용에 필요한 약관 동의 단계예요</p>
      </div>
      {model.status === "ready" ? <fieldset className={styles.agreement}>
        <legend className={styles.srOnly}>서비스 이용 약관 동의</legend>
        <div className={styles.allRow}><ConsentCheckbox all label="모두 동의합니다" checked={model.summary.all} partial={model.summary.partial} onChange={model.toggleAll} /></div>
        {model.policies.map((policy) => <div className={styles.policyRow} key={policyKey(policy)}>
          <ConsentCheckbox label={policyLabel(policy)} checked={model.selection[policyKey(policy)] === true} onChange={(agreed) => model.toggle(policy, agreed)} />
          <span className={styles.requirement}>{policy.required ? "필수" : "선택"}</span>
          <button type="button" className={styles.details} aria-label={`${policyLabel(policy)} 내용 보기`} onClick={() => setOpened(policy)}><PivotAsset screen="consent" name="imgChevronForward" width={24} height={24} /></button>
        </div>)}
      </fieldset> : <div className={styles.loadState} role={model.status === "error" ? "alert" : "status"}>
        <p>{model.status === "loading" ? "약관을 불러오고 있어요." : model.error}</p>
        {model.status === "error" && <button type="button" className={styles.textButton} onClick={model.reload}>약관 다시 불러오기</button>}
      </div>}
    </section>
    <div className={styles.consentActions}>
      <PrimaryButton disabled={continuing || model.status !== "ready" || !model.summary.canContinue} onClick={continueOnce} aria-describedby="consent-feedback">시작하기</PrimaryButton>
      <p id="consent-feedback" className={styles.consentFeedback} role="status" aria-live="polite">
        {model.reachedBoundary ? preview
          ? "미리보기 선택을 확인했어요. 가입이나 서버 저장은 진행되지 않아요."
          : "약관 선택을 확인했어요. 관심 분야 선택으로 이동해요." : ""}
      </p>
    </div>
    {opened && <PolicyDialog policy={opened} onClose={() => setOpened(null)} />}
  </PivotFrame>;
}
