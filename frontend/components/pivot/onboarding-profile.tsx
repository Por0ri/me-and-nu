"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { validateOnboardingProfile, type OnboardingProfile } from "@/lib/pivot/onboarding-service";
import { PivotBrand, PivotFrame, PrimaryButton } from "./pivot-ui";
import styles from "./onboarding.module.css";

export function OnboardingProfileScreen({ profile, onChange, onContinue, onBack, message = "" }: {
  profile: OnboardingProfile; onChange: (profile: OnboardingProfile) => void;
  onContinue: (profile: OnboardingProfile) => void; onBack: () => void; message?: string;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const [touched, setTouched] = useState({ nickname: false, birthDate: false });
  const validation = validateOnboardingProfile(profile);
  const errors = validation.valid ? {} : validation.errors;
  useEffect(() => { window.scrollTo({ top: 0, behavior: "instant" }); heading.current?.focus({ preventScroll: true }); }, []);
  function next(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched({ nickname: true, birthDate: true });
    if (!validation.valid) {
      form.current?.querySelector<HTMLInputElement>(validation.errors.nickname ? "#onboarding-nickname" : "#onboarding-birthdate")?.focus();
      return;
    }
    onContinue(validation.profile);
  }
  return <PivotFrame decorated={false} className={styles.frame}>
    <div className={styles.profileHeader}><button type="button" className={styles.profileBack} onClick={onBack}>약관으로 돌아가기</button><PivotBrand screen="consent" /></div>
    <form ref={form} className={styles.profileForm} onSubmit={next} noValidate>
      <section className={styles.profileBody} aria-labelledby="profile-heading">
        <h1 id="profile-heading" ref={heading} tabIndex={-1}>어떻게 불러드릴까요?</h1>
        <p>가입에 필요한 기본 정보를 알려주세요.</p>
        <div className={styles.profileField}>
          <label htmlFor="onboarding-nickname">닉네임</label>
          <input id="onboarding-nickname" name="nickname" autoComplete="nickname" value={profile.nickname} required
            onChange={(event) => onChange({ ...profile, nickname: event.target.value })} onBlur={() => setTouched((old) => ({ ...old, nickname: true }))}
            aria-invalid={touched.nickname && !!errors.nickname} aria-describedby="nickname-hint nickname-error" />
          <p id="nickname-hint">1~30자로 입력해주세요.</p>
          <p id="nickname-error" className={styles.fieldError} role="alert">{touched.nickname ? errors.nickname : ""}</p>
        </div>
        <div className={styles.profileField}>
          <label htmlFor="onboarding-birthdate">생년월일</label>
          <input id="onboarding-birthdate" name="birthDate" type="date" autoComplete="bday" value={profile.birthDate} required
            onChange={(event) => onChange({ ...profile, birthDate: event.target.value })} onBlur={() => setTouched((old) => ({ ...old, birthDate: true }))}
            aria-invalid={touched.birthDate && !!errors.birthDate} aria-describedby="birthdate-hint birthdate-error" />
          <p id="birthdate-hint">만 14세 이상 가입할 수 있어요. 정확한 생년월일을 입력해주세요.</p>
          <p id="birthdate-error" className={styles.fieldError} role="alert">{touched.birthDate ? errors.birthDate : ""}</p>
        </div>
        {message && <p className={styles.fieldError} role="alert">{message}</p>}
      </section>
      <div className={styles.actions}><PrimaryButton type="submit">취향 선택하기</PrimaryButton></div>
    </form>
  </PivotFrame>;
}
