"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { NICKNAME_MAX, NICKNAME_TAKEN_MESSAGE, nicknameProblem, nicknameRuleBreaks, type NicknameCheck, type OnboardingProfile } from "@/lib/pivot/onboarding-service";
import { PivotBrand, PivotFrame, PrimaryButton } from "./pivot-ui";
import styles from "./onboarding.module.css";

export function OnboardingProfileScreen({ profile, onChange, onContinue, message = "", checkNickname }: {
  profile: OnboardingProfile; onChange: (profile: OnboardingProfile) => void;
  onContinue: (profile: OnboardingProfile) => void; message?: string;
  /** Duplicate check 0.3s after typing stops; without it, or when it fails, nothing is blocked. */
  checkNickname?: (nickname: string, signal: AbortSignal) => Promise<NicknameCheck>;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const typed = typeof profile?.nickname === "string" ? profile.nickname : "";
  // What is typed is checked as it is (no trimming), so a space at either end is marked too.
  const breaks = nicknameRuleBreaks(typed);
  const valid = !nicknameProblem(typed);
  const [checked, setChecked] = useState<{ nickname: string; taken: boolean } | null>(null);
  useEffect(() => {
    if (!valid || !checkNickname) return;
    const abort = new AbortController();
    const timer = setTimeout(() => {
      checkNickname(typed, abort.signal)
        .then(result => { if (!abort.signal.aborted) setChecked({ nickname: typed, taken: !result.available && result.reason === "taken" }); })
        // Only a convenience (the backend may not have it yet): a failed check never blocks; completion checks again.
        .catch(() => { if (!abort.signal.aborted) setChecked({ nickname: typed, taken: false }); });
    }, 300);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [typed, valid, checkNickname]);
  const answered = !checkNickname || checked?.nickname === typed;
  const taken = checked?.nickname === typed && checked.taken;
  const ready = valid && answered && !taken;
  const error = taken ? NICKNAME_TAKEN_MESSAGE : message;
  useEffect(() => { window.scrollTo({ top: 0, behavior: "instant" }); heading.current?.focus({ preventScroll: true }); }, []);
  function next(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (ready) onContinue({ nickname: typed });
  }
  return <PivotFrame decorated={false} className={styles.frame}>
    <div className={styles.profileHeader}><PivotBrand screen="consent" className={styles.profileBrand} /></div>
    <form className={styles.profileForm} onSubmit={next} noValidate>
      <section className={styles.profileBody} aria-labelledby="profile-heading">
        <h1 id="profile-heading" ref={heading} tabIndex={-1}>어떻게 불러드릴까요?</h1>
        <p>가입에 필요한 기본 정보를 알려주세요</p>
        <div className={styles.profileField}>
          <label htmlFor="onboarding-nickname">닉네임</label>
          <div className={styles.profileInput}>
            {/* No hard cap at 20, so going over shows up as a red "2~20자" instead of keys silently doing nothing. */}
            <input id="onboarding-nickname" name="nickname" autoComplete="nickname" value={typed} required
              onChange={(event) => onChange({ nickname: event.target.value })}
              aria-invalid={breaks.length || breaks.characters || !!error} aria-describedby="nickname-hint nickname-error" />
            <span className={styles.profileCount} aria-hidden="true">{`${[...typed].length}/${NICKNAME_MAX}`}</span>
          </div>
          <p id="nickname-hint"><span className={breaks.length ? styles.profileRuleBroken : undefined}>2~20자</span>, <span className={breaks.characters ? styles.profileRuleBroken : undefined}>공백·특수문자 제외</span></p>
          <p id="nickname-error" className={styles.fieldError} role="alert">{error}</p>
        </div>
      </section>
      <div className={styles.actions}><PrimaryButton type="submit" disabled={!ready}>시작하기</PrimaryButton></div>
    </form>
  </PivotFrame>;
}
