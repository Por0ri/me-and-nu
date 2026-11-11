"use client";

import { useEffect, useRef, type FormEvent } from "react";
import { NICKNAME_TAKEN_MESSAGE, type OnboardingProfile } from "@/lib/pivot/onboarding-service";
import { NicknameField, useNicknameStatus, type NicknameCheckRequest } from "./nickname-field";
import { PivotBrand, PivotFrame, PrimaryButton } from "./pivot-ui";
import styles from "./onboarding.module.css";

export function OnboardingProfileScreen({ profile, onChange, onContinue, message = "", checkNickname }: {
  profile: OnboardingProfile; onChange: (profile: OnboardingProfile) => void;
  onContinue: (profile: OnboardingProfile) => void; message?: string;
  /** Duplicate check 0.3s after typing stops; without it, or when it fails, nothing is blocked. */
  checkNickname?: NicknameCheckRequest;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const typed = typeof profile?.nickname === "string" ? profile.nickname : "";
  // What is typed is checked as it is (no trimming), so a space at either end is marked too.
  const status = useNicknameStatus(typed, checkNickname);
  const error = status.taken ? NICKNAME_TAKEN_MESSAGE : message;
  useEffect(() => { window.scrollTo({ top: 0, behavior: "instant" }); heading.current?.focus({ preventScroll: true }); }, []);
  function next(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.ready) onContinue({ nickname: typed });
  }
  return <PivotFrame decorated={false} className={styles.frame}>
    <div className={styles.profileHeader}><PivotBrand screen="consent" className={styles.profileBrand} /></div>
    <form className={styles.profileForm} onSubmit={next} noValidate>
      <section className={styles.profileBody} aria-labelledby="profile-heading">
        <h1 id="profile-heading" ref={heading} tabIndex={-1}>어떻게 불러드릴까요?</h1>
        <p>가입에 필요한 기본 정보를 알려주세요</p>
        <div className={styles.profileField}>
          <NicknameField id="onboarding-nickname" value={typed} onChange={(nickname) => onChange({ nickname })} breaks={status.breaks} error={error} />
        </div>
      </section>
      <div className={styles.actions}><PrimaryButton type="submit" disabled={!status.ready}>시작하기</PrimaryButton></div>
    </form>
  </PivotFrame>;
}
