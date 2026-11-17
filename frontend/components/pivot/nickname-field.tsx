"use client";
// The nickname box shared by onboarding and the My Page profile edit (2026-11 design), so both keep one rule:
// 2~20 Korean (jamo too), English or digit characters, the broken part of the rule in red, and the duplicate check.
// Place it inside the onboarding .profileField card, whose rules style the label, input and lines.
import { useEffect, useState } from "react";
import { NICKNAME_MAX, nicknameProblem, nicknameRuleBreaks, type NicknameCheck } from "@/lib/pivot/onboarding-service";
import styles from "./onboarding.module.css";

export type NicknameCheckRequest = (nickname: string, signal: AbortSignal) => Promise<NicknameCheck>;

/** Whether the typed nickname can be used: the rule, then the duplicate check 0.3 s after typing stops. Without a
 * check, or when it fails (the backend may not have it yet), nothing is blocked; saving checks again. `own` (the
 * member's current nickname) is not asked about. */
export function useNicknameStatus(typed: string, check?: NicknameCheckRequest, own?: string) {
  const valid = !nicknameProblem(typed);
  const skip = !check || typed === own;
  const [checked, setChecked] = useState<{ nickname: string; taken: boolean } | null>(null);
  useEffect(() => {
    if (!valid || skip) return;
    const abort = new AbortController();
    const timer = setTimeout(() => {
      check!(typed, abort.signal)
        .then(result => { if (!abort.signal.aborted) setChecked({ nickname: typed, taken: !result.available && result.reason === "taken" }); })
        .catch(() => { if (!abort.signal.aborted) setChecked({ nickname: typed, taken: false }); });
    }, 300);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [typed, valid, skip, check]);
  const taken = !skip && checked?.nickname === typed && checked.taken;
  return { valid, taken, ready: valid && (skip || checked?.nickname === typed) && !taken, breaks: nicknameRuleBreaks(typed) };
}

/** Label, input with n/20 (no hard cap, so going over shows a red "2~20자" instead of keys silently doing nothing),
 * the rule with its broken part in red, and the error line. */
export function NicknameField({ id, value, onChange, breaks, error }: {
  id: string; value: string; onChange: (value: string) => void; breaks: { length: boolean; characters: boolean }; error: string;
}) {
  return <>
    <label htmlFor={id}>닉네임</label>
    <div className={styles.profileInput}>
      <input id={id} name="nickname" autoComplete="nickname" value={value} required onChange={(event) => onChange(event.target.value)}
        aria-invalid={breaks.length || breaks.characters || !!error} aria-describedby="nickname-hint nickname-error" />
      <span className={styles.profileCount} aria-hidden="true">{`${[...value].length}/${NICKNAME_MAX}`}</span>
    </div>
    <p id="nickname-hint"><span className={breaks.length ? styles.profileRuleBroken : undefined}>2~20자</span>, <span className={breaks.characters ? styles.profileRuleBroken : undefined}>공백·특수문자 제외</span></p>
    <p id="nickname-error" className={styles.fieldError} role="alert">{error}</p>
  </>;
}
